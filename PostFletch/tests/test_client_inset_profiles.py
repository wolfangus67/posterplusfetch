from pathlib import Path
import unittest

import main


class ClientInsetProfileTests(unittest.TestCase):
    def test_stremio_tv_nuvio_defaults_to_flush_edges(self):
        cfg = main.build_request_config({"primary_client": "stremio_tv_nuvio"})
        self.assertEqual(cfg.bar_bottom_inset, 0.0)
        self.assertEqual(cfg.sash_badge_inset, 0.0)

    def test_stremio_desktop_web_uses_cropped_client_defaults(self):
        cfg = main.build_request_config({"primary_client": "stremio_desktop_web"})
        self.assertEqual(cfg.bar_bottom_inset, 0.007)
        self.assertEqual(cfg.sash_badge_inset, 0.004)

    def test_explicit_insets_override_client_profile(self):
        cfg = main.build_request_config({
            "primary_client": "stremio_tv_nuvio",
            "bar_bottom_inset": "0.006",
            "sash_badge_inset": "0.003",
        })
        self.assertEqual(cfg.bar_bottom_inset, 0.006)
        self.assertEqual(cfg.sash_badge_inset, 0.003)

    def test_configurator_preserves_insets_when_loading_presets(self):
        html = Path("configurator.html").read_text(encoding="utf-8")

        self.assertIn(
            '<option value="stremio_tv_nuvio" selected>Stremio TV, Nuvio, Plex, Jellyfin</option>', html
        )
        self.assertIn(
            '<option value="stremio_desktop_web">Stremio Desktop/Web</option>',
            html,
        )
        self.assertIn(
            "stremio_tv_nuvio:    { bar: 0.000, notch: 0.000 }", html
        )
        self.assertIn(
            "stremio_desktop_web: { bar: 0.007, notch: 0.004 }", html
        )
        presets = html.split("const PRESETS = [", 1)[1].split("];", 1)[0]

        self.assertIn("preserveClientInsets: true", html)
        self.assertIn("!preserveClientInsets && p.has('bar_bottom_inset')", html)
        self.assertIn("!preserveClientInsets && p.has('sash_badge_inset')", html)
        self.assertNotIn("bar_bottom_inset=", presets)
        self.assertNotIn("sash_badge_inset=", presets)
        self.assertNotIn("sash_badge_notch_offset", html)


if __name__ == "__main__":
    unittest.main()
