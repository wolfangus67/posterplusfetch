from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from fetching import (
    PrefetchService,
    PrefetchStore,
    _compute_next_run,
    _extract_catalogs,
    normalize_addon_url,
    quality_tokens_from_streams,
    stream_is_debrid_cached,
)


class PrefetchHelpersTest(unittest.TestCase):
    def test_normalize_addon_url_handles_manifest_configure_and_stremio(self) -> None:
        self.assertEqual(
            normalize_addon_url("https://example.com/stremio/v1/manifest.json"),
            "https://example.com/stremio/v1",
        )
        self.assertEqual(
            normalize_addon_url("https://example.com/stremio/v1/configure"),
            "https://example.com/stremio/v1",
        )
        self.assertEqual(
            normalize_addon_url("stremio://example.com/stremio/v1/manifest.json"),
            "https://example.com/stremio/v1",
        )

    def test_extract_catalogs_keeps_movie_series_and_mixed_only(self) -> None:
        manifest = {
            "name": "Demo addon",
            "catalogs": [
                {"id": "top", "name": "Top", "type": "movie", "pageSize": 50},
                {"id": "shows", "name": "Shows", "type": "series", "pageSize": 75, "showInHome": True},
                {"id": "mixed", "name": "Mixed", "type": "mixed", "pageSize": 25},
                {"id": "search", "name": "Search", "type": "movie", "isSearch": True},
                {"id": "unsupported", "type": "user"},
            ],
        }
        catalogs = _extract_catalogs(manifest, "https://example.com/addon", "Demo addon")
        self.assertEqual(len(catalogs), 3)
        self.assertEqual(catalogs[0]["name"], "Top")
        self.assertEqual(catalogs[1]["type"], "series")
        self.assertTrue(catalogs[1]["home"])
        self.assertEqual(catalogs[2]["type"], "mixed")

    def test_compute_next_run_uses_next_available_day(self) -> None:
        config = {
            "schedule_enabled": True,
            "run_time": "08:30",
            "days": [0, 2, 4],
        }
        now = datetime(2026, 1, 1, 7, 0, 0)  # Thursday (weekday 3)
        result = _compute_next_run(config, now=now)
        self.assertIsNotNone(result)
        self.assertTrue(result.endswith("08:30:00"))

    def test_prefetch_store_persists_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = PrefetchStore(path)
            store.update_config(run_time="09:45", days=[1, 3, 5])
            store.add_log("info", "hello", count=2)
            snapshot = store.snapshot()
            self.assertEqual(snapshot["config"]["run_time"], "09:45")
            self.assertEqual(snapshot["config"]["days"], [1, 3, 5])
            self.assertEqual(snapshot["config"]["stream_manifest_url"], "")
            self.assertEqual(snapshot["config"]["per_catalog_limit"], 100)
            self.assertEqual(snapshot["logs"][-1]["message"], "hello")
            self.assertTrue(path.exists())

    def test_catalog_selection_preserves_home_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PrefetchStore(Path(tmp) / "state.json")
            service = PrefetchService(store)
            catalogs = [
                {
                    "key": "addon|movie|home",
                    "id": "home",
                    "type": "movie",
                    "name": "Home",
                    "pageSize": 50,
                    "home": True,
                    "selected": True,
                }
            ]
            import asyncio
            saved = asyncio.run(service.save_catalog_selection(catalogs))
            self.assertTrue(saved[0]["home"])
            self.assertTrue(store.snapshot()["catalogs"]["loaded"][0]["home"])

    def test_stream_cache_detection_is_conservative(self) -> None:
        self.assertTrue(stream_is_debrid_cached({"name": "AIOStreams\nCached", "title": "Movie.2160p.WEB-DL"}))
        self.assertTrue(stream_is_debrid_cached({"behaviorHints": {"cached": True}}))
        self.assertFalse(stream_is_debrid_cached({"name": "AIOStreams\nUncached", "title": "Movie.2160p"}))
        self.assertFalse(stream_is_debrid_cached({"title": "Movie.2160p.WEB-DL"}))
        self.assertTrue(stream_is_debrid_cached({"name": "🌎 French\n1080p", "url": "https://example.com/stream"}))
        self.assertFalse(stream_is_debrid_cached({"name": "🟢 Scrape Summary", "streamData": {"type": "statistic"}}))

    def test_quality_tokens_are_extracted_from_cached_streams(self) -> None:
        tokens = quality_tokens_from_streams(
            [
                {
                    "name": "AIOStreams\nCached",
                    "title": "Demo.2026.2160p.WEB-DL.DV.Atmos.mkv",
                }
            ]
        )
        self.assertEqual(tokens, ["4K", "WEBDL", "DV", "ATMOS"])

    def test_cached_cloud_streams_without_source_default_to_web(self) -> None:
        tokens = quality_tokens_from_streams(
            [
                {
                    "name": "🌎 French\n1080p",
                    "title": "Yoroi",
                    "url": "https://example.com/proxy",
                }
            ]
        )
        self.assertEqual(tokens, ["1080P", "WEBDL"])


if __name__ == "__main__":
    unittest.main()
