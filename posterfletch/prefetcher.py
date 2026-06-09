from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote, urlparse, urlunparse
import threading
import time

from storage import StateStore, utc_now_iso


REQUEST_TIMEOUT = 20
STREAM_WARM_TIMEOUT = 10


def normalize_addon_base(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        raise ValueError("URL vide")

    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"URL invalide: {raw!r}")

    path = parsed.path.rstrip("/")
    for suffix in ("/manifest.json", "/configure"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]

    normalized = urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))
    return normalized.rstrip("/")


def build_manifest_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/manifest.json"


def build_catalog_url(base_url: str, media_type: str, catalog_id: str, skip: int, limit: int) -> str:
    return (
        f"{base_url.rstrip('/')}/catalog/{quote(media_type, safe='')}/"
        f"{quote(catalog_id, safe='')}.json?skip={skip}&limit={limit}"
    )


def build_stream_url(base_url: str, media_type: str, item_id: str) -> str:
    return (
        f"{base_url.rstrip('/')}/stream/{quote(media_type, safe='')}/"
        f"{quote(item_id, safe='')}.json"
    )


def safe_iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") if dt else None


def compute_next_run(config: dict[str, Any], now: datetime | None = None) -> str | None:
    if not config.get("schedule_enabled", True):
        return None

    time_value = str(config.get("run_time", "03:00"))
    try:
        hour, minute = (int(part) for part in time_value.split(":", 1))
    except Exception:
        hour, minute = 3, 0

    days = config.get("days") or []
    if not days:
        return None

    now = now or datetime.now()
    for offset in range(8):
        candidate_day = now.date() + timedelta(days=offset)
        candidate_dt = datetime.combine(candidate_day, datetime.min.time()).replace(
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0,
        )
        if candidate_dt <= now:
            continue
        if candidate_dt.weekday() in days:
            return safe_iso(candidate_dt)

    return None


