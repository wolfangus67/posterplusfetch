from __future__ import annotations

import asyncio
import contextlib
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
import hmac
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse, urlunparse
import json
import re
import threading

try:  # pragma: no cover - exercised implicitly in the real app
    from fastapi import APIRouter, HTTPException, Request
except ImportError:  # pragma: no cover - lightweight test fallback
    class HTTPException(Exception):
        def __init__(self, status_code: int = 500, detail: str = "") -> None:
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    class Request:  # type: ignore[override]
        query_params: dict[str, Any]
        app: Any

    class APIRouter:  # type: ignore[override]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def _decorator(self, func):
            return func

        def get(self, *args: Any, **kwargs: Any):
            return self._decorator

        def post(self, *args: Any, **kwargs: Any):
            return self._decorator

        def delete(self, *args: Any, **kwargs: Any):
            return self._decorator

from config import (
    ACCESS_KEY,
    PREFETCH_STATE_PATH,
)
from cache import delete_cached_final_posters_for_imdb, get_cached_quality, set_cached_quality
try:  # pragma: no cover - exercised in the full app
    from quality import _tokens_from_stremio_stream
except ImportError:  # pragma: no cover - lightweight test fallback
    def _tokens_from_stremio_stream(name: str, title: str, behavior_hints: dict | None = None) -> set[str]:
        binge_group = ""
        filename = ""
        if behavior_hints:
            binge_group = behavior_hints.get("bingeGroup") or ""
            filename = behavior_hints.get("filename") or ""
        text = f"{name}\n{title}\n{binge_group}\n{filename}".upper()
        tokens: set[str] = set()

        if re.search(r"\b(2160P|4K|UHD)\b", text):
            tokens.add("4K")
        elif "1080P" in text:
            tokens.add("1080P")

        if re.search(r"\bDV\b|DOLBY.?VISION|\bDOVI\b", text):
            tokens.add("DV")
        if "HDR10+" in text:
            tokens.add("HDR10+")
        elif re.search(r"\bHDR10\b|\bHDR\b", text):
            tokens.add("HDR10")

        if "REMUX" in text:
            tokens.add("REMUX")
        elif re.search(r"WEB.?DL|WEBDL", text):
            tokens.add("WEBDL")

        if "ATMOS" in text:
            tokens.add("ATMOS")
        if re.search(r"DTS.?X\b", text):
            tokens.add("DTSX")

        return tokens


FETCH_STATE_PATH = Path(PREFETCH_STATE_PATH)

DEFAULT_STATE: dict[str, Any] = {
    "config": {
        "enabled": True,
        "catalog_manifest_url": "",
        "stream_manifest_url": "",
        "run_time": "03:00",
        "days": [0, 1, 2, 3, 4, 5, 6],
        "limit": 500,
        "per_catalog_limit": 100,
        "schedule_enabled": True,
    },
    "catalogs": {
        "loaded": [],
        "selected": [],
    },
    "runtime": {
        "state": "idle",
        "message": "En attente",
        "progress": 0.0,
        "total_catalogs": 0,
        "processed_catalogs": 0,
        "processed_items": 0,
        "cached_items": 0,
        "requests_sent": 0,
        "errors": 0,
        "running_catalog": "",
        "started_at": None,
        "finished_at": None,
        "last_run": None,
        "next_run": None,
        "stop_requested": False,
    },
    "logs": [],
}


def _utc_now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalize_addon_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        raise ValueError("URL vide")

    if raw.startswith("stremio://"):
        raw = "https://" + raw[len("stremio://") :]

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


def _normalize_imdb_id(value: Any) -> str:
    raw = str(value or "").strip()
    if raw.startswith("tt"):
        return raw
    if "tt" in raw:
        match = re.search(r"(tt\d+)", raw)
        if match:
            return match.group(1)
    return ""


