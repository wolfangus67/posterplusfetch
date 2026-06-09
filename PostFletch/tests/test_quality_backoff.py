import unittest

import main


class QualityBackoffTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.source = main._cfg.QUALITY_SOURCE
        main._quality_source_backoff_until.clear()
        main._quality_source_fail_count.clear()

    def tearDown(self):
        main._cfg.QUALITY_SOURCE = self.source
        main._quality_source_backoff_until.clear()
        main._quality_source_fail_count.clear()

    async def test_failure_creates_short_source_cooldown(self):
        main._cfg.QUALITY_SOURCE = "aiostreams"
        main._record_quality_result(main.FETCH_FAILED)
        self.assertGreater(main._quality_backoff_remaining(), 0)
        self.assertEqual(main._quality_source_fail_count["aiostreams"], 1)

    async def test_concurrent_failures_do_not_skip_escalation_steps(self):
        main._cfg.QUALITY_SOURCE = "aiostreams"
        main._record_quality_result(main.FETCH_FAILED)
        main._record_quality_result(main.FETCH_FAILED)
        self.assertEqual(main._quality_source_fail_count["aiostreams"], 1)

    async def test_success_clears_source_cooldown(self):
        main._cfg.QUALITY_SOURCE = "scraper"
        main._record_quality_result(main.FETCH_FAILED)
        main._record_quality_result([])
        self.assertEqual(main._quality_backoff_remaining(), 0)
        self.assertNotIn("scraper", main._quality_source_fail_count)


if __name__ == "__main__":
    unittest.main()
