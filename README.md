# Posterfletch

Standalone Prefetch workspace with a **real Flask backend** and a frontend that calls real API endpoints.

## What it does

- loads Stremio-compatible addon manifests
- extracts real catalogs from the manifest
- persists configuration and selected catalogs in `data/state.json`
- runs a background prefetch job
- warms stream URLs when a stream addon is configured
- exposes live status and logs through `/api/status`

## Run

```bash
pip install -r requirements.txt
python app.py
```

Then open:

```text
http://127.0.0.1:8787
```

## API

- `GET /api/status`
- `POST /api/addon/manifest`
- `POST /api/catalogs/load`
- `GET /api/catalogs/selection`
- `POST /api/catalogs/selection`
- `GET /api/schedule`
- `POST /api/schedule`
- `DELETE /api/schedule`
- `POST /api/run`
- `GET /api/logs`