def _stream_cache_flag(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.lower()
        if any(token in lowered for token in ("uncached", "not cached", "non cached", "pas en cache", "not ready")):
            return False
        if any(token in lowered for token in ("cached", "en cache", "instant", "ready")):
            return True
    return None


def stream_is_debrid_cached(stream: dict[str, Any]) -> bool:
    stream_data = stream.get("streamData")
    if isinstance(stream_data, dict) and str(stream_data.get("type") or "").lower() == "statistic":
        return False

    checks = (
        "cached",
        "isCached",
        "cache",
        "debridCached",
        "instant",
        "isInstant",
        "ready",
        "availability",
        "status",
    )
    for key in checks:
        flag = _stream_cache_flag(stream.get(key))
        if flag is not None:
            return flag

    behavior_hints = stream.get("behaviorHints")
    if isinstance(behavior_hints, dict):
        for key in checks:
            flag = _stream_cache_flag(behavior_hints.get(key))
            if flag is not None:
                return flag

    text = "\n".join(str(stream.get(key) or "") for key in ("name", "title", "description"))
    flag = _stream_cache_flag(text)
    if flag is not None:
        return flag

    return bool(stream.get("url") or stream.get("externalUrl"))


def quality_tokens_from_streams(streams: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    for stream in streams[:5]:
        behavior_hints = stream.get("behaviorHints")
        seen |= _tokens_from_stremio_stream(
            stream.get("name") or "",
            stream.get("title") or stream.get("description") or "",
            behavior_hints if isinstance(behavior_hints, dict) else None,
        )

    tokens: list[str] = []
    for res in ("4K", "1080P"):
        if res in seen:
            tokens.append(res)
            break
    if tokens and not ({"REMUX", "WEBDL"} & seen):
        # AIOStreams formatters (notably GDrive/proxy based results) often expose
        # a cached 1080p/4K stream without a WEB-DL marker in the display text.
        # Treat cached debrid/cloud streams as web-sourced so "HD Web" can render.
        seen.add("WEBDL")
    for source in ("REMUX", "WEBDL"):
        if source in seen:
            tokens.append(source)
            break
    for visual in ("DV", "HDR10+", "HDR10"):
        if visual in seen:
            tokens.append(visual)
            break
    for audio in ("ATMOS", "DTSX"):
        if audio in seen:
            tokens.append(audio)
            break
    return tokens


def _quality_tokens_need_source_enrichment(tokens: list[str] | None) -> bool:
    token_set = set(tokens or [])
    return bool(token_set & {"4K", "1080P"}) and not bool(token_set & {"REMUX", "WEBDL"})


def _extract_catalogs(manifest: dict[str, Any], base_url: str, source_label: str) -> list[dict[str, Any]]:
    catalog_entries: list[dict[str, Any]] = []
    for idx, catalog in enumerate(manifest.get("catalogs", []) or []):
        if not isinstance(catalog, dict):
            continue
        if catalog.get("isSearch"):
            continue

        media_type = str(catalog.get("type", "")).lower().strip()
        if media_type not in {"movie", "series", "mixed"}:
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
                "home": bool(catalog.get("showInHome")),
                "source": source_label,
                "addon_base_url": base_url,
                "manifest_url": build_manifest_url(base_url),
                "order": idx,
                "selected": True,
            }
        )

    return catalog_entries


def _catalog_identity(catalog: dict[str, Any]) -> str:
    return catalog.get("key") or f"{catalog.get('addon_base_url')}|{catalog.get('type')}|{catalog.get('id')}"


