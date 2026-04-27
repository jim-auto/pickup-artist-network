from __future__ import annotations

import unittest

from collector import extract_snapshot_from_html, extract_x_profile_snapshot


class CollectorTests(unittest.TestCase):
    def test_extract_snapshot_from_html_collects_summary_and_platform_links(self) -> None:
        html = """
        <html>
          <head>
            <title>Example Profile</title>
            <meta name="description" content="Public page for an example profile.">
          </head>
          <body>
            <h1>Example Profile</h1>
            <p>This page links to public platforms.</p>
            <a href="https://note.com/example">note</a>
            <a href="https://www.youtube.com/@example">youtube</a>
          </body>
        </html>
        """

        snapshot = extract_snapshot_from_html(
            "example",
            "https://example.com/profile",
            html,
            label="test",
            fetched_url="https://example.com/profile",
        )

        self.assertEqual(snapshot["account_id"], "example")
        self.assertEqual(snapshot["summary"], "Public page for an example profile.")
        self.assertTrue(snapshot["needs_review"])
        self.assertIn("https://note.com/example", snapshot["links"])
        self.assertIn("https://www.youtube.com/@example", snapshot["links"])

    def test_extract_snapshot_from_html_applies_platform_allowlist_and_skips_self_links(self) -> None:
        html = """
        <html>
          <head><title>About Example</title></head>
          <body>
            <a href="https://www.youtube.com/about/">self youtube</a>
            <a href="https://twitter.com/example">x</a>
            <a href="https://www.instagram.com/example/">instagram</a>
            <a href="https://note.com/example">note</a>
            <a href="https://twitter.com/example-login">login-ish</a>
          </body>
        </html>
        """

        snapshot = extract_snapshot_from_html(
            "youtube",
            "https://www.youtube.com/about/",
            html,
            label="test",
            fetched_url="https://www.youtube.com/about/",
            allowed_platforms=["x", "instagram"],
            deny_url_keywords=["login"],
        )

        self.assertEqual(
            snapshot["links"],
            ["https://twitter.com/example", "https://www.instagram.com/example/"],
        )

    def test_extract_x_profile_snapshot_compresses_profile_and_keeps_x_link(self) -> None:
        html = """
        <html>
          <head>
            <title>City Example (@city_example) / X</title>
            <meta property="og:description" content="Official city account sharing updates, notices, and community information for residents and visitors." />
          </head>
        </html>
        """

        snapshot = extract_x_profile_snapshot(
            "city-example",
            "https://x.com/city_example",
            html,
            label="official X profile",
            fetched_url="https://x.com/city_example",
        )

        self.assertEqual(snapshot["account_id"], "city-example")
        self.assertEqual(snapshot["links"], ["https://x.com/city_example"])
        self.assertTrue(snapshot["needs_review"])
        self.assertIn("Official city account", snapshot["summary"])
        self.assertLessEqual(len(snapshot["summary"]), 180)

    def test_extract_x_profile_snapshot_uses_embedded_user_data_when_meta_is_sparse(self) -> None:
        html = """
        <html>
          <body>
            <script>
              window.__INITIAL_STATE__ = {"users":{"entities":{"1":{"description":"Official city updates &amp; notices","entities":{"url":{"urls":[{"expanded_url":"https://city.example.jp/"}]}},"location":"Example City","name":"City Example","screen_name":"city_example"}}}};
            </script>
          </body>
        </html>
        """

        snapshot = extract_x_profile_snapshot(
            "city-example",
            "https://x.com/city_example",
            html,
            label="official X profile",
            fetched_url="https://x.com/city_example",
        )

        self.assertEqual(snapshot["summary"], "Official city updates & notices")
        self.assertIn("City Example (@city_example)", snapshot["profile_text"])
        self.assertIn("Location: Example City", snapshot["profile_text"])
        self.assertIn("https://city.example.jp/", snapshot["links"])
        self.assertNotIn("Pinned post parsing is not automated yet.", snapshot["profile_text"])

    def test_extract_x_profile_snapshot_builds_identity_fallback_without_bare_url(self) -> None:
        snapshot = extract_x_profile_snapshot(
            "city-example",
            "https://x.com/city_example",
            "<html><body></body></html>",
            label="official X profile",
            fetched_url="https://x.com/city_example",
        )

        self.assertEqual(snapshot["summary"], "X profile for @city_example.")
        self.assertEqual(snapshot["profile_text"], "@city_example")
        self.assertNotEqual(snapshot["summary"], "https://x.com/city_example")

    def test_extract_x_profile_snapshot_adds_configured_pinned_post_hint(self) -> None:
        profile_html = """
        <html>
          <body>
            <script>
              window.__INITIAL_STATE__ = {"users":{"entities":{"1":{"description":"Official city updates","location":"Example City","name":"City Example","screen_name":"city_example"}}}};
            </script>
          </body>
        </html>
        """
        pinned_html = """
        <html>
          <head>
            <title>City Example on X: "Pinned update about this weekend's field schedule." / X</title>
            <meta property="og:description" content="Pinned update about this weekend's field schedule." />
            <link rel="canonical" href="https://x.com/city_example/status/12345" />
          </head>
        </html>
        """

        snapshot = extract_x_profile_snapshot(
            "city-example",
            "https://x.com/city_example",
            profile_html,
            label="official X profile",
            fetched_url="https://x.com/city_example",
            pinned_post_url="https://x.com/city_example/status/12345",
            pinned_post_html=pinned_html,
            pinned_post_fetched_url="https://x.com/city_example/status/12345",
        )

        self.assertEqual(snapshot["pinned_post_url"], "https://x.com/city_example/status/12345")
        self.assertEqual(snapshot["pinned_post_text"], "Pinned update about this weekend's field schedule.")
        self.assertIn("configured X status URL", snapshot["review_notes"])

    def test_extract_x_profile_snapshot_notes_missing_configured_pinned_post(self) -> None:
        snapshot = extract_x_profile_snapshot(
            "city-example",
            "https://x.com/city_example",
            "<html><body></body></html>",
            label="official X profile",
            fetched_url="https://x.com/city_example",
        )

        self.assertEqual(snapshot["pinned_post_url"], "")
        self.assertEqual(snapshot["pinned_post_text"], "")
        self.assertIn("configure pinned_post_url", snapshot["review_notes"])


if __name__ == "__main__":
    unittest.main()
