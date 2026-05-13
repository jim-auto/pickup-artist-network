from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import requests

from collector import (
    build_following_observations,
    collect_x_profile_snapshots,
    extract_snapshot_from_html,
    extract_x_following_handles_from_hrefs,
    extract_x_profile_snapshot,
    merge_generated_snapshots,
    preserve_missing_generated_snapshots,
    load_playwright_cookies,
    load_dotenv_values,
    load_x_login_credentials,
)


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
              window.__INITIAL_STATE__ = {"users":{"entities":{"1":{"description":"Official city updates &amp; notices","entities":{"url":{"urls":[{"expanded_url":"https://city.example.jp/"}]}},"location":"Example City","name":"City Example","screen_name":"city_example","profile_image_url_https":"https://pbs.twimg.com/profile_images/example_normal.jpg"}}}};
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
        self.assertEqual(
            snapshot["icon_url"],
            "https://pbs.twimg.com/profile_images/example_400x400.jpg",
        )
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

    def test_extract_x_following_handles_from_hrefs_filters_reserved_paths(self) -> None:
        handles = extract_x_following_handles_from_hrefs(
            [
                "/home",
                "/city_example",
                "/alpha_user",
                "/alpha_user",
                "/settings/profile",
                "https://x.com/beta_user",
                "/i/flow/login",
                "/search",
            ],
            source_handle="city_example",
        )

        self.assertEqual(handles, ["alpha_user", "beta_user"])

    def test_build_following_observations_maps_only_known_handles(self) -> None:
        observations = build_following_observations(
            "source-account",
            ["alpha_user", "missing_user", "source_user"],
            handle_to_account_id={
                "alpha_user": "alpha-account",
                "source_user": "source-account",
            },
            source_url="https://x.com/source_user",
            following_url="https://x.com/source_user/following",
        )

        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["target"], "alpha-account")
        self.assertEqual(observations[0]["type"], "follow")
        self.assertTrue(observations[0]["needs_review"])

    def test_load_dotenv_values_parses_simple_key_value_pairs(self) -> None:
        with TemporaryDirectory() as temp_dir:
            dotenv_path = Path(temp_dir) / ".env"
            dotenv_path.write_text(
                "TWITTER_USERNAME=test_user\n"
                "TWITTER_PASSWORD='secret value'\n"
                "# comment\n"
                "EMPTY=\n",
                encoding="utf-8",
            )

            values = load_dotenv_values(dotenv_path)

        self.assertEqual(values["TWITTER_USERNAME"], "test_user")
        self.assertEqual(values["TWITTER_PASSWORD"], "secret value")
        self.assertEqual(values["EMPTY"], "")

    def test_load_x_login_credentials_prefers_dotenv_when_env_missing(self) -> None:
        with TemporaryDirectory() as temp_dir:
            dotenv_path = Path(temp_dir) / ".env"
            dotenv_path.write_text(
                "TWITTER_USERNAME=test_user\nTWITTER_PASSWORD=test_pass\n",
                encoding="utf-8",
            )

            username, password = load_x_login_credentials(dotenv_path)

        self.assertEqual(username, "test_user")
        self.assertEqual(password, "test_pass")

    def test_load_playwright_cookies_converts_selenium_cookie_shape(self) -> None:
        with TemporaryDirectory() as temp_dir:
            cookie_path = Path(temp_dir) / "cookies.json"
            cookie_path.write_text(
                '[{"name":"auth_token","value":"abc","domain":".x.com","path":"/","httpOnly":true,"secure":true,"sameSite":"None","expiry":1893456000}]',
                encoding="utf-8",
            )

            cookies = load_playwright_cookies(cookie_path)

        self.assertEqual(len(cookies), 1)
        self.assertEqual(cookies[0]["name"], "auth_token")
        self.assertEqual(cookies[0]["domain"], ".x.com")
        self.assertEqual(cookies[0]["sameSite"], "None")
        self.assertEqual(cookies[0]["expires"], 1893456000.0)

    def test_preserve_missing_generated_snapshots_keeps_configured_accounts(self) -> None:
        existing_snapshots = [
            {"account_id": "alpha", "summary": "old alpha"},
            {"account_id": "beta", "summary": "old beta"},
            {"account_id": "gamma", "summary": "old gamma"},
        ]
        fresh_snapshots = [
            {"account_id": "alpha", "summary": "new alpha"},
        ]

        preserved = preserve_missing_generated_snapshots(
            existing_snapshots,
            fresh_snapshots,
            configured_account_ids={"alpha", "beta"},
        )

        self.assertEqual(preserved, [{"account_id": "beta", "summary": "old beta"}])

    def test_merge_generated_snapshots_keeps_largest_follower_count(self) -> None:
        merged = merge_generated_snapshots(
            [
                {
                    "account_id": "alpha",
                    "profile_url": "https://x.com/alpha",
                    "icon_url": "https://example.com/alpha.jpg",
                    "follower_count": 0,
                    "collector": {"type": "x_profile"},
                },
                {
                    "account_id": "alpha",
                    "profile_url": "https://x.com/alpha",
                    "follower_count": 1234,
                    "collector": {"type": "x_profile"},
                },
            ]
        )

        self.assertEqual(merged[0]["icon_url"], "https://example.com/alpha.jpg")
        self.assertEqual(merged[0]["follower_count"], 1234)

    def test_collect_x_profile_snapshots_falls_back_when_profile_fetch_fails(self) -> None:
        with patch("collector.fetch_page", side_effect=requests.RequestException("boom")):
            snapshots = collect_x_profile_snapshots(
                [{"account_id": "alpha", "url": "https://x.com/alpha", "label": "test profile"}],
                continue_on_error=True,
            )

        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0]["account_id"], "alpha")
        self.assertEqual(snapshots[0]["links"], ["https://x.com/alpha"])
        self.assertIn("Profile fetch failed during this run", snapshots[0]["review_notes"])


if __name__ == "__main__":
    unittest.main()