def _merge_selection(
    loaded: list[dict[str, Any]],
    existing: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    lookup = {_catalog_identity(item): item for item in existing}
    merged: list[dict[str, Any]] = []
    for catalog in loaded:
        current = deepcopy(catalog)
        old = lookup.get(_catalog_identity(catalog))
        if old:
            current["selected"] = bool(old.get("selected", True))
            current["order"] = int(old.get("order", current.get("order", 0)))
        merged.append(current)
    merged.sort(key=lambda item: int(item.get("order", 0)))
    return merged


def _compute_next_run(config: dict[str, Any], now: datetime | None = None) -> str | None:
    if not config.get("schedule_enabled", True):
        return None

    days = config.get("days") or []
    if not days:
        return None

    run_time = str(config.get("run_time") or "03:00")
    try:
        hour, minute = (int(part) for part in run_time.split(":", 1))
    except Exception:
        hour, minute = 3, 0

    now = now or datetime.now()
    for offset in range(8):
        candidate_day = now.date() + timedelta(days=offset)
        candidate = datetime.combine(candidate_day, datetime.min.time()).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        if candidate <= now:
            continue
        if candidate.weekday() in days:
            return candidate.isoformat(timespec="seconds")
    return None


class PrefetchStore:
    def __init__(self, path: Path = FETCH_STATE_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._state = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            state = deepcopy(DEFAULT_STATE)
            self._save_unlocked(state)
            return state

        try:
            with self.path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            state = deepcopy(DEFAULT_STATE)
            self._save_unlocked(state)
            return state

        state = deepcopy(DEFAULT_STATE)
        for section in ("config", "catalogs", "runtime"):
            state[section].update(data.get(section, {}) or {})
        logs = data.get("logs", [])
        if isinstance(logs, list):
            state["logs"] = logs[-500:]
        return state

    def _save_unlocked(self, state: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
        tmp.replace(self.path)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._state)

    def update_config(self, **kwargs: Any) -> dict[str, Any]:
        with self._lock:
            self._state["config"].update(kwargs)
            self._save_unlocked(self._state)
            return deepcopy(self._state["config"])

    def set_loaded_catalogs(self, catalogs: list[dict[str, Any]]) -> None:
        with self._lock:
            self._state["catalogs"]["loaded"] = deepcopy(catalogs)
            self._save_unlocked(self._state)

    def set_selected_catalogs(self, catalogs: list[dict[str, Any]]) -> None:
        with self._lock:
            self._state["catalogs"]["selected"] = deepcopy(catalogs)
            self._save_unlocked(self._state)

    def update_runtime(self, **kwargs: Any) -> dict[str, Any]:
        with self._lock:
            self._state["runtime"].update(kwargs)
            self._save_unlocked(self._state)
            return deepcopy(self._state["runtime"])

    def add_log(self, level: str, message: str, **context: Any) -> dict[str, Any]:
        entry = {
            "ts": _utc_now_iso(),
            "level": level.upper(),
            "message": message,
            "context": context or {},
        }
        with self._lock:
            self._state["logs"].append(entry)
            self._state["logs"] = self._state["logs"][-500:]
            self._save_unlocked(self._state)
            return deepcopy(entry)


@dataclass
class JobResult:
    processed_catalogs: int = 0
    processed_items: int = 0
    cached_items: int = 0
    requests_sent: int = 0
    errors: int = 0


class PrefetchService:
    def __init__(self, store: PrefetchStore) -> None:
        self.store = store
        self.client: Any | None = None
        self._job_lock = asyncio.Lock()
        self._job_task: asyncio.Task[None] | None = None
        self._scheduler_task: asyncio.Task[None] | None = None
        self._last_schedule_slot: str | None = None
        self._stop_event = asyncio.Event()

    def configure_client(self, client: Any | None) -> None:
        self.client = client

    async def start(self) -> None:
        if self._scheduler_task is None:
            self._scheduler_task = asyncio.create_task(self._scheduler_loop())

    async def shutdown(self) -> None:
        self._stop_event.set()
        if self._job_task and not self._job_task.done():
            self._job_task.cancel()
        if self._scheduler_task and not self._scheduler_task.done():
            self._scheduler_task.cancel()
        for task in (self._job_task, self._scheduler_task):
            if task is None:
                continue
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._job_task = None
        self._scheduler_task = None

    def _runtime_snapshot(self) -> dict[str, Any]:
        state = self.store.snapshot()
        state["runtime"]["next_run"] = _compute_next_run(state["config"])
        return state

    async def validate_manifest(self, url: str) -> dict[str, Any]:
        client = self._require_client()
        base_url = normalize_addon_url(url)
        manifest = await self._fetch_json(client, build_manifest_url(base_url))
        catalogs = _extract_catalogs(manifest, base_url, manifest.get("name") or "Addon")
        return {
            "name": manifest.get("name") or "Addon",
            "version": manifest.get("version") or "",
            "url": base_url,
            "catalog_count": len(catalogs),
            "catalogs": catalogs,
        }

    async def load_catalogs(self, catalog_manifest_url: str) -> dict[str, Any]:
        client = self._require_client()
        base_url = normalize_addon_url(catalog_manifest_url)
        manifest = await self._fetch_json(client, build_manifest_url(base_url))
        loaded = _extract_catalogs(manifest, base_url, manifest.get("name") or "Catalog addon")
        existing = self.store.snapshot()["catalogs"].get("loaded", [])
        merged = _merge_selection(loaded, existing)
        self.store.update_config(catalog_manifest_url=base_url)
        self.store.set_loaded_catalogs(merged)
        self.store.set_selected_catalogs([c for c in merged if c.get("selected", True)])
        self.store.update_runtime(
            message=f"{len(merged)} catalogues chargés",
            next_run=_compute_next_run(self.store.snapshot()["config"]),
        )
        self.store.add_log("info", "Catalogues chargés", count=len(merged))
        return {
            "name": manifest.get("name") or "Catalog addon",
            "url": base_url,
            "catalog_count": len(merged),
            "catalogs": merged,
        }

    async def save_catalog_selection(self, catalogs: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
                    "home": bool(catalog.get("home")),
                    "source": catalog.get("source"),
                    "addon_base_url": catalog.get("addon_base_url"),
                    "manifest_url": catalog.get("manifest_url"),
                    "order": int(catalog.get("order", idx)),
                    "selected": bool(catalog.get("selected", True)),
                }
            )

        normalized.sort(key=lambda item: int(item.get("order", 0)))
        self.store.set_loaded_catalogs(normalized)
        self.store.set_selected_catalogs([cat for cat in normalized if cat.get("selected", True)])
        self.store.add_log("info", "Sélection des catalogues enregistrée", count=len(normalized))
        return normalized

    async def update_schedule(self, payload: dict[str, Any]) -> dict[str, Any]:
        run_time = str(payload.get("run_time") or payload.get("time") or "03:00")
        days = payload.get("days")
        if not isinstance(days, list) or not days:
            raise ValueError("Au moins un jour doit être sélectionné")

        try:
            hour, minute = (int(part) for part in run_time.split(":", 1))
            if hour < 0 or hour > 23 or minute < 0 or minute > 59:
                raise ValueError
        except Exception as exc:
            raise ValueError("L'heure doit être au format HH:MM") from exc

        current_config = self.store.snapshot()["config"]
        catalog_manifest_url = (
            payload.get("catalog_manifest_url")
            if "catalog_manifest_url" in payload
            else payload.get("manifest_url", current_config.get("catalog_manifest_url", ""))
        )
        stream_manifest_url = (
            payload.get("stream_manifest_url")
            if "stream_manifest_url" in payload
            else current_config.get("stream_manifest_url", "")
        )
        config = self.store.update_config(
            catalog_manifest_url=str(catalog_manifest_url or "").strip(),
            stream_manifest_url=str(stream_manifest_url or "").strip(),
            schedule_enabled=bool(payload.get("enabled", True)),
            run_time=f"{hour:02d}:{minute:02d}",
            days=[int(day) for day in days],
            limit=max(1, int(payload.get("limit") or 500)),
            per_catalog_limit=max(1, int(payload.get("per_catalog_limit") or payload.get("catalog_limit") or 100)),
        )
        self.store.update_runtime(
            message="Planification mise à jour",
            next_run=_compute_next_run(config),
        )
        self.store.add_log(
            "info",
            "Planification enregistrée",
            run_time=config["run_time"],
            days=config["days"],
        )
        return {**config, "next_run": _compute_next_run(config)}

    async def disable_schedule(self) -> dict[str, Any]:
        config = self.store.update_config(schedule_enabled=False)
        self.store.update_runtime(next_run=None, message="Planification désactivée")
        self.store.add_log("warning", "Planification désactivée")
        return {**config, "next_run": None}

    async def start_manual_job(self) -> tuple[bool, str]:
        async with self._job_lock:
            if self._job_task and not self._job_task.done():
                return False, "Un job est déjà en cours"
            snapshot = self.store.snapshot()
            if not snapshot["catalogs"].get("selected", []):
                return False, "Aucun catalogue sélectionné"
            self._stop_event.clear()
            self._job_task = asyncio.create_task(self._run_job(manual=True))
            return True, "Job lancé"

    async def stop_job(self) -> tuple[bool, str]:
        if not self._job_task or self._job_task.done():
            return False, "Aucun job en cours"
        self._stop_event.set()
        self.store.update_runtime(stop_requested=True, message="Arrêt demandé")
        return True, "Arrêt demandé"

    def get_status(self) -> dict[str, Any]:
        return self._runtime_snapshot()

    async def warm_item(self, imdb_id: str, media_type: str, stream_manifest_url: str = "") -> dict[str, Any]:
        imdb_id = _normalize_imdb_id(imdb_id)
        if not imdb_id:
            raise ValueError("IMDb ID invalide")
        media_type = "series" if media_type in {"series", "tv"} else "movie"

        cached = get_cached_quality(imdb_id)
        manifest_url = stream_manifest_url.strip() or str(
            self.store.snapshot()["config"].get("stream_manifest_url") or ""
        ).strip()
        if cached is not None and not (_quality_tokens_need_source_enrichment(cached) and manifest_url):
            delete_cached_final_posters_for_imdb(imdb_id)
            return {"status": "already_cached", "tokens": cached}
        if not manifest_url:
            return {"status": "missing_manifest", "tokens": None}

        tokens = await self._fetch_cached_stream_quality(
            client=self._require_client(),
            stream_manifest_url=manifest_url,
            imdb_id=imdb_id,
            media_type=media_type,
        )
        if tokens is None:
            return {"status": "not_cached", "tokens": None}
        self.store.add_log("info", "Qualité cache détectée pour la preview", imdb_id=imdb_id, tokens=tokens)
        return {"status": "cached", "tokens": tokens}

    async def _scheduler_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._maybe_run_scheduled_job()
            except Exception as exc:  # pragma: no cover
                self.store.add_log("error", "Erreur du scheduler", error=str(exc))
            await asyncio.sleep(20)

    async def _maybe_run_scheduled_job(self) -> None:
        snapshot = self.store.snapshot()
        config = snapshot["config"]
        runtime = snapshot["runtime"]
        if not config.get("schedule_enabled", True):
            return
        if not snapshot["catalogs"].get("selected", []):
            return
        if runtime.get("state") == "running":
            return

        now = datetime.now()
        run_time = str(config.get("run_time") or "03:00")
        try:
            hour, minute = (int(part) for part in run_time.split(":", 1))
        except Exception:
            hour, minute = 3, 0
        if now.hour != hour or now.minute != minute:
            return
        if now.weekday() not in (config.get("days") or []):
            return

        slot = now.strftime("%Y-%m-%d %H:%M")
        if self._last_schedule_slot == slot:
            return
        self._last_schedule_slot = slot
        async with self._job_lock:
            if self._job_task and not self._job_task.done():
                return
            self._stop_event.clear()
            self._job_task = asyncio.create_task(self._run_job(manual=False))

    async def _run_job(self, manual: bool) -> None:
        snapshot = self.store.snapshot()
        config = snapshot["config"]
        catalogs = snapshot["catalogs"].get("selected", [])
        started_at = _utc_now_iso()
        total_catalogs = len(catalogs)

        self.store.update_runtime(
            state="running",
            message="Préchargement en cours",
            progress=0.0,
            total_catalogs=total_catalogs,
            processed_catalogs=0,
            processed_items=0,
            cached_items=0,
            requests_sent=0,
            errors=0,
            running_catalog="",
            started_at=started_at,
            finished_at=None,
            last_run=snapshot["runtime"].get("last_run"),
            next_run=_compute_next_run(config),
            stop_requested=False,
        )
        self.store.add_log("info", "Job démarré", manual=manual, selected=total_catalogs)

        result = JobResult()
        try:
            if not catalogs:
                await self._finish_job(result, "Aucun catalogue sélectionné", error=True)
                return

            client = self._require_client()
            global_limit = max(1, int(config.get("limit") or 500))
            per_catalog_limit = max(1, int(config.get("per_catalog_limit") or global_limit))
            remaining_budget = global_limit
            for index, catalog in enumerate(catalogs, start=1):
                if self._stop_event.is_set():
                    self.store.add_log("warning", "Job interrompu par l'opérateur")
                    break

                self.store.update_runtime(
                    running_catalog=catalog.get("name", catalog.get("id", "")),
                    processed_catalogs=index - 1,
                    progress=(index - 1) / max(total_catalogs, 1),
                )

                self.store.add_log(
                    "info",
                    "Catalogue en cours",
                    catalog=catalog.get("name"),
                    type=catalog.get("type"),
                )

                page_size = max(1, min(int(catalog.get("pageSize") or 100), 100))
                processed_for_catalog = 0
                skip = 0
                while processed_for_catalog < per_catalog_limit and remaining_budget > 0 and not self._stop_event.is_set():
                    catalog_url = build_catalog_url(
                        catalog["addon_base_url"],
                        catalog["type"],
                        catalog["id"],
                        skip,
                        page_size,
                    )
                    try:
                        payload = await self._fetch_json(client, catalog_url)
                        result.requests_sent += 1
                    except Exception as exc:
                        result.errors += 1
                        self.store.add_log(
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
                        if self._stop_event.is_set():
                            break

                        result.processed_items += 1
                        processed_for_catalog += 1
                        remaining_budget -= 1

                        media_type = str(meta.get("type") or catalog.get("type") or "").lower().strip()
                        if media_type not in {"movie", "series"}:
                            continue

                        imdb_id = _normalize_imdb_id(meta.get("id") or meta.get("imdb_id"))
                        if not imdb_id:
                            continue

                        cached = get_cached_quality(imdb_id)
                        if cached is not None:
                            result.cached_items += 1
                            self.store.add_log(
                                "info",
                                "Qualité déjà en cache",
                                imdb_id=imdb_id,
                                tokens=cached,
                            )
                        else:
                            stream_manifest_url = str(config.get("stream_manifest_url") or "").strip()
                            if stream_manifest_url:
                                result.requests_sent += 1
                                try:
                                    warmed = await self._fetch_cached_stream_quality(
                                        client=client,
                                        stream_manifest_url=stream_manifest_url,
                                        imdb_id=imdb_id,
                                        media_type=media_type,
                                    )
                                except Exception as exc:
                                    result.errors += 1
                                    self.store.add_log(
                                        "warning",
                                        "Échec de lecture AIOStreams cache-only",
                                        imdb_id=imdb_id,
                                        error=str(exc),
                                    )
                                    continue
                                if warmed is not None:
                                    result.cached_items += 1
                                    self.store.add_log(
                                        "info",
                                        "Qualité cache détectée via AIOStreams",
                                        imdb_id=imdb_id,
                                        tokens=warmed,
                                    )

                        self.store.update_runtime(
                            processed_items=result.processed_items,
                            cached_items=result.cached_items,
                            requests_sent=result.requests_sent,
                            errors=result.errors,
                            progress=(index - 1 + min(processed_for_catalog, per_catalog_limit) / max(per_catalog_limit, 1))
                            / max(total_catalogs, 1),
                        )

                        if remaining_budget <= 0 or processed_for_catalog >= per_catalog_limit:
                            break

                    if len(metas) < page_size:
                        break
                    skip += page_size

                result.processed_catalogs = index
                self.store.update_runtime(
                    processed_catalogs=index,
                    processed_items=result.processed_items,
                    cached_items=result.cached_items,
                    requests_sent=result.requests_sent,
                    errors=result.errors,
                    progress=index / max(total_catalogs, 1),
                )

            if self._stop_event.is_set():
                await self._finish_job(result, "Job interrompu", error=True)
            else:
                await self._finish_job(result, "Préchargement terminé avec succès")
        except Exception as exc:  # pragma: no cover
            result.errors += 1
            self.store.add_log("error", "Job en erreur", error=str(exc))
            await self._finish_job(result, f"Erreur inattendue: {exc}", error=True)
        finally:
            self._job_task = None

    async def _finish_job(self, result: JobResult, message: str, error: bool = False) -> None:
        finished_at = _utc_now_iso()
        runtime_update = {
            "state": "error" if error else "success",
            "message": message,
            "progress": 1.0,
            "processed_catalogs": result.processed_catalogs,
            "processed_items": result.processed_items,
            "cached_items": result.cached_items,
            "requests_sent": result.requests_sent,
            "errors": result.errors,
            "running_catalog": "",
            "finished_at": finished_at,
            "last_run": finished_at,
            "next_run": _compute_next_run(self.store.snapshot()["config"]),
            "stop_requested": bool(self._stop_event.is_set()),
        }
        self.store.update_runtime(**runtime_update)
        self.store.add_log(
            "info" if not error else "error",
            message,
            processed_catalogs=result.processed_catalogs,
            processed_items=result.processed_items,
            cached_items=result.cached_items,
            requests_sent=result.requests_sent,
            errors=result.errors,
        )

    async def _fetch_json(self, client: Any, url: str, timeout: float = 20.0) -> dict[str, Any]:
        response = await client.get(
            url,
            timeout=timeout,
            headers={
                "Accept": "application/json",
                "User-Agent": "PostersPlus-Prefetch/1.0",
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Réponse JSON invalide")
        return payload

    async def _fetch_cached_stream_quality(
        self,
        client: Any,
        stream_manifest_url: str,
        imdb_id: str,
        media_type: str,
        season: int = 1,
        episode: int = 1,
    ) -> list[str] | None:
        base_url = normalize_addon_url(stream_manifest_url)
        stream_type = "series" if media_type in {"series", "tv"} else "movie"
        item_id = f"{imdb_id}:{season}:{episode}" if stream_type == "series" else imdb_id
        payload = await self._fetch_json(client, build_stream_url(base_url, stream_type, item_id), timeout=30.0)
        streams = [s for s in (payload.get("streams") or []) if isinstance(s, dict)]
        cached_streams = [stream for stream in streams if stream_is_debrid_cached(stream)]
        if not cached_streams and stream_type == "series":
            payload = await self._fetch_json(client, build_stream_url(base_url, "series", imdb_id), timeout=30.0)
            streams = [s for s in (payload.get("streams") or []) if isinstance(s, dict)]
            cached_streams = [stream for stream in streams if stream_is_debrid_cached(stream)]
        if not cached_streams:
            return None

        tokens = quality_tokens_from_streams(cached_streams)
        set_cached_quality(imdb_id, tokens)
        delete_cached_final_posters_for_imdb(imdb_id)
        return tokens

    def _require_client(self) -> Any:
        if self.client is None:
            raise RuntimeError("Client HTTP non initialisé")
        return self.client


router = APIRouter(prefix="/prefetch", tags=["prefetch"])


def _service(request: Request) -> PrefetchService:
    if ACCESS_KEY:
        provided = request.query_params.get("access_key", "")
        if not hmac.compare_digest(provided, ACCESS_KEY):
            raise HTTPException(status_code=403, detail="Unauthorized")
    service = getattr(request.app.state, "prefetch_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Prefetch service not initialised")
    return service


@router.get("/status")
async def get_status(request: Request):
    return {
        "success": True,
        **_service(request).get_status(),
    }


@router.post("/manifest")
async def validate_manifest(request: Request):
    payload = await request.json()
    url = (payload.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL du manifest manquante")
    try:
        addon = await _service(request).validate_manifest(url)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Impossible de charger le manifest: {exc}") from exc
    return {"success": True, "addon": addon}


@router.post("/load")
async def load_catalogs(request: Request):
    payload = await request.json()
    url = (payload.get("catalog_manifest_url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="L'URL du manifest catalogue est obligatoire")
    try:
        addon = await _service(request).load_catalogs(url)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Échec du chargement des catalogues: {exc}") from exc
    return {"success": True, "addon": addon}


@router.get("/selection")
async def get_selection(request: Request):
    state = _service(request).store.snapshot()
    return {
        "success": True,
        "catalogs": state["catalogs"].get("selected", []),
        "loaded": state["catalogs"].get("loaded", []),
    }


@router.post("/selection")
async def save_selection(request: Request):
    payload = await request.json()
    catalogs = payload.get("catalogs")
    if not isinstance(catalogs, list):
        raise HTTPException(status_code=400, detail="La liste des catalogues est manquante")
    normalized = await _service(request).save_catalog_selection(catalogs)
    return {"success": True, "catalogs": normalized}


@router.get("/schedule")
async def get_schedule(request: Request):
    state = _service(request).store.snapshot()
    return {
        "success": True,
        "schedule": {
            **state["config"],
            "next_run": _compute_next_run(state["config"]),
        },
    }


@router.post("/schedule")
async def save_schedule(request: Request):
    payload = await request.json()
    try:
        schedule = await _service(request).update_schedule(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "schedule": schedule}


@router.delete("/schedule")
async def disable_schedule(request: Request):
    schedule = await _service(request).disable_schedule()
    return {"success": True, "schedule": schedule}


@router.post("/run")
async def run_job(request: Request):
    ok, message = await _service(request).start_manual_job()
    if not ok:
        raise HTTPException(status_code=409, detail=message)
    return {"success": True, "message": message}


@router.post("/stop")
async def stop_job(request: Request):
    ok, message = await _service(request).stop_job()
    if not ok:
        raise HTTPException(status_code=409, detail=message)
    return {"success": True, "message": message}


@router.post("/warm")
async def warm_item(request: Request):
    payload = await request.json()
    try:
        result = await _service(request).warm_item(
            imdb_id=str(payload.get("imdb_id") or ""),
            media_type=str(payload.get("type") or payload.get("media_type") or "movie"),
            stream_manifest_url=str(payload.get("stream_manifest_url") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Échec AIOStreams cache-only: {exc}") from exc
    return {"success": True, **result}


@router.get("/logs")
async def get_logs(request: Request, limit: int = 50):
    state = _service(request).store.snapshot()
    limit = max(1, min(limit, 200))
    return {"success": True, "logs": state["logs"][-limit:]}
