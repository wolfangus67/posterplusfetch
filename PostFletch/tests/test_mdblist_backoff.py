import unittest

import main


class MDBListBackoffTests(unittest.TestCase):
    def setUp(self):
        self.server_keys = main._cfg.SERVER_MDBLIST_KEYS
        self.active_key_idx = main._mdblist_active_key_idx
        main._rating_backoff.clear()
        main._rating_fail_count.clear()
        main._mdblist_key_cooldown.clear()

    def tearDown(self):
        main._cfg.SERVER_MDBLIST_KEYS = self.server_keys
        main._mdblist_active_key_idx = self.active_key_idx
        main._rating_backoff.clear()
        main._rating_fail_count.clear()
        main._mdblist_key_cooldown.clear()

    def test_replacement_key_is_not_blocked_by_title_backoff(self):
        title = "tt11347692"
        first_key = main._rating_retry_key(title, "exhausted-key")
        replacement_key = main._rating_retry_key(title, "healthy-key")

        main._rating_backoff[first_key] = 3600.0

        self.assertIn(first_key, main._rating_backoff)
        self.assertNotIn(replacement_key, main._rating_backoff)

    def test_failure_escalation_is_independent_per_key(self):
        title = "tt11347692"
        first_key = main._rating_retry_key(title, "key-1")
        second_key = main._rating_retry_key(title, "key-2")

        main._rating_fail_count[first_key] = 3

        self.assertEqual(main._rating_fail_count[first_key], 3)
        self.assertEqual(main._rating_fail_count.get(second_key, 0), 0)

    def test_rotation_selects_next_healthy_server_key(self):
        main._cfg.SERVER_MDBLIST_KEYS = ["key-1", "key-2"]
        main._mdblist_key_cooldown["key-1"] = 100.0

        selected = main._next_mdblist_server_key("key-1", now=10.0)

        self.assertEqual(selected, "key-2")
        self.assertEqual(main._mdblist_active_key_idx, 1)

    def test_rotation_does_not_replace_query_supplied_key(self):
        main._cfg.SERVER_MDBLIST_KEYS = ["key-1", "key-2"]
        self.assertIsNone(main._next_mdblist_server_key("user-key", now=10.0))

    def test_same_key_and_title_share_retry_state(self):
        self.assertEqual(
            main._rating_retry_key("tt11347692", "key-2"),
            main._rating_retry_key("tt11347692", "key-2"),
        )


class MDBListRateLimitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.server_keys = main._cfg.SERVER_MDBLIST_KEYS
        main._cfg.SERVER_MDBLIST_KEYS = ["server-key-1", "server-key-2"]
        main._rating_backoff.clear()
        main._mdblist_key_cooldown.clear()

    def tearDown(self):
        main._cfg.SERVER_MDBLIST_KEYS = self.server_keys
        main._rating_backoff.clear()
        main._mdblist_key_cooldown.clear()

    async def test_query_key_rate_limit_does_not_select_server_fallback(self):
        result = main._RateLimited(retry_after=60)

        delay, fallback = main._mark_mdblist_rate_limit(
            "tt11347692", "request-key", result
        )

        self.assertEqual(delay, 60)
        self.assertIsNone(fallback)
        self.assertIn("request-key", main._mdblist_key_cooldown)


if __name__ == "__main__":
    unittest.main()
