from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
import unittest

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from prefetcher import compute_next_run, extract_catalogs, normalize_addon_base


class PrefetcherHelpersTest(unittest.TestCase):
    def test_normalize_addon_base_strips_manifest_and_configure(self) -> None:
        self.assertEqual(
            normalize_addon_base("https://example.com/stremio/configure"),
            "https://example.com/stremio",
        )
        self.assertEqual(
            normalize_addon_base("https://example.com/stremio/manifest.json"),
            "https://example.com/stremio",
        )

    def test_extract_catalogs_keeps_movie_and_series_only(self) -> None:
        manifest = {
            "name": "Demo addon",
            "catalogs": [
                {"id": "top", "name": "Top", "type": "movie", "pageSize": 50},
                {"id": "shows", "name": "Shows", "type": "series", "pageSize": 75},
                {"id": "search", "name": "Search", "type": "movie", "isSearch": True},
                {"id": "unsupported", "type": "user"},
            ],
        }
        catalogs = extract_catalogs(manifest, "https://example.com/addon", "Demo addon")
        self.assertEqual(len(catalogs), 2)
        self.assertEqual(catalogs[0]["name"], "Top")
        self.assertEqual(catalogs[1]["type"], "series")

    def test_compute_next_run_uses_next_available_day(self) -> None:
        config = {
            "schedule_enabled": True,
            "run_time": "08:30",
            "days": [0, 2, 4],
        }
        now = datetime(2026, 1, 1, 7, 0, 0)  # Thursday (weekday 3)
        result = compute_next_run(config, now=now)
        self.assertIsNotNone(result)
        self.assertTrue(result.endswith("08:30:00"))


if __name__ == "__main__":
    unittest.main()