def fetch_json(url: str, timeout: int = REQUEST_TIMEOUT) -> dict[str, Any]:
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError(
            "La dépendance 'requests' est requise pour contacter les manifests"
        ) from exc

    response = requests.get(
        url,
        timeout=timeout,
        headers={
            "Accept": "application/json",
            "User-Agent": "Posterfletch/1.0",
        },
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("Réponse JSON invalide")
    return data


def extract_catalogs(manifest: dict[str, Any], base_url: str, source_label: str) -> list[dict[str, Any]]:
    catalog_entries: list[dict[str, Any]] = []
    for idx, catalog in enumerate(manifest.get("catalogs", []) or []):
        if not isinstance(catalog, dict):
            continue

        if catalog.get("isSearch"):
            continue

        media_type = str(catalog.get("type", "")).lower().strip()
        if media_type not in {"movie", "series"}:
            continue

        catalog_id = str(catalog.get("id", "")).strip()
        if not catalog_id:
            continue

        catalog_entries.append(
            {
                "key": f"{base_url}|{media_type}|{catalog_id}",
                "id": catalog_id,
                "type": media_type,
                "name": catalog.get("name") or catalog_id,
                "pageSize": int(catalog.get("pageSize") or 100),
                "extra": catalog.get("extra", []) or [],
                "source": source_label,
                "addon_base_url": base_url,
                "manifest_url": build_manifest_url(base_url),
                "order": idx,
                "selected": True,
            }
        )

    return catalog_entries


@dataclass
class JobResult:
    processed: int = 0
    cached: int = 0
    requests_sent: int = 0
    errors: int = 0


class PrefetchRunner:
    def __init__(self, store: StateStore) -> None:
        self.store = store
        self.stop_event = threading.Event()
        self._job_lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._last_schedule_fire: str | None = None

    def is_running(self) -> bool:
        return bool(self._worker and self._worker.is_alive())

    def start_manual_job(self) -> tuple[bool, str]:
        with self._job_lock:
            if self.is_running():
                return False, "Un job est déjà en cours"

            snapshot = self.store.snapshot()
            if not snapshot["catalogs"].get("selected", []):
                return False, "Aucun catalogue sélectionné"

            self.stop_event.clear()
            worker = threading.Thread(target=self._run_job, args=(True,), daemon=True)
            self._worker = worker
            worker.start()
            return True, "Job lancé"

    def maybe_run_scheduled_job(self) -> None:
        with self._job_lock:
            if self.is_running():
                return

            state = self.store.snapshot()
            config = state["config"]
            runtime = state["runtime"]
            if not config.get("schedule_enabled", True):
                return
            if not state["catalogs"].get("selected", []):
                return
            if runtime.get("state") == "running":
                return

            now = datetime.now()
            current_key = now.strftime("%Y-%m-%d %H:%M")
            if self._last_schedule_fire == current_key:
                return

            run_time = str(config.get("run_time", "03:00"))
            try:
                hour, minute = (int(part) for part in run_time.split(":", 1))
            except Exception:
                hour, minute = 3, 0

            if now.hour != hour or now.minute != minute:
                return

            if now.weekday() not in (config.get("days") or []):
                return

            self._last_schedule_fire = current_key
            self.stop_event.clear()
            worker = threading.Thread(target=self._run_job, args=(False,), daemon=True)
            self._worker = worker
            worker.start()

    def _set_runtime(self, **kwargs: Any) -> None:
        self.store.update_runtime(**kwargs)

    def _log(self, level: str, message: str, **context: Any) -> None:
        self.store.add_log(level, message, **context)

    def _run_job(self, manual: bool) -> None:
        snapshot = self.store.snapshot()
        config = snapshot["config"]
        selected_catalogs = snapshot["catalogs"].get("selected", [])
        started_at = utc_now_iso()

        self._set_runtime(
            state="running",
            message="Préchargement en cours",
            progress=0,
            total=max(len(selected_catalogs), 1),
            processed=0,
            cached=0,
            requests=0,
            errors=0,
            running_catalog="",
            started_at=started_at,
            finished_at=None,
            last_run=snapshot["runtime"].get("last_run"),
            next_run=compute_next_run(config),
        )
        self._log("info", "Job démarré", manual=manual, selected=len(selected_catalogs))

        result = JobResult()

        if not selected_catalogs:
            self._finish_job(result, "Aucun catalogue sélectionné", error=True)
            return

        stream_base = (config.get("stream_manifest_url") or "").strip()
        stream_base = normalize_addon_base(stream_base) if stream_base else ""
        global_request_limit = 50
        request_budget = 0

        try:
            for index, catalog in enumerate(selected_catalogs, start=1):
                if self.stop_event.is_set():
                    self._log("warning", "Job interrompu par l'opérateur")
                    break

                self._set_runtime(
                    running_catalog=catalog.get("name", catalog.get("id", "")),
                    progress=index - 1,
                )
                self._log(
                    "info",
                    "Catalogue en cours",
                    catalog=catalog.get("name"),
                    type=catalog.get("type"),
                )

                page_size = int(catalog.get("pageSize") or 100)
                limit = int(config.get("limit") or 500)
                catalog_limit = min(limit, 5000)
                processed_for_catalog = 0
                skip = 0

                while processed_for_catalog < catalog_limit and not self.stop_event.is_set():
                    catalog_url = build_catalog_url(
                        catalog["addon_base_url"],
                        catalog["type"],
                        catalog["id"],
                        skip,
                        page_size,
                    )
                    try:
                        payload = fetch_json(catalog_url)
                        result.requests_sent += 1
                        request_budget += 1
                    except Exception as exc:
                        result.errors += 1
                        self._log(
                            "error",
                            "Échec de chargement du catalogue",
                            catalog=catalog.get("name"),
                            error=str(exc),
                        )
                        break

                    metas = payload.get("metas", []) or []
                    if not metas:
                        break

                    for meta in metas:
                        if self.stop_event.is_set():
                            break

                        result.processed += 1
                        processed_for_catalog += 1

                        if (
                            stream_base
                            and request_budget < global_request_limit
                            and catalog["type"] in {"movie", "series"}
                        ):
                            warmed = self._warm_streams(stream_base, catalog["type"], meta, result)
                            request_budget += warmed

                        self._set_runtime(
                            progress=index - 1 + processed_for_catalog / max(catalog_limit, 1),
                            processed=result.processed,
                            cached=result.cached,
                            requests=result.requests_sent,
                            errors=result.errors,
                        )

                        if processed_for_catalog >= catalog_limit:
                            break

                    if len(metas) < page_size:
                        break
                    skip += page_size

                self._set_runtime(
                    progress=index,
                    processed=result.processed,
                    cached=result.cached,
                    requests=result.requests_sent,
                    errors=result.errors,
                )

            if self.stop_event.is_set():
                self._finish_job(result, "Job interrompu", error=True)
            else:
                self._finish_job(result, "Préchargement terminé avec succès")
        except Exception as exc:  # pragma: no cover - safety net
            result.errors += 1
            self._log("error", "Job en erreur", error=str(exc))
            self._finish_job(result, f"Erreur inattendue: {exc}", error=True)

    def _warm_streams(self, base_url: str, media_type: str, meta: dict[str, Any], result: JobResult) -> int:
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError(
                "La dépendance 'requests' est requise pour réchauffer les flux"
            ) from exc

        warmed_requests = 0
        item_id = str(meta.get("id", "")).strip()
        if not item_id:
            return 0

        stream_url = build_stream_url(base_url, media_type, item_id)
        try:
            payload = fetch_json(stream_url, timeout=STREAM_WARM_TIMEOUT)
            warmed_requests += 1
            result.requests_sent += 1
        except Exception as exc:
            result.errors += 1
            self._log("warning", "Échec de lecture des streams", item=item_id, error=str(exc))
            return warmed_requests

        streams = payload.get("streams", []) or []
        for stream in streams:
            if result.cached >= 9999:
                break
            if warmed_requests >= 2:
                break

            url = str(stream.get("url") or "").strip()
            if not url or not url.startswith(("http://", "https://")):
                continue

            try:
                response = requests.get(
                    url,
                    timeout=STREAM_WARM_TIMEOUT,
                    stream=True,
                    headers={
                        "User-Agent": "Posterfletch/1.0",
                        "Range": "bytes=0-0",
                    },
                )
                response.raise_for_status()
                response.close()
                warmed_requests += 1
                result.requests_sent += 1
                result.cached += 1
            except Exception as exc:
                result.errors += 1
                self._log(
                    "warning",
                    "Échec de préchauffage d'un flux",
                    item=item_id,
                    error=str(exc),
                )

        return warmed_requests

    def _finish_job(self, result: JobResult, message: str, error: bool = False) -> None:
        finished_at = utc_now_iso()
        runtime_update = {
            "state": "error" if error else "success",
            "message": message,
            "progress": 1,
            "processed": result.processed,
            "cached": result.cached,
            "requests": result.requests_sent,
            "errors": result.errors,
            "running_catalog": "",
            "finished_at": finished_at,
            "last_run": finished_at,
            "next_run": compute_next_run(self.store.snapshot()["config"]),
        }
        self._set_runtime(**runtime_update)
        self._log(
            "info" if not error else "error",
            message,
            processed=result.processed,
            cached=result.cached,
            requests=result.requests_sent,
            errors=result.errors,
        )
