from __future__ import annotations

from pathlib import Path
import threading
import time
from typing import Any

from flask import Flask, Response, jsonify, request, send_from_directory

from prefetcher import (
    PrefetchRunner,
    compute_next_run,
    extract_catalogs,
    fetch_json,
    normalize_addon_base,
)
from storage import StateStore, utc_now_iso


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
STATE_FILE = DATA_DIR / "state.json"

store = StateStore(STATE_FILE)
runner = PrefetchRunner(store)

app = Flask(__name__)


def json_success(**payload: Any) -> Response:
    return jsonify({"success": True, **payload})


def json_error(message: str, status: int = 400, **payload: Any) -> tuple[Response, int]:
    return jsonify({"success": False, "error": message, **payload}), status


def catalog_identity(catalog: dict[str, Any]) -> str:
    return catalog.get("key") or f"{catalog.get('addon_base_url')}|{catalog.get('type')}|{catalog.get('id')}"


def merge_selection(loaded: list[dict[str, Any]], existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lookup = {catalog_identity(item): item for item in existing}
    merged: list[dict[str, Any]] = []
    for catalog in loaded:
        selected = deepcopy_catalog(catalog)
        existing_item = lookup.get(catalog_identity(catalog))
        if existing_item:
            selected["selected"] = bool(existing_item.get("selected", True))
            selected["order"] = existing_item.get("order", selected.get("order", 0))
        merged.append(selected)
    merged.sort(key=lambda item: int(item.get("order", 0)))
    return merged


def deepcopy_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": catalog.get("key"),
        "id": catalog.get("id"),
        "type": catalog.get("type"),
        "name": catalog.get("name"),
        "pageSize": catalog.get("pageSize"),
        "extra": catalog.get("extra", []),
        "source": catalog.get("source"),
        "addon_base_url": catalog.get("addon_base_url"),
        "manifest_url": catalog.get("manifest_url"),
        "order": catalog.get("order", 0),
        "selected": bool(catalog.get("selected", True)),
    }


@app.route("/")
def index() -> Response:
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/styles.css")
def styles() -> Response:
    return send_from_directory(BASE_DIR, "styles.css")


@app.route("/app.js")
def js() -> Response:
    return send_from_directory(BASE_DIR, "app.js")


@app.route("/README.md")
def readme() -> Response:
    return send_from_directory(BASE_DIR, "README.md")


@app.route("/api/status", methods=["GET"])
def api_status():
    state = store.snapshot()
    state["runtime"]["next_run"] = compute_next_run(state["config"])
    return json_success(
        timestamp=utc_now_iso(),
        config=state["config"],
        catalogs=state["catalogs"],
        runtime=state["runtime"],
        logs=state["logs"][-20:],
    )


@app.route("/api/addon/manifest", methods=["POST"])
def api_manifest():
    data = request.get_json(silent=True) or {}
    raw_url = (data.get("url") or request.args.get("url") or "").strip()
    if not raw_url:
        return json_error("URL du manifest manquante")

    try:
        base_url = normalize_addon_base(raw_url)
        manifest = fetch_json(f"{base_url}/manifest.json")
    except Exception as exc:
        store.add_log("error", "Manifest inaccessible", url=raw_url, error=str(exc))
        return json_error(f"Impossible de charger le manifest: {exc}", 502)

    addon_name = manifest.get("name") or "Addon"
    addon_version = manifest.get("version") or ""
    catalogs = extract_catalogs(manifest, base_url, addon_name)

    return json_success(
        addon={
            "name": addon_name,
            "version": addon_version,
            "url": base_url,
            "catalog_count": len(catalogs),
            "catalogs": catalogs,
        }
    )


@app.route("/api/catalogs/load", methods=["POST"])
def api_catalogs_load():
    data = request.get_json(silent=True) or {}
    catalog_url = (data.get("catalog_manifest_url") or "").strip()
    stream_url = (data.get("stream_manifest_url") or "").strip()

    if not catalog_url:
        return json_error("L'URL du manifest catalogue est obligatoire")

    try:
        catalog_base = normalize_addon_base(catalog_url)
        catalog_manifest = fetch_json(f"{catalog_base}/manifest.json")
        loaded_catalogs = extract_catalogs(catalog_manifest, catalog_base, catalog_manifest.get("name") or "Catalog addon")
    except Exception as exc:
        store.add_log("error", "Chargement des catalogues échoué", error=str(exc), url=catalog_url)
        return json_error(f"Échec du chargement des catalogues: {exc}", 502)

    stream_base = ""
    if stream_url:
        try:
            stream_base = normalize_addon_base(stream_url)
            fetch_json(f"{stream_base}/manifest.json")
        except Exception as exc:
            store.add_log("warning", "Manifest stream invalide", error=str(exc), url=stream_url)
            return json_error(f"Le manifest stream est invalide: {exc}", 502)

    existing_loaded = store.get("catalogs", {}).get("loaded", [])
    selected_catalogs = merge_selection(loaded_catalogs, existing_loaded)

    store.update_config(
        catalog_manifest_url=catalog_base,
        stream_manifest_url=stream_base,
    )
    store.set_loaded_catalogs(selected_catalogs)
    store.set_selected_catalogs([catalog for catalog in selected_catalogs if catalog.get("selected", True)])
    store.update_runtime(
        message=f"{len(loaded_catalogs)} catalogues chargés",
        next_run=compute_next_run(store.get("config")),
    )
    store.add_log("info", "Catalogues chargés", count=len(loaded_catalogs))

    return json_success(
        addon={
            "catalog_count": len(loaded_catalogs),
            "catalogs": selected_catalogs,
            "stream_manifest_url": stream_base,
        }
    )


