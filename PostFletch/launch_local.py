from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / "cache"
LOG_DIR = ROOT


def _build_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("DB_PATH", str(CACHE_DIR / "cache.db"))
    env.setdefault("BADGE_DIR", str(ROOT / "badges"))
    env.setdefault("TMDB_POSTER_CACHE_DIR", str(CACHE_DIR / "tmdb_posters"))
    env.setdefault("TMDB_LOGO_CACHE_DIR", str(CACHE_DIR / "tmdb_logos"))
    env.setdefault("PREFETCH_STATE_PATH", str(CACHE_DIR / "postersplus_prefetch_state.json"))
    env.setdefault("TEXTLESS_TEXT_DETECTION", "false")
    return env


def main() -> int:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / "tmdb_posters").mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / "tmdb_logos").mkdir(parents=True, exist_ok=True)

    out_path = LOG_DIR / "server-8000.out.log"
    err_path = LOG_DIR / "server-8000.err.log"

    stdout = out_path.open("ab", buffering=0)
    stderr = err_path.open("ab", buffering=0)

    creationflags = 0
    if os.name == "nt":
        creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP
        creationflags |= subprocess.DETACHED_PROCESS

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ],
        cwd=str(ROOT),
        env=_build_env(),
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=stderr,
        close_fds=True,
        creationflags=creationflags,
    )
    print(proc.pid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
