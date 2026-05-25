from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts import apply_following_screened as apply_script


class ApplyFollowingScreenedTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old_paths = {
            "SEED_FILE": apply_script.SEED_FILE,
            "X_PROFILE_FILE": apply_script.X_PROFILE_FILE,
            "GENERATED_SNAPSHOT_FILE": apply_script.GENERATED_SNAPSHOT_FILE,
            "FOLLOWING_SCREENED_FILE": apply_script.FOLLOWING_SCREENED_FILE,
        }

    def tearDown(self) -> None:
        for name, value in self._old_paths.items():
            setattr(apply_script, name, value)

    def test_profile_scene_evidence_rejects_followback_only(self) -> None:
        self.assertFalse(
            apply_script._has_profile_scene_evidence(
                {
                    "summary": "認証垢は即フォローバックします",
                    "profile_text": "投資と散歩の記録",
                }
            )
        )
        self.assertTrue(
            apply_script._has_profile_scene_evidence(
                {
                    "summary": "ナンパ系オンラインサロン運営",
                    "profile_text": "恋愛工学の教科書",
                }
            )
        )
        self.assertTrue(
            apply_script._has_profile_scene_evidence(
                {
                    "summary": "講習生として女修行中",
                    "profile_text": "マッチングアプリで経験人数を増やす記録",
                }
            )
        )

    def test_screened_rows_require_profile_scene_evidence(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            seed = base / "seed_entities.txt"
            screened = base / "following_screened.json"
            seed.write_text("person|existing|Existing|existing|real\n", encoding="utf-8")
            screened.write_text(
                json.dumps(
                    [
                        {
                            "account_id": "eligible",
                            "handle": "eligible_pua",
                            "ok_bio_scene": True,
                            "summary": "ナンパ系オンラインサロン運営",
                            "profile_text": "",
                            "follower_count": 10,
                        },
                        {
                            "account_id": "handle-only",
                            "handle": "pua_nor",
                            "ok_bio_scene": True,
                            "summary": "それなりに頑張ります",
                            "profile_text": "",
                            "follower_count": 100,
                        },
                        {
                            "account_id": "existing",
                            "handle": "existing",
                            "ok_bio_scene": True,
                            "summary": "ナンパ師",
                            "profile_text": "",
                            "follower_count": 1000,
                        },
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            apply_script.SEED_FILE = seed
            apply_script.FOLLOWING_SCREENED_FILE = screened

            rows = apply_script._screened_rows(limit=None)

        self.assertEqual([row["account_id"] for row in rows], ["eligible"])

    def test_prune_ineligible_applied_profiles_removes_only_auto_applied_rows(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            seed = base / "seed_entities.txt"
            x_profiles = base / "x_profile_sources.json"
            snapshots = base / "source_snapshots.generated.json"
            screened = base / "following_screened.json"

            seed.write_text(
                "\n".join(
                    [
                        "person|eligible|@eligible|eligible|real",
                        "person|ineligible|@ineligible|ineligible|real",
                        "person|manual|Manual|manual|real",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            x_profiles.write_text(
                json.dumps(
                    [
                        {
                            "account_id": "eligible",
                            "url": "https://x.com/eligible",
                            "label": apply_script.X_PROFILE_LABEL,
                        },
                        {
                            "account_id": "ineligible",
                            "url": "https://x.com/ineligible",
                            "label": apply_script.X_PROFILE_LABEL,
                        },
                        {
                            "account_id": "manual",
                            "url": "https://x.com/manual",
                            "label": "manual",
                        },
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            snapshots.write_text(
                json.dumps(
                    [
                        {
                            "account_id": "source",
                            "observations": [
                                {
                                    "target": "ineligible",
                                    "review_notes": apply_script.NOTE,
                                },
                                {
                                    "target": "manual",
                                    "review_notes": apply_script.NOTE,
                                },
                            ],
                        },
                        {
                            "account_id": "eligible",
                            "collector": {"type": "following_screened_profile"},
                            "observations": [],
                        },
                        {
                            "account_id": "ineligible",
                            "collector": {"type": "following_screened_profile"},
                            "observations": [],
                        },
                        {
                            "account_id": "manual",
                            "collector": {"type": "manual"},
                            "observations": [],
                        },
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            screened.write_text(
                json.dumps(
                    [
                        {
                            "account_id": "eligible",
                            "handle": "eligible",
                            "ok_bio_scene": True,
                            "summary": "ナンパ系オンラインサロン運営",
                            "profile_text": "",
                        },
                        {
                            "account_id": "ineligible",
                            "handle": "ineligible",
                            "ok_bio_scene": True,
                            "summary": "それなりに頑張ります",
                            "profile_text": "",
                        },
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            apply_script.SEED_FILE = seed
            apply_script.X_PROFILE_FILE = x_profiles
            apply_script.GENERATED_SNAPSHOT_FILE = snapshots
            apply_script.FOLLOWING_SCREENED_FILE = screened

            pruned = apply_script._prune_ineligible_applied_profiles()
            seed_text = seed.read_text(encoding="utf-8")
            profile_payload = json.loads(x_profiles.read_text(encoding="utf-8"))
            snapshot_payload = json.loads(snapshots.read_text(encoding="utf-8"))

        self.assertEqual(pruned, {"ineligible"})
        self.assertIn("person|eligible|", seed_text)
        self.assertIn("person|manual|", seed_text)
        self.assertNotIn("person|ineligible|", seed_text)
        self.assertEqual(
            {row["account_id"] for row in profile_payload},
            {"eligible", "manual"},
        )
        self.assertEqual(
            {row["account_id"] for row in snapshot_payload},
            {"source", "eligible", "manual"},
        )
        source = next(row for row in snapshot_payload if row["account_id"] == "source")
        self.assertEqual(source["observations"], [{"target": "manual", "review_notes": apply_script.NOTE}])


if __name__ == "__main__":
    unittest.main()
