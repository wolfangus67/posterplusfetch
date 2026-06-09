import unittest

from tmdb import image_language_order


class ImageLanguageOrderTests(unittest.TestCase):
    def test_native_content_keeps_native_language_first(self):
        self.assertEqual(
            image_language_order("fr", "fr", "native_if_original_english"),
            ["fr", "en"],
        )

    def test_foreign_content_prefers_english_then_original(self):
        for original_language in ("ko", "ja", "ru", "zh"):
            with self.subTest(original_language=original_language):
                self.assertEqual(
                    image_language_order(
                        "fr", original_language, "native_if_original_english"
                    ),
                    ["en", original_language],
                )

    def test_existing_priorities_are_unchanged(self):
        self.assertEqual(
            image_language_order("fr", "ja", "native_original"),
            ["fr", "ja"],
        )
        self.assertEqual(
            image_language_order("fr", "ja", "original_native"),
            ["ja", "fr"],
        )
        self.assertEqual(
            image_language_order("fr", "ja", "native_text"),
            ["fr"],
        )

    def test_duplicate_languages_are_only_tried_once(self):
        self.assertEqual(
            image_language_order("en", "en", "native_if_original_english"),
            ["en"],
        )


if __name__ == "__main__":
    unittest.main()
