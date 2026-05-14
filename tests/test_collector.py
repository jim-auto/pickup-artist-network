from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import requests

from collector import (
    build_following_observations,
    collect_x_profile_snapshots,
    extract_snapshot_from_html,
    extract_x_following_handles_from_hrefs,
    extract_x_profile_snapshot,
    fetch_x_api_user_details,
    fetch_x_web_user_details,
    load_x_api_bearer_token,
    load_x_web_profile_skip,
    merge_generated_snapshots,
    merge_refreshed_snapshots_into_existing,
    merge_x_api_user_details_into_snapshot,
    merge_x_web_user_details_into_snapshot,
    preserve_missing_generated_snapshots,
    refresh_missing_x_web_profiles,
    save_x_web_profile_skip,
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

    def test_load_x_api_bearer_token_reads_dotenv(self) -> None:
        with TemporaryDirectory() as temp_dir:
            dotenv_path = Path(temp_dir) / ".env"
            dotenv_path.write_text("X_BEARER_TOKEN=test_token\n", encoding="utf-8")

            token = load_x_api_bearer_token(dotenv_path)

        self.assertEqual(token, "test_token")

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

    def test_merge_refreshed_snapshots_updates_only_matching_entries(self) -> None:
        merged = merge_refreshed_snapshots_into_existing(
            [
                {
                    "account_id": "beta",
                    "profile_url": "https://x.com/beta",
                    "follower_count": 0,
                    "collector": {"type": "x_profile"},
                },
                {
                    "account_id": "alpha",
                    "profile_url": "https://x.com/alpha",
                    "follower_count": 0,
                    "collector": {"type": "x_profile"},
                },
            ],
            [
                {
                    "account_id": "alpha",
                    "profile_url": "https://x.com/alpha",
                    "follower_count": 1234,
                    "collector": {"type": "x_web_profile"},
                }
            ],
        )

        self.assertEqual([snapshot["account_id"] for snapshot in merged], ["beta", "alpha"])
        self.assertEqual(merged[1]["follower_count"], 1234)
        self.assertEqual(
            merged[1]["collector"]["sources"],
            [{"type": "x_profile"}, {"type": "x_web_profile"}],
        )

    def test_x_web_profile_skip_roundtrip(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            skip_path = Path(tmp_dir) / "skip.json"
            payload = {"profiles": {"alpha": {"handle": "alpha", "reason": "empty_or_unavailable"}}}
            save_x_web_profile_skip(payload, skip_path)

            self.assertEqual(load_x_web_profile_skip(skip_path), payload)

    def test_fetch_x_api_user_details_batches_public_metrics(self) -> None:
        response = Mock()
        response.json.return_value = {
            "data": [
                {
                    "username": "alpha_user",
                    "public_metrics": {"followers_count": 1234},
                    "profile_image_url": "https://pbs.twimg.com/profile_images/example_normal.jpg",
                }
            ]
        }
        response.raise_for_status.return_value = None
        with patch("collector.requests.get", return_value=response) as get_mock:
            details = fetch_x_api_user_details(
                [{"account_id": "alpha", "url": "https://x.com/alpha_user"}],
                "bearer",
            )

        self.assertEqual(details["alpha"]["public_metrics"]["followers_count"], 1234)
        self.assertEqual(get_mock.call_args.kwargs["headers"]["Authorization"], "Bearer bearer")

    def test_merge_x_api_user_details_into_snapshot_updates_icon_and_followers(self) -> None:
        snapshot = {
            "account_id": "alpha",
            "profile_url": "https://x.com/alpha_user",
            "links": ["https://x.com/alpha_user"],
            "summary": "Old summary",
            "review_notes": "Old note.",
            "follower_count": 0,
        }
        merged = merge_x_api_user_details_into_snapshot(
            snapshot,
            {
                "username": "alpha_user",
                "name": "Alpha",
                "description": "Fresh public description",
                "location": "Tokyo",
                "profile_image_url": "https://pbs.twimg.com/profile_images/example_normal.jpg",
                "public_metrics": {"followers_count": 1234},
            },
        )

        self.assertEqual(merged["follower_count"], 1234)
        self.assertEqual(merged["icon_url"], "https://pbs.twimg.com/profile_images/example_400x400.jpg")
        self.assertEqual(merged["summary"], "Fresh public description")
        self.assertIn("Location: Tokyo", merged["profile_text"])

    def test_fetch_x_web_user_details_parses_user_by_screen_name(self) -> None:
        response = Mock()
        response.status_code = 200
        response.ok = True
        response.json.return_value = {
            "data": {
                "user": {
                    "result": {
                        "__typename": "User",
                        "core": {"screen_name": "alpha_user"},
                        "legacy": {"followers_count": 1234},
                    }
                }
            }
        }
        response.raise_for_status.return_value = None
        with patch("collector.requests.get", return_value=response) as get_mock:
            details = fetch_x_web_user_details(
                "alpha_user",
                {"authorization": "Bearer token", "x-csrf-token": "ct0"},
            )

        self.assertEqual(details["legacy"]["followers_count"], 1234)
        self.assertIn("UserByScreenName", get_mock.call_args.args[0])

    def test_fetch_x_web_user_details_raises_on_rate_limit(self) -> None:
        response = Mock()
        response.status_code = 429
        response.ok = False
        with patch("collector.requests.get", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "rate limited"):
                fetch_x_web_user_details("alpha_user", {"authorization": "Bearer token"})

    def test_merge_x_web_user_details_into_snapshot_updates_followers(self) -> None:
        merged = merge_x_web_user_details_into_snapshot(
            {"account_id": "alpha", "links": [], "follower_count": 0},
            {
                "core": {"screen_name": "alpha_user", "name": "Alpha"},
                "legacy": {"description": "Fresh bio", "followers_count": 1234},
                "avatar": {"image_url": "https://pbs.twimg.com/profile_images/example_normal.jpg"},
                "location": {"location": "Tokyo"},
            },
        )

        self.assertEqual(merged["follower_count"], 1234)
        self.assertEqual(merged["icon_url"], "https://pbs.twimg.com/profile_images/example_400x400.jpg")
        self.assertIn("UserByScreenName", merged["review_notes"])

    def test_refresh_missing_x_web_profiles_records_unavailable_and_skips_next_time(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            config_path = base / "x_profile_sources.json"
            output_path = base / "source_snapshots.generated.json"
            skip_path = base / "x_web_profile_skip.json"
            config_path.write_text(
                """
                [
                  {"account_id": "alpha", "url": "https://x.com/alpha_user"},
                  {"account_id": "beta", "url": "https://x.com/beta_user"}
                ]
                """,
                encoding="utf-8",
            )
            output_path.write_text(
                """
                [
                  {"account_id": "alpha", "profile_url": "https://x.com/alpha_user", "links": [], "follower_count": 0},
                  {"account_id": "beta", "profile_url": "https://x.com/beta_user", "links": [], "follower_count": 0}
                ]
                """,
                encoding="utf-8",
            )

            seed_entities = [
                {"id": "alpha", "type": "person"},
                {"id": "beta", "type": "person"},
            ]
            with patch("collector.load_seed_entities", return_value=seed_entities), patch(
                "collector.resolve_x_cookie_file", return_value=base / "cookies.json"
            ), patch("collector.x_web_cookie_headers", return_value={"authorization": "Bearer token"}), patch(
                "collector.fetch_x_web_user_details",
                side_effect=[
                    {},
                    {
                        "__typename": "User",
                        "core": {"screen_name": "beta_user", "name": "Beta"},
                        "legacy": {"followers_count": 1234},
                    },
                ],
            ) as fetch_mock:
                refreshed = refresh_missing_x_web_profiles(
                    x_profile_config_path=config_path,
                    output_path=output_path,
                    skip_file_path=skip_path,
                    pause_seconds=0,
                )

            self.assertEqual([snapshot["account_id"] for snapshot in refreshed], ["beta"])
            self.assertEqual(fetch_mock.call_count, 2)
            self.assertIn("alpha", load_x_web_profile_skip(skip_path)["profiles"])

            with patch("collector.load_seed_entities", return_value=seed_entities), patch(
                "collector.resolve_x_cookie_file", return_value=base / "cookies.json"
            ), patch("collector.x_web_cookie_headers", return_value={"authorization": "Bearer token"}), patch(
                "collector.fetch_x_web_user_details"
            ) as second_fetch_mock:
                second = refresh_missing_x_web_profiles(
                    x_profile_config_path=config_path,
                    output_path=output_path,
                    skip_file_path=skip_path,
                    pause_seconds=0,
                )

            self.assertEqual(second, [])
            second_fetch_mock.assert_not_called()

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