@app.route("/api/catalogs/selection", methods=["GET"])
def api_catalogs_selection():
    state = store.snapshot()
    return json_success(
        catalogs=state["catalogs"].get("selected", []),
        loaded=state["catalogs"].get("loaded", []),
    )


@app.route("/api/catalogs/selection", methods=["POST"])
def api_catalogs_selection_save():
    data = request.get_json(silent=True) or {}
    catalogs = data.get("catalogs")
    if not isinstance(catalogs, list):
        return json_error("La liste des catalogues est manquante")

    normalized: list[dict[str, Any]] = []
    for idx, catalog in enumerate(catalogs):
        if not isinstance(catalog, dict):
            continue
        normalized.append(
            {
                "key": catalog.get("key"),
                "id": catalog.get("id"),
                "type": catalog.get("type"),
                "name": catalog.get("name"),
                "pageSize": int(catalog.get("pageSize") or 100),
                "extra": catalog.get("extra", []),
                "source": catalog.get("source"),
                "addon_base_url": catalog.get("addon_base_url"),
                "manifest_url": catalog.get("manifest_url"),
                "order": int(catalog.get("order", idx)),
                "selected": bool(catalog.get("selected", True)),
            }
        )

    store.set_loaded_catalogs(normalized)
    store.set_selected_catalogs([catalog for catalog in normalized if catalog.get("selected", True)])
    store.add_log("info", "Sélection des catalogues enregistrée", count=len(normalized))
    return json_success(catalogs=normalized)


@app.route("/api/schedule", methods=["GET"])
def api_schedule_get():
    state = store.snapshot()
    return json_success(
        schedule={
            **state["config"],
            "next_run": compute_next_run(state["config"]),
        }
    )


@app.route("/api/schedule", methods=["POST"])
def api_schedule_post():
    data = request.get_json(silent=True) or {}
    run_time = str(data.get("run_time") or data.get("time") or "03:00")
    days = data.get("days")
    enabled = bool(data.get("enabled", True))

    if not isinstance(days, list) or not days:
        return json_error("Au moins un jour doit être sélectionné")

    try:
        hour, minute = (int(part) for part in run_time.split(":", 1))
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            raise ValueError
    except Exception:
        return json_error("L'heure doit être au format HH:MM")

    config = store.update_config(
        schedule_enabled=enabled,
        run_time=f"{hour:02d}:{minute:02d}",
        days=[int(day) for day in days],
        limit=max(1, int(data.get("limit") or 500)),
        include_movies=bool(data.get("include_movies", True)),
        include_series=bool(data.get("include_series", True)),
        refresh_expired=bool(data.get("refresh_expired", True)),
    )
    store.update_runtime(next_run=compute_next_run(config), message="Planification mise à jour")
    store.add_log("info", "Planification enregistrée", run_time=config["run_time"], days=config["days"])

    return json_success(schedule={**config, "next_run": compute_next_run(config)})


@app.route("/api/schedule", methods=["DELETE"])
def api_schedule_delete():
    config = store.update_config(schedule_enabled=False)
    store.update_runtime(next_run=None, message="Planification désactivée")
    store.add_log("warning", "Planification désactivée")
    return json_success(schedule={**config, "next_run": None})


@app.route("/api/run", methods=["POST"])
def api_run():
    success, message = runner.start_manual_job()
    if not success:
        return json_error(message, 409)
    return json_success(message=message)


@app.route("/api/logs", methods=["GET"])
def api_logs():
    limit = max(1, min(int(request.args.get("limit", 50)), 200))
    logs = store.snapshot()["logs"][-limit:]
    return json_success(logs=logs)


@app.route("/api/health", methods=["GET"])
def api_health():
    return json_success(ok=True, timestamp=utc_now_iso())


def schedule_loop() -> None:
    while True:
        try:
            runner.maybe_run_scheduled_job()
        except Exception as exc:  # pragma: no cover - safety net
            store.add_log("error", "Erreur de scheduler", error=str(exc))
        time.sleep(20)


def startup() -> None:
    state = store.snapshot()
    store.update_runtime(next_run=compute_next_run(state["config"]))
    if not any(log.get("message") == "Application démarrée" for log in state["logs"][-3:]):
        store.add_log("info", "Application démarrée")
    thread = threading.Thread(target=schedule_loop, daemon=True)
    thread.start()


if __name__ == "__main__":
    startup()
    app.run(host="127.0.0.1", port=8787, debug=False)
