from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import json
import threading
from typing import Any


DEFAULT_STATE: dict[str, Any] = {
    "config": {
        "enabled": True,
        "catalog_manifest_url": "",
        "stream_manifest_url": "",
        "run_time": "03:00",
        "days": [0, 1, 2, 3, 4, 5, 6],
        "limit": 500,
        "include_movies": True,
        "include_series": True,
        "refresh_expired": True,
        "schedule_enabled": True,
    },
    "catalogs": {
        "loaded": [],
        "selected": [],
    },
    "runtime": {
        "state": "idle",
        "message": "En attente",
        "progress": 0,
        "total": 0,
        "processed": 0,
        "cached": 0,
        "requests": 0,
        "errors": 0,
        "running_catalog": "",
        "started_at": None,
        "finished_at": None,
        "last_run": None,
        "next_run": None,
    },
    "logs": [],
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class StateStore:
    """Thread-safe JSON-backed application state."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
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

        return self._merge_defaults(data)

    def _merge_defaults(self, data: dict[str, Any]) -> dict[str, Any]:
        merged = deepcopy(DEFAULT_STATE)

        for section in ("config", "catalogs", "runtime"):
            merged[section].update(data.get(section, {}) or {})

        logs = data.get("logs", [])
        if isinstance(logs, list):
            merged["logs"] = logs[-200:]

        return merged

    def _save_unlocked(self, state: dict[str, Any]) -> None:
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
        tmp_path.replace(self.path)

    def save(self) -> None:
        with self.lock:
            self._save_unlocked(self._state)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return deepcopy(self._state)

    def get(self, section: str, default: Any = None) -> Any:
        with self.lock:
            return deepcopy(self._state.get(section, default))

    def update_config(self, **kwargs: Any) -> dict[str, Any]:
        with self.lock:
            self._state["config"].update(kwargs)
            self._save_unlocked(self._state)
            return deepcopy(self._state["config"])

    def set_loaded_catalogs(self, catalogs: list[dict[str, Any]]) -> None:
        with self.lock:
            self._state["catalogs"]["loaded"] = deepcopy(catalogs)
            self._save_unlocked(self._state)

    def set_selected_catalogs(self, catalogs: list[dict[str, Any]]) -> None:
        with self.lock:
            self._state["catalogs"]["selected"] = deepcopy(catalogs)
            self._save_unlocked(self._state)

    def update_runtime(self, **kwargs: Any) -> dict[str, Any]:
        with self.lock:
            self._state["runtime"].update(kwargs)
            self._save_unlocked(self._state)
            return deepcopy(self._state["runtime"])

    def add_log(self, level: str, message: str, **context: Any) -> dict[str, Any]:
        entry = {
            "ts": utc_now_iso(),
            "level": level.upper(),
            "message": message,
            "context": context or {},
        }

        with self.lock:
            self._state["logs"].append(entry)
            self._state["logs"] = self._state["logs"][-200:]
            self._save_unlocked(self._state)
            return deepcopy(entry)

