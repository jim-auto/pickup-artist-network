from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from graph_model import add_edge, query_relations
from scraper import (
    approve_review_candidate,
    build_connection_audit_payload,
    build_graph_density_payload,
    build_graph_from_sources,
    build_thin_candidates_payload,
    build_growth_targets_payload,
    candidate_to_observation,
    format_connection_audit_output,
    format_growth_targets_output,
    format_review_candidate_decisions_output,
    format_review_candidates_output,
    format_thin_candidate_decisions_output,
    format_thin_candidates_output,
    format_query_output,
    generate_review_candidates,
    infer_keyword_cluster_edges,
    infer_profile_bridge_edges,
    load_generated_snapshots,
    load_seed_entities,
    materialize_inferred_social_edges,
    merge_snapshots_by_account,
    set_review_candidate_decision,
    set_thin_candidate_decision,
    set_thin_candidate_decisions,
)


class ScraperSourceSnapshotTests(unittest.TestCase):
    def test_snapshot_observations_and_links_become_edges(self) -> None:
        seed_entities = [
            {"type": "person", "id": "alpha", "name": "Alpha", "aliases": []},
            {"type": "community", "id": "beta", "name": "Beta", "aliases": []},
            {"type": "platform", "id": "note", "name": "note", "aliases": []},
        ]
        snapshots = [
            {
                "account_id": "alpha",
                "profile_url": "https://example.com/alpha/profile",
                "pinned_post_url": "https://example.com/alpha/pinned",
                "icon_url": "https://example.com/alpha/icon.png",
                "links": ["https://note.com/alpha"],
                "observations": [
                    {
                        "target": "beta",
                        "type": "affiliation",
                        "description": "Alpha is introduced as a Beta member.",
                        "source_urls": ["https://example.com/alpha/pinned"],
                        "confidence": 0.66,
                    }
                ],
            }
        ]

        graph = build_graph_from_sources(seed_entities, snapshots)
        edges = {(edge.source, edge.target, edge.type) for edge in graph.edges}
        alpha = next(node for node in graph.nodes if node.id == "alpha")

        self.assertIn(("alpha", "note", "affiliation"), edges)
        self.assertIn(("alpha", "beta", "affiliation"), edges)
        self.assertEqual(alpha.icon_url, "https://example.com/alpha/icon.png")
        self.assertIn("https://example.com/alpha/profile", alpha.source_urls)
        self.assertIn("https://note.com/alpha", alpha.source_urls)

    def test_detected_platform_node_is_created_when_missing(self) -> None:
        seed_entities = [
            {"type": "person", "id": "alpha", "name": "Alpha", "aliases": []},
        ]
        snapshots = [
            {
                "account_id": "alpha",
                "profile_url": "https://example.com/alpha/profile",
                "links": ["https://www.youtube.com/@alpha"],
                "observations": [],
            }
        ]

        graph = build_graph_from_sources(seed_entities, snapshots)
        edges = {(edge.source, edge.target, edge.type) for edge in graph.edges}
        node_ids = {node.id for node in graph.nodes}

        self.assertIn("youtube", node_ids)
        self.assertIn(("alpha", "youtube", "affiliation"), edges)

    def test_note_mu_link_maps_to_note_platform(self) -> None:
        seed_entities = [
            {"type": "person", "id": "alpha", "name": "Alpha", "aliases": []},
        ]
        snapshots = [
            {
                "account_id": "alpha",
                "profile_url": "https://x.com/alpha",
                "links": ["https://note.mu/alpha"],
                "observations": [],
            }
        ]

        graph = build_graph_from_sources(seed_entities, snapshots)
        edges = {(edge.source, edge.target, edge.type) for edge in graph.edges}

        self.assertIn(("alpha", "note", "affiliation"), edges)

    def test_lin_ee_link_maps_to_line_platform(self) -> None:
        seed_entities = [
            {"type": "person", "id": "alpha", "name": "Alpha", "aliases": []},
        ]
        snapshots = [
            {
                "account_id": "alpha",
                "profile_url": "https://x.com/alpha",
                "links": ["https://lin.ee/alpha"],
                "observations": [],
            }
        ]

        graph = build_graph_from_sources(seed_entities, snapshots)
        edges = {(edge.source, edge.target, edge.type) for edge in graph.edges}

        self.assertIn(("alpha", "line", "affiliation"), edges)

    def test_invalid_snapshot_target_is_rejected(self) -> None:
        seed_entities = [
            {"type": "person", "id": "alpha", "name": "Alpha", "aliases": []},
        ]
        snapshots = [
            {
                "account_id": "alpha",
                "profile_url": "https://example.com/alpha/profile",
                "observations": [
                    {
                        "target": "missing-target",
                        "type": "affiliation",
                        "description": "Should fail because the target does not exist.",
                    }
                ],
            }
        ]

        with self.assertRaises(ValueError):
            build_graph_from_sources(seed_entities, snapshots)

    def test_invalid_snapshot_url_scheme_is_rejected(self) -> None:
        seed_entities = [
            {"type": "person", "id": "alpha", "name": "Alpha", "aliases": []},
        ]
        snapshots = [
            {
                "account_id": "alpha",
                "profile_url": "ftp://example.com/alpha/profile",
                "observations": [],
            }
        ]

        with self.assertRaises(ValueError):
            build_graph_from_sources(seed_entities, snapshots)

    def test_review_metadata_propagates_to_graph(self) -> None:
        seed_entities = [
            {"type": "person", "id": "alpha", "name": "Alpha", "aliases": []},
            {"type": "community", "id": "beta", "name": "Beta", "aliases": []},
        ]
        snapshots = [
            {
                "account_id": "alpha",
                "profile_url": "https://example.com/alpha/profile",
                "summary": "Auto summary",
                "needs_review": True,
                "review_notes": "Auto-collected summary needs checking.",
                "observations": [
                    {
                        "target": "beta",
                        "type": "affiliation",
                        "description": "Alpha may be related to Beta.",
                        "needs_review": True,
                        "evidence_kind": "interpretation",
                        "review_notes": "Interpretive relation.",
                    }
                ],
            }
        ]

        graph = build_graph_from_sources(seed_entities, snapshots)
        alpha = next(node for node in graph.nodes if node.id == "alpha")
        edge = graph.edges[0]

        self.assertTrue(alpha.needs_review)
        self.assertEqual(alpha.review_notes, "Auto-collected summary needs checking.")
        self.assertTrue(edge.needs_review)
        self.assertEqual(edge.evidence_kind, "interpretation")
        self.assertEqual(edge.review_notes, "Interpretive relation.")

    def test_manual_snapshot_takes_precedence_over_generated_values(self) -> None:
        manual_snapshots = [
            {
                "account_id": "alpha",
                "profile_url": "manual://alpha",
                "profile_text": "Manual profile text",
                "summary": "Manual summary",
                "links": ["https://x.com/manual_alpha"],
                "review_notes": "Manual note.",
                "observations": [],
            }
        ]
        generated_snapshots = [
            {
                "account_id": "alpha",
                "profile_url": "https://x.com/generated_alpha",
                "profile_text": "Generated profile text",
                "summary": "Generated summary",
                "links": ["https://x.com/generated_alpha"],
                "review_notes": "Generated note.",
                "collector": {"type": "x_profile"},
                "snapshot_origin": "generated",
                "observations": [],
            }
        ]

        merged = merge_snapshots_by_account(manual_snapshots, generated_snapshots)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["profile_text"], "Manual profile text")
        self.assertEqual(merged[0]["summary"], "Manual summary")
        self.assertIn("https://x.com/manual_alpha", merged[0]["links"])
        self.assertIn("https://x.com/generated_alpha", merged[0]["links"])
        self.assertTrue(merged[0]["needs_review"])
        self.assertIn("Generated snapshot differs from manual fields", merged[0]["review_notes"])

    def test_generated_follower_count_fills_manual_snapshot_gap(self) -> None:
        manual_snapshots = [
            {
                "account_id": "alpha",
                "profile_url": "manual://alpha",
                "summary": "Manual summary",
                "observations": [],
            }
        ]
        generated_snapshots = [
            {
                "account_id": "alpha",
                "profile_url": "https://x.com/generated_alpha",
                "summary": "Generated summary",
                "follower_count": 4875,
                "collector": {"type": "x_profile"},
                "snapshot_origin": "generated",
                "observations": [],
            }
        ]

        merged = merge_snapshots_by_account(manual_snapshots, generated_snapshots)

        self.assertEqual(merged[0]["summary"], "Manual summary")
        self.assertEqual(merged[0]["follower_count"], 4875)

    def test_x_web_profile_snapshot_can_override_generated_profile_gap(self) -> None:
        merged = merge_snapshots_by_account(
            [],
            [
                {
                    "account_id": "alpha",
                    "profile_url": "https://x.com/alpha",
                    "summary": "HTML fallback",
                    "follower_count": 0,
                    "collector": {"type": "x_profile"},
                    "snapshot_origin": "generated",
                    "observations": [],
                },
                {
                    "account_id": "alpha",
                    "profile_url": "https://x.com/alpha",
                    "summary": "Authenticated web profile",
                    "follower_count": 5860,
                    "collector": {"type": "x_web_profile"},
                    "snapshot_origin": "generated",
                    "observations": [],
                },
            ],
        )

        self.assertEqual(merged[0]["summary"], "Authenticated web profile")
        self.assertEqual(merged[0]["follower_count"], 5860)

    def test_load_generated_snapshots_combines_collector_output_and_hint_fixtures(self) -> None:
        generated_snapshots = [{"account_id": "alpha", "profile_url": "https://example.com/alpha"}]
        hint_snapshots = [{"account_id": "beta", "profile_url": "manual://generated-hint/beta"}]

        with tempfile.TemporaryDirectory() as tmp_dir:
            generated_path = Path(tmp_dir) / "generated.json"
            hints_path = Path(tmp_dir) / "generated_hints.json"
            with open(generated_path, "w", encoding="utf-8") as handle:
                json.dump(generated_snapshots, handle)
            with open(hints_path, "w", encoding="utf-8") as handle:
                json.dump(hint_snapshots, handle)

            loaded = load_generated_snapshots(generated_path, hints_path)

        self.assertEqual([item["account_id"] for item in loaded], ["alpha", "beta"])

    def test_load_seed_entities_reads_optional_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            seed_path = Path(tmp_dir) / "seed_entities.txt"
            seed_path.write_text(
                "person|alpha|Alpha|A|real\ncommunity|beta|Beta||fictional\n",
                encoding="utf-8",
            )

            entities = load_seed_entities(seed_path)

        self.assertEqual(entities[0]["scope"], "real")
        self.assertEqual(entities[1]["scope"], "fictional")

    def test_build_growth_targets_payload_counts_only_real_seed_entities(self) -> None:
        payload = build_growth_targets_payload(
            [
                {"type": "person", "id": "alpha", "name": "Alpha", "aliases": [], "scope": "real"},
                {"type": "person", "id": "beta", "name": "Beta", "aliases": [], "scope": "fictional"},
                {"type": "community", "id": "gamma", "name": "Gamma", "aliases": [], "scope": "real"},
            ]
        )

        self.assertEqual(payload["headline"]["current"], 1)
        self.assertEqual(payload["headline"]["target"], 1000)
        type_rows = {row["type"]: row for row in payload["types"]}
        self.assertEqual(type_rows["person"]["current"], 1)
        self.assertEqual(type_rows["community"]["current"], 1)
        self.assertEqual(type_rows["content"]["current"], 0)

    def test_format_growth_targets_output_lists_headline_and_ranges(self) -> None:
        output = format_growth_targets_output(
            {
                "headline": {"label": "Real person target", "current": 3, "target": 1000},
                "phases": [{"label": "Phase 1", "real_person_target": 20}],
                "types": [
                    {"type": "person", "current": 3, "target_min": 1000, "target_max": 1000},
                    {"type": "community", "current": 2, "target_min": 8, "target_max": 12},
                ],
            }
        )

        self.assertIn("[OK] Real person target: 3 / 1000", output)
        self.assertIn("- Phase 1: real person target 20", output)
        self.assertIn("- person: 3 / 1000", output)
        self.assertIn("- community: 2 / 8-12", output)

    def test_format_query_output_lists_nodes_and_edges(self) -> None:
        seed_entities = [
            {"type": "person", "id": "alpha", "name": "Alpha", "aliases": []},
            {"type": "community", "id": "beta", "name": "Beta", "aliases": []},
        ]
        snapshots = [
            {
                "account_id": "alpha",
                "profile_url": "https://example.com/alpha/profile",
                "observations": [
                    {
                        "target": "beta",
                        "type": "affiliation",
                        "description": "Alpha is introduced as a Beta member.",
                    }
                ],
            }
        ]

        graph = build_graph_from_sources(seed_entities, snapshots)
        result = query_relations(graph, node_id="alpha", direction="outgoing")
        output = format_query_output(result)

        self.assertIn("[OK] query result: 2 nodes / 1 edges", output)
        self.assertIn("matched: alpha", output)
        self.assertIn("- Alpha [person] (alpha)", output)
        self.assertIn("- Alpha (alpha) -[affiliation]-> Beta (beta)", output)

    def test_generate_review_candidates_turns_generated_mentions_into_review_only_candidates(self) -> None:
        seed_entities = [
            {"type": "person", "id": "alpha", "name": "Alpha", "aliases": []},
            {"type": "person", "id": "beta", "name": "Beta", "aliases": []},
            {"type": "location", "id": "shibuya", "name": "Shibuya", "aliases": []},
        ]
        graph = build_graph_from_sources(seed_entities, [])
        generated_snapshots = [
            {
                "account_id": "alpha",
                "summary": "Alpha profile summary",
                "profile_text": "Alpha plans a joint stream with Beta around Shibuya.",
                "pinned_post_text": "",
                "profile_url": "https://example.com/alpha",
                "pinned_post_url": "https://example.com/alpha/status/1",
                "links": ["https://x.com/alpha"],
            }
        ]

        payload = generate_review_candidates(seed_entities, generated_snapshots, graph)
        candidates = payload["candidates"]

        self.assertEqual(len(candidates), 2)
        self.assertTrue(any(item["target"] == "beta" and item["type"] == "collaboration" for item in candidates))
        self.assertTrue(any(item["target"] == "shibuya" and item["type"] == "activity" for item in candidates))
        self.assertTrue(all(item["needs_review"] for item in candidates))

    def test_materialize_inferred_social_edges_promotes_profile_mention(self) -> None:
        seed_entities = [
            {"type": "person", "id": "alpha", "name": "Alpha", "aliases": []},
            {"type": "person", "id": "beta", "name": "Beta", "aliases": ["beta_guy"]},
        ]
        graph = build_graph_from_sources(seed_entities, [])
        generated_snapshots = [
            {
                "account_id": "alpha",
                "summary": "",
                "profile_text": "shoutout to @beta_guy for the clip.",
                "pinned_post_text": "",
                "profile_url": "https://x.com/alpha",
                "pinned_post_url": "",
                "links": [],
            }
        ]
        before = generate_review_candidates(seed_entities, generated_snapshots, graph, None)
        self.assertTrue(
            any(
                item["source"] == "alpha" and item["target"] == "beta" and item["type"] == "profile_mention"
                for item in before["candidates"]
            )
        )

        added = materialize_inferred_social_edges(graph, seed_entities, generated_snapshots, None)
        self.assertGreaterEqual(added, 1)
        self.assertTrue(
            any(
                edge.source == "alpha" and edge.target == "beta" and edge.type == "profile_mention"
                for edge in graph.edges
            )
        )

        after = generate_review_candidates(seed_entities, generated_snapshots, graph, None)
        self.assertFalse(
            any(
                item["source"] == "alpha" and item["target"] == "beta" and item["type"] == "profile_mention"
                for item in after["candidates"]
            )
        )

    def test_materialize_inferred_social_edges_promotes_cjk_location_activity(self) -> None:
        seed_entities = [
            {
                "type": "person",
                "id": "leopard-nanpa",
                "name": "レオパ",
                "aliases": ["Leopard_nanpa"],
            },
            {"type": "location", "id": "miso", "name": "味噌", "aliases": ["miso", "みそ", "味噌m"]},
        ]
        graph = build_graph_from_sources(seed_entities, [])
        generated_snapshots = [
            {
                "account_id": "leopard-nanpa",
                "summary": "味噌/23年9月〜/アラサー",
                "profile_text": "レオパ (@Leopard_nanpa)\n味噌/23年9月〜/アラサー",
                "pinned_post_text": "",
                "profile_url": "https://x.com/Leopard_nanpa",
                "pinned_post_url": "",
                "links": [],
            }
        ]

        added = materialize_inferred_social_edges(graph, seed_entities, generated_snapshots, None)

        self.assertEqual(added, 1)
        self.assertTrue(
            any(
                edge.source == "leopard-nanpa"
                and edge.target == "miso"
                and edge.type == "activity"
                and edge.needs_review
                for edge in graph.edges
            )
        )
        edge = next(
            edge
            for edge in graph.edges
            if edge.source == "leopard-nanpa" and edge.target == "miso" and edge.type == "activity"
        )
        self.assertIn("Materialized generated candidate", edge.review_notes)
        self.assertNotIn("not part of the canonical graph", edge.review_notes)

    def test_generate_review_candidates_can_infer_monetization_for_content(self) -> None:
        seed_entities = [
            {"type": "person", "id": "alpha", "name": "Alpha", "aliases": []},
            {"type": "content", "id": "guide-01", "name": "Guide 01", "aliases": []},
        ]
        graph = build_graph_from_sources(seed_entities, [])
        generated_snapshots = [
            {
                "account_id": "alpha",
                "profile_text": "Alpha links readers to Guide 01 as the main product funnel.",
                "profile_url": "https://example.com/alpha",
                "links": [],
            }
        ]

        payload = generate_review_candidates(seed_entities, generated_snapshots, graph)

        self.assertEqual(len(payload["candidates"]), 1)
        self.assertEqual(payload["candidates"][0]["type"], "monetization")

    def test_generate_review_candidates_uses_profile_mention_for_content_to_location(self) -> None:
        seed_entities = [
            {"type": "content", "id": "guide-01", "name": "Guide 01", "aliases": []},
            {"type": "location", "id": "shibuya", "name": "Shibuya", "aliases": []},
        ]
        graph = build_graph_from_sources(seed_entities, [])
        generated_snapshots = [
            {
                "account_id": "guide-01",
                "summary": "Guide 01 introduces Shibuya as a featured district.",
                "profile_url": "https://example.com/guide-01",
                "links": [],
            }
        ]

        payload = generate_review_candidates(seed_entities, generated_snapshots, graph)

        self.assertEqual(len(payload["candidates"]), 1)
        self.assertEqual(payload["candidates"][0]["type"], "profile_mention")

    def test_generate_review_candidates_consolidates_same_relation_across_bases(self) -> None:
        seed_entities = [
            {"type": "person", "id": "alpha", "name": "Alpha", "aliases": []},
            {"type": "location", "id": "shibuya", "name": "Shibuya", "aliases": []},
        ]
        graph = build_graph_from_sources(seed_entities, [])
        generated_snapshots = [
            {
                "account_id": "alpha",
                "summary": "Alpha works in Shibuya.",
                "profile_text": "Alpha posts from Shibuya every week.",
                "profile_url": "https://example.com/alpha",
                "links": [],
            }
        ]

        payload = generate_review_candidates(seed_entities, generated_snapshots, graph)

        self.assertEqual(len(payload["candidates"]), 1)
        self.assertEqual(payload["candidates"][0]["id"], "alpha__shibuya__activity")
        self.assertEqual(payload["candidates"][0]["type"], "activity")
        self.assertEqual(payload["candidates"][0]["basis"], "summary, profile_text")
        self.assertIn("summary, profile_text", payload["candidates"][0]["review_notes"])

    def test_generate_review_candidates_accepts_short_cjk_aliases(self) -> None:
        seed_entities = [
            {"type": "person", "id": "alpha", "name": "Alpha", "aliases": []},
            {"type": "location", "id": "shinjuku", "name": "Shinjuku", "aliases": ["新宿"]},
        ]
        graph = build_graph_from_sources(seed_entities, [])
        generated_snapshots = [
            {
                "account_id": "alpha",
                "profile_text": "Alpha posts field notes from 新宿 every weekend.",
                "profile_url": "https://example.com/alpha",
                "links": [],
            }
        ]

        payload = generate_review_candidates(seed_entities, generated_snapshots, graph)

        self.assertEqual(len(payload["candidates"]), 1)
        self.assertEqual(payload["candidates"][0]["target"], "shinjuku")
        self.assertEqual(payload["candidates"][0]["matched_text"], "新宿")

    def test_format_review_candidates_output_lists_candidate_context(self) -> None:
        seed_entities = [
            {"type": "person", "id": "alpha", "name": "Alpha", "aliases": []},
            {"type": "person", "id": "beta", "name": "Beta", "aliases": []},
        ]
        payload = {
            "generated_at": "2026-04-25T00:00:00+00:00",
            "candidates": [
                {
                    "id": "alpha__beta__collaboration__profile_text",
                    "source": "alpha",
                    "target": "beta",
                    "type": "collaboration",
                    "basis": "profile_text",
                    "matched_text": "Beta",
                    "review_notes": "Generated review candidate.",
                }
            ],
        }

        output = format_review_candidates_output(payload, seed_entities)

        self.assertIn("[OK] review candidates: 1", output)
        self.assertIn("Alpha (alpha) -[collaboration]-> Beta (beta)", output)
        self.assertIn("basis=profile_text", output)
        self.assertIn("match=Beta", output)

    def test_format_review_candidate_decisions_output_lists_decision_context(self) -> None:
        seed_entities = [
            {"type": "person", "id": "alpha", "name": "Alpha", "aliases": []},
            {"type": "person", "id": "beta", "name": "Beta", "aliases": []},
        ]
        payload = {
            "updated_at": "2026-04-25T00:00:00+00:00",
            "decisions": {
                "alpha__beta__collaboration__profile_text": {
                    "candidate_id": "alpha__beta__collaboration__profile_text",
                    "status": "dismissed",
                    "source": "alpha",
                    "target": "beta",
                    "type": "collaboration",
                    "basis": "profile_text",
                    "note": "already reviewed",
                    "updated_at": "2026-04-25T00:00:00+00:00",
                }
            },
        }

        output = format_review_candidate_decisions_output(payload, seed_entities)

        self.assertIn("[OK] candidate decisions: 1", output)
        self.assertIn("dismissed: Alpha (alpha) -[collaboration]-> Beta (beta)", output)
        self.assertIn("basis=profile_text", output)
        self.assertIn("note: already reviewed", output)

    def test_build_thin_candidates_payload_prioritizes_low_signal_outliers(self) -> None:
        seed_entities = [
            {"type": "person", "id": "idol", "name": "@idol", "aliases": []},
            {"type": "person", "id": "street", "name": "ストナン講師", "aliases": []},
        ]
        snapshots = [
            {
                "account_id": "idol",
                "profile_url": "https://x.com/idol",
                "summary": "Official music account.",
                "follower_count": 100000,
            },
            {
                "account_id": "street",
                "profile_url": "https://x.com/street",
                "summary": "ストナン講習をしています。",
                "follower_count": 10,
            },
        ]
        graph = build_graph_from_sources(seed_entities, snapshots)

        payload = build_thin_candidates_payload(graph)

        self.assertEqual([candidate["id"] for candidate in payload["candidates"]], ["idol"])
        self.assertEqual(payload["candidates"][0]["priority"], "high")
        self.assertIn("high-follower outlier", payload["candidates"][0]["reasons"])

    def test_build_thin_candidates_payload_treats_dating_app_terms_as_relevant(self) -> None:
        seed_entities = [
            {"type": "person", "id": "dating-app-tips", "name": "@dating_app_tips", "aliases": []},
            {"type": "person", "id": "idol", "name": "@idol", "aliases": []},
        ]
        snapshots = [
            {
                "account_id": "dating-app-tips",
                "profile_url": "https://x.com/tips",
                "summary": "TinderTips とタップル攻略を発信しています。",
                "follower_count": 20000,
            },
            {
                "account_id": "idol",
                "profile_url": "https://x.com/idol",
                "summary": "Official music account.",
                "follower_count": 100000,
            },
        ]
        graph = build_graph_from_sources(seed_entities, snapshots)

        payload = build_thin_candidates_payload(graph)

        self.assertEqual([candidate["id"] for candidate in payload["candidates"]], ["idol"])

    def test_build_thin_candidates_payload_treats_romanized_street_handle_as_relevant(self) -> None:
        seed_entities = [
            {"type": "person", "id": "street-handle", "name": "@K_suto_nan", "aliases": ["K_suto_nan"]},
            {"type": "person", "id": "idol", "name": "@idol", "aliases": []},
        ]
        snapshots = [
            {
                "account_id": "street-handle",
                "profile_url": "https://x.com/K_suto_nan",
                "summary": "Diary account.",
                "follower_count": 20000,
            },
            {
                "account_id": "idol",
                "profile_url": "https://x.com/idol",
                "summary": "Official music account.",
                "follower_count": 100000,
            },
        ]
        graph = build_graph_from_sources(seed_entities, snapshots)

        payload = build_thin_candidates_payload(graph)

        self.assertEqual([candidate["id"] for candidate in payload["candidates"]], ["idol"])

    def test_build_thin_candidates_payload_treats_english_street_handle_as_relevant(self) -> None:
        seed_entities = [
            {"type": "person", "id": "vegeta-street", "name": "V", "aliases": ["vegeta_street"]},
            {"type": "person", "id": "idol", "name": "@idol", "aliases": []},
        ]
        snapshots = [
            {
                "account_id": "vegeta-street",
                "profile_url": "https://x.com/vegeta_street",
                "summary": "Diary account.",
                "follower_count": 20000,
            },
            {
                "account_id": "idol",
                "profile_url": "https://x.com/idol",
                "summary": "Official music account.",
                "follower_count": 100000,
            },
        ]
        graph = build_graph_from_sources(seed_entities, snapshots)

        payload = build_thin_candidates_payload(graph)

        self.assertEqual([candidate["id"] for candidate in payload["candidates"]], ["idol"])

    def test_build_thin_candidates_payload_treats_rojou_terms_as_relevant(self) -> None:
        seed_entities = [
            {"type": "person", "id": "street-profile", "name": "路上メモ", "aliases": ["rojou_ski"]},
            {"type": "person", "id": "idol", "name": "@idol", "aliases": []},
        ]
        snapshots = [
            {
                "account_id": "street-profile",
                "profile_url": "https://x.com/rojou_ski",
                "summary": "Diary account.",
                "follower_count": 20000,
            },
            {
                "account_id": "idol",
                "profile_url": "https://x.com/idol",
                "summary": "Official music account.",
                "follower_count": 100000,
            },
        ]
        graph = build_graph_from_sources(seed_entities, snapshots)

        payload = build_thin_candidates_payload(graph)

        self.assertEqual([candidate["id"] for candidate in payload["candidates"]], ["idol"])

    def test_build_thin_candidates_payload_treats_nannpa_typo_as_relevant(self) -> None:
        seed_entities = [
            {"type": "person", "id": "nannpa-profile", "name": "mi", "aliases": ["nannpashitai"]},
            {"type": "person", "id": "idol", "name": "@idol", "aliases": []},
        ]
        snapshots = [
            {
                "account_id": "nannpa-profile",
                "profile_url": "https://x.com/nannpashitai",
                "summary": "Diary account.",
                "follower_count": 20000,
            },
            {
                "account_id": "idol",
                "profile_url": "https://x.com/idol",
                "summary": "Official music account.",
                "follower_count": 100000,
            },
        ]
        graph = build_graph_from_sources(seed_entities, snapshots)

        payload = build_thin_candidates_payload(graph)

        self.assertEqual([candidate["id"] for candidate in payload["candidates"]], ["idol"])

    def test_build_thin_candidates_payload_treats_solid_relevant_neighbor_as_relevant(self) -> None:
        seed_entities = [
            {"type": "person", "id": "main", "name": "Main", "aliases": []},
            {"type": "person", "id": "side", "name": "Side", "aliases": []},
            {"type": "person", "id": "idol", "name": "@idol", "aliases": []},
        ]
        snapshots = [
            {
                "account_id": "main",
                "profile_url": "https://x.com/main",
                "summary": "ストナン講習をしています。",
                "follower_count": 20000,
            },
            {
                "account_id": "side",
                "profile_url": "https://x.com/side",
                "summary": "Diary account.",
                "follower_count": 20000,
                "observations": [
                    {
                        "target": "main",
                        "type": "affiliation",
                        "description": "Public profile links the main account.",
                        "confidence": 0.9,
                    }
                ],
            },
            {
                "account_id": "idol",
                "profile_url": "https://x.com/idol",
                "summary": "Official music account.",
                "follower_count": 100000,
            },
        ]
        graph = build_graph_from_sources(seed_entities, snapshots)

        payload = build_thin_candidates_payload(graph)

        self.assertEqual([candidate["id"] for candidate in payload["candidates"]], ["idol"])

    def test_build_thin_candidates_payload_does_not_propagate_relevance_over_assistive_edges(
        self,
    ) -> None:
        seed_entities = [
            {"type": "person", "id": "main", "name": "Main", "aliases": []},
            {"type": "person", "id": "side", "name": "Side", "aliases": []},
        ]
        snapshots = [
            {
                "account_id": "main",
                "profile_url": "https://x.com/main",
                "summary": "ストナン講習をしています。",
                "follower_count": 20000,
            },
            {
                "account_id": "side",
                "profile_url": "https://x.com/side",
                "summary": "Diary account.",
                "follower_count": 20000,
            },
        ]
        graph = build_graph_from_sources(seed_entities, snapshots)
        add_edge(
            graph,
            {
                "source": "side",
                "target": "main",
                "type": "affiliation",
                "description": "Auto bridge.",
                "confidence": 0.23,
                "evidence_kind": "interpretation",
                "needs_review": True,
                "review_notes": "Profile bridge auto-edge for low-degree node coverage.",
            },
        )

        payload = build_thin_candidates_payload(graph)

        self.assertEqual([candidate["id"] for candidate in payload["candidates"]], ["side"])
        self.assertEqual(payload["candidates"][0]["solid_degree"], 0)
        self.assertEqual(payload["candidates"][0]["assistive_degree"], 1)

    def test_build_thin_candidates_payload_can_filter_score_and_limit(self) -> None:
        seed_entities = [
            {"type": "person", "id": "large", "name": "@large", "aliases": []},
            {"type": "person", "id": "small", "name": "@small", "aliases": []},
            {"type": "person", "id": "medium", "name": "@medium", "aliases": []},
        ]
        snapshots = [
            {
                "account_id": "large",
                "profile_url": "https://x.com/large",
                "summary": "Official music account.",
                "follower_count": 100000,
            },
            {
                "account_id": "small",
                "profile_url": "https://x.com/small",
                "summary": "Diary account.",
                "follower_count": 10,
            },
            {
                "account_id": "medium",
                "profile_url": "https://x.com/medium",
                "summary": "Official account.",
                "follower_count": 1000,
            },
        ]
        graph = build_graph_from_sources(seed_entities, snapshots)

        payload = build_thin_candidates_payload(graph, min_score=80, limit=1)

        self.assertEqual([candidate["id"] for candidate in payload["candidates"]], ["large"])

    def test_thin_candidates_score_bridge_only_nodes_by_solid_degree(self) -> None:
        seed_entities = [
            {"type": "person", "id": "idol", "name": "@idol", "aliases": []},
            {"type": "person", "id": "neighbor", "name": "@neighbor", "aliases": []},
        ]
        snapshots = [
            {
                "account_id": "idol",
                "profile_url": "https://x.com/idol",
                "icon_url": "https://example.com/idol.jpg",
                "summary": "X profile for idol.",
                "follower_count": 100000,
            },
            {
                "account_id": "neighbor",
                "profile_url": "https://x.com/neighbor",
                "summary": "Official account.",
            },
        ]
        graph = build_graph_from_sources(seed_entities, snapshots)
        add_edge(
            graph,
            {
                "source": "idol",
                "target": "neighbor",
                "type": "affiliation",
                "description": "Auto bridge.",
                "confidence": 0.23,
                "evidence_kind": "interpretation",
                "needs_review": True,
                "review_notes": "Profile bridge auto-edge for low-degree node coverage.",
            },
        )

        payload = build_thin_candidates_payload(graph)
        candidate = payload["candidates"][0]

        self.assertEqual(candidate["id"], "idol")
        self.assertEqual(candidate["degree"], 1)
        self.assertEqual(candidate["solid_degree"], 0)
        self.assertEqual(candidate["assistive_degree"], 1)
        self.assertEqual(candidate["score"], 106)
        self.assertIn("no solid account edges", candidate["reasons"])
        self.assertIn("1 auto bridge edges", candidate["reasons"])

    def test_thin_candidate_decision_keep_removes_candidate_from_queue(self) -> None:
        seed_entities = [{"type": "person", "id": "idol", "name": "@idol", "aliases": []}]
        snapshots = [
            {
                "account_id": "idol",
                "profile_url": "https://x.com/idol",
                "summary": "Official music account.",
                "follower_count": 100000,
            }
        ]
        graph = build_graph_from_sources(seed_entities, snapshots)
        decisions_payload = {"updated_at": "", "decisions": {}}

        decision = set_thin_candidate_decision(
            decisions_payload,
            graph,
            "idol",
            status="keep",
            note="Relevant despite sparse profile text.",
        )
        payload = build_thin_candidates_payload(graph, decisions_payload=decisions_payload)

        self.assertEqual(decision["node_id"], "idol")
        self.assertEqual(decision["status"], "keep")
        self.assertEqual(payload["candidates"], [])

    def test_thin_candidate_decisions_can_mark_multiple_nodes(self) -> None:
        seed_entities = [
            {"type": "person", "id": "idol", "name": "@idol", "aliases": []},
            {"type": "person", "id": "news", "name": "@news", "aliases": []},
        ]
        snapshots = [
            {
                "account_id": "idol",
                "profile_url": "https://x.com/idol",
                "summary": "Official music account.",
                "follower_count": 100000,
            },
            {
                "account_id": "news",
                "profile_url": "https://x.com/news",
                "summary": "Breaking news account.",
                "follower_count": 50000,
            },
        ]
        graph = build_graph_from_sources(seed_entities, snapshots)
        decisions_payload = {"updated_at": "", "decisions": {}}

        decisions = set_thin_candidate_decisions(
            decisions_payload,
            graph,
            ["idol", "news", "idol"],
            status="exclude",
            note="Off-topic batch.",
        )
        payload = build_thin_candidates_payload(graph, decisions_payload=decisions_payload)

        self.assertEqual([decision["node_id"] for decision in decisions], ["idol", "news"])
        self.assertEqual(decisions_payload["decisions"]["idol"]["status"], "exclude")
        self.assertEqual(decisions_payload["decisions"]["news"]["note"], "Off-topic batch.")
        self.assertEqual(payload["candidates"], [])

    def test_format_thin_candidate_outputs_include_context(self) -> None:
        candidates_output = format_thin_candidates_output(
            {
                "generated_at": "2026-05-24T00:00:00+00:00",
                "candidates": [
                    {
                        "id": "idol",
                        "name": "@idol",
                        "priority": "high",
                        "score": 98,
                        "follower_count": 100000,
                        "degree": 0,
                        "reasons": ["missing relevance keyword", "no account edges"],
                    }
                ],
            }
        )
        decisions_output = format_thin_candidate_decisions_output(
            {
                "updated_at": "2026-05-24T00:00:00+00:00",
                "decisions": {
                    "idol": {
                        "node_id": "idol",
                        "name": "@idol",
                        "status": "exclude",
                        "score": 98,
                        "note": "Off-topic.",
                        "updated_at": "2026-05-24T00:00:00+00:00",
                    }
                },
            }
        )

        self.assertIn("[OK] thin candidates: 1", candidates_output)
        self.assertIn("high score=98: @idol (idol)", candidates_output)
        self.assertIn("reasons=missing relevance keyword, no account edges", candidates_output)
        self.assertIn("[OK] thin candidate decisions: 1", decisions_output)
        self.assertIn("exclude: @idol (idol) score=98", decisions_output)
        self.assertIn("note: Off-topic.", decisions_output)

    def test_graph_density_payload_counts_solid_and_assistive_edges(self) -> None:
        seed_entities = [
            {"type": "person", "id": "main", "name": "Main", "aliases": [], "scope": "real"},
            {"type": "person", "id": "side", "name": "Side", "aliases": [], "scope": "real"},
            {"type": "person", "id": "lonely", "name": "Lonely", "aliases": [], "scope": "real"},
        ]
        snapshots = [
            {
                "account_id": "main",
                "profile_url": "https://x.com/main",
                "summary": "ストナン講習をしています。",
                "observations": [
                    {
                        "target": "side",
                        "type": "follow",
                        "description": "Manual follow relation.",
                        "confidence": 0.9,
                        "evidence_kind": "fact",
                    }
                ],
            },
            {
                "account_id": "side",
                "profile_url": "https://x.com/side",
                "summary": "Side account.",
            },
            {
                "account_id": "lonely",
                "profile_url": "https://x.com/lonely",
                "summary": "X profile for lonely.",
            },
        ]
        graph = build_graph_from_sources(seed_entities, snapshots)
        add_edge(
            graph,
            {
                "source": "lonely",
                "target": "main",
                "type": "affiliation",
                "description": "Auto bridge.",
                "confidence": 0.23,
                "evidence_kind": "interpretation",
                "needs_review": True,
                "review_notes": "Profile bridge auto-edge for low-degree node coverage.",
            },
        )

        density = build_graph_density_payload(graph)
        growth = build_growth_targets_payload(seed_entities, graph=graph)
        output = format_growth_targets_output(growth)

        self.assertEqual(density["solid_edge_count"], 1)
        self.assertEqual(density["assistive_edge_count"], 1)
        self.assertEqual(density["person_person_solid_edges"], 1)
        self.assertEqual(density["person_person_assistive_edges"], 1)
        self.assertEqual(density["relevant_bridge_only"], 0)
        self.assertIn("density", growth)
        self.assertIn("solid_ratio=", output)
        self.assertIn("relevant persons:", output)

    def test_profile_bridge_skips_nodes_that_already_have_solid_degree(self) -> None:
        seed_entities = [
            {"type": "person", "id": "hub", "name": "Hub", "aliases": []},
            {"type": "person", "id": "peer", "name": "Peer", "aliases": []},
            {"type": "person", "id": "spare", "name": "Spare", "aliases": []},
        ]
        snapshots = [
            {
                "account_id": "hub",
                "profile_url": "https://x.com/hub",
                "summary": "ストナンと講習とナンパの記録。",
                "follower_count": 5000,
                "observations": [
                    {
                        "target": "peer",
                        "type": "follow",
                        "description": "Solid follow.",
                        "confidence": 0.9,
                    },
                    {
                        "target": "spare",
                        "type": "follow",
                        "description": "Solid follow 2.",
                        "confidence": 0.9,
                    },
                ],
            },
            {
                "account_id": "peer",
                "profile_url": "https://x.com/peer",
                "summary": "ストナン講習アカウント。",
                "follower_count": 100,
            },
            {
                "account_id": "spare",
                "profile_url": "https://x.com/spare",
                "summary": "ナンパと講習メモ。",
                "follower_count": 100,
            },
        ]
        graph = build_graph_from_sources(seed_entities, snapshots)
        before = len(graph.edges)
        added = infer_profile_bridge_edges(graph)
        # hub already has solid_degree >= target, so no bridge edges are required.
        self.assertEqual(added, 0)
        self.assertEqual(len(graph.edges), before)

    def test_connection_audit_payload_splits_solid_assistive_and_review_edges(self) -> None:
        seed_entities = [
            {"type": "person", "id": "alpha", "name": "Alpha", "aliases": []},
            {"type": "person", "id": "beta", "name": "Beta", "aliases": []},
            {"type": "location", "id": "miso", "name": "Miso", "aliases": []},
        ]
        snapshots = [
            {
                "account_id": "alpha",
                "profile_url": "https://x.com/alpha",
                "summary": "Alpha account.",
                "observations": [
                    {
                        "target": "miso",
                        "type": "activity",
                        "description": "Manual profile states Miso field.",
                        "confidence": 0.9,
                        "evidence_kind": "fact",
                    }
                ],
            },
            {
                "account_id": "beta",
                "profile_url": "https://x.com/beta",
                "summary": "Beta account.",
                "observations": [
                    {
                        "target": "miso",
                        "type": "activity",
                        "description": "Generated profile text mentions Miso.",
                        "confidence": 0.42,
                        "evidence_kind": "interpretation",
                        "needs_review": True,
                        "review_notes": "Auto profile text match.",
                    }
                ],
            },
        ]
        graph = build_graph_from_sources(seed_entities, snapshots)
        add_edge(
            graph,
            {
                "source": "beta",
                "target": "miso",
                "type": "affiliation",
                "description": "Auto bridge.",
                "confidence": 0.23,
                "evidence_kind": "interpretation",
                "needs_review": True,
                "review_notes": "Profile bridge auto-edge for low-degree node coverage.",
            },
        )

        payload = build_connection_audit_payload(graph, "miso")
        output = format_connection_audit_output(payload)

        self.assertEqual(payload["summary"]["total"], 3)
        self.assertEqual(payload["summary"]["solid"], 2)
        self.assertEqual(payload["summary"]["assistive"], 1)
        self.assertEqual(payload["summary"]["needs_review"], 2)
        self.assertEqual(payload["summary"]["evidence_kind"]["fact"], 1)
        self.assertEqual(payload["summary"]["evidence_kind"]["interpretation"], 2)
        self.assertIn("[OK] connection audit: Miso (miso) [location]", output)
        self.assertIn("solid=2 assistive=1 needs_review=2", output)
        self.assertIn("assistive/needs_review/interpretation incoming affiliation: Beta", output)

    def test_candidate_to_observation_marks_approved_manual_interpretation(self) -> None:
        observation = candidate_to_observation(
            {
                "id": "alpha__beta__profile_mention__profile_text",
                "source": "alpha",
                "target": "beta",
                "type": "profile_mention",
                "basis": "profile_text",
                "matched_text": "Beta",
                "evidence_text": "Alpha profile references Beta.",
                "source_urls": ["https://example.com/source"],
                "confidence": 0.4,
            },
            approval_note="Reviewed manually.",
        )

        self.assertEqual(observation["target"], "beta")
        self.assertEqual(observation["type"], "profile_mention")
        self.assertEqual(observation["evidence_kind"], "interpretation")
        self.assertFalse(observation["needs_review"])
        self.assertIn("Reviewed manually.", observation["review_notes"])

    def test_approve_review_candidate_appends_observation_to_manual_snapshot(self) -> None:
        manual_snapshots = [
            {
                "account_id": "alpha",
                "profile_url": "manual://alpha",
                "summary": "Manual summary",
                "observations": [],
            }
        ]
        candidate = {
            "id": "alpha__beta__profile_mention__profile_text",
            "source": "alpha",
            "target": "beta",
            "type": "profile_mention",
            "basis": "profile_text",
            "matched_text": "Beta",
            "evidence_text": "Alpha profile references Beta.",
            "source_urls": ["https://example.com/source"],
            "confidence": 0.4,
        }

        observation = approve_review_candidate(manual_snapshots, candidate)

        self.assertEqual(observation["target"], "beta")
        self.assertEqual(len(manual_snapshots[0]["observations"]), 1)
        self.assertEqual(manual_snapshots[0]["observations"][0]["target"], "beta")

    def test_approve_review_candidate_seeds_new_manual_snapshot_from_profile_mention(self) -> None:
        manual_snapshots: list[dict[str, object]] = []
        candidate = {
            "id": "alpha__beta__profile_mention",
            "source": "alpha",
            "target": "beta",
            "type": "profile_mention",
            "basis": "summary, profile_text",
            "matched_text": "Beta",
            "evidence_text": "Alpha profile references Beta.",
            "source_urls": ["https://example.com/source"],
            "confidence": 0.4,
        }
        reference_snapshot = {
            "account_id": "alpha",
            "profile_url": "https://example.com/alpha",
            "pinned_post_url": "https://example.com/alpha/status/1",
            "profile_text": "Alpha profile text",
            "pinned_post_text": "Alpha pinned text",
            "summary": "Alpha summary",
            "links": ["https://x.com/alpha"],
        }

        approve_review_candidate(
            manual_snapshots,
            candidate,
            reference_snapshot=reference_snapshot,
        )

        self.assertEqual(manual_snapshots[0]["profile_url"], "https://example.com/alpha")
        self.assertEqual(manual_snapshots[0]["summary"], "Alpha summary")
        self.assertEqual(manual_snapshots[0]["links"], ["https://x.com/alpha"])

    def test_approve_review_candidate_rejects_duplicate_observation(self) -> None:
        candidate = {
            "id": "alpha__beta__profile_mention__profile_text",
            "source": "alpha",
            "target": "beta",
            "type": "profile_mention",
            "basis": "profile_text",
            "matched_text": "Beta",
            "evidence_text": "Alpha profile references Beta.",
            "source_urls": ["https://example.com/source"],
            "confidence": 0.4,
        }
        manual_snapshots = [
            {
                "account_id": "alpha",
                "profile_url": "manual://alpha",
                "summary": "Manual summary",
                "observations": [candidate_to_observation(candidate)],
            }
        ]

        with self.assertRaises(ValueError):
            approve_review_candidate(manual_snapshots, candidate)

    def test_build_graph_from_sources_normalizes_legacy_reference_following_edges(self) -> None:
        seed_entities = [
            {"type": "person", "id": "alpha", "name": "Alpha", "aliases": []},
            {"type": "person", "id": "beta", "name": "Beta", "aliases": []},
        ]
        snapshots = [
            {
                "account_id": "alpha",
                "profile_url": "https://x.com/alpha",
                "observations": [
                    {
                        "target": "beta",
                        "type": "reference",
                        "description": "Authenticated X following list shows this account follows @beta_user.",
                        "source_urls": ["https://x.com/alpha/following"],
                        "confidence": 0.64,
                    }
                ],
            }
        ]

        graph = build_graph_from_sources(seed_entities, snapshots)

        self.assertTrue(any(edge.source == "alpha" and edge.target == "beta" and edge.type == "follow" for edge in graph.edges))

    def test_dismissed_candidate_is_filtered_from_regenerated_candidates(self) -> None:
        seed_entities = [
            {"type": "person", "id": "alpha", "name": "Alpha", "aliases": []},
            {"type": "person", "id": "beta", "name": "Beta", "aliases": []},
        ]
        graph = build_graph_from_sources(seed_entities, [])
        generated_snapshots = [
            {
                "account_id": "alpha",
                "profile_text": "Alpha plans a joint stream with Beta.",
                "profile_url": "https://example.com/alpha",
                "links": [],
            }
        ]
        initial_payload = generate_review_candidates(seed_entities, generated_snapshots, graph)
        decisions_payload = {"updated_at": "", "decisions": {}}
        set_review_candidate_decision(
            decisions_payload,
            initial_payload["candidates"][0],
            status="dismissed",
            note="not useful",
        )

        filtered_payload = generate_review_candidates(
            seed_entities,
            generated_snapshots,
            graph,
            decisions_payload=decisions_payload,
        )

        self.assertEqual(filtered_payload["candidates"], [])

    def test_grouped_decision_suppresses_future_candidate_group(self) -> None:
        seed_entities = [
            {"type": "person", "id": "alpha", "name": "Alpha", "aliases": []},
            {"type": "location", "id": "shibuya", "name": "Shibuya", "aliases": []},
        ]
        graph = build_graph_from_sources(seed_entities, [])
        generated_snapshots = [
            {
                "account_id": "alpha",
                "summary": "Alpha works in Shibuya.",
                "profile_text": "Alpha posts from Shibuya every week.",
                "profile_url": "https://example.com/alpha",
                "links": [],
            }
        ]
        decisions_payload = {
            "updated_at": "",
            "decisions": {
                "alpha__shibuya__activity": {
                    "candidate_id": "alpha__shibuya__activity",
                    "status": "approved",
                    "source": "alpha",
                    "target": "shibuya",
                    "type": "activity",
                    "basis": "profile_text, summary",
                }
            },
        }

        filtered_payload = generate_review_candidates(
            seed_entities,
            generated_snapshots,
            graph,
            decisions_payload=decisions_payload,
        )

        self.assertEqual(filtered_payload["candidates"], [])

    def test_short_ambiguous_person_alias_is_not_a_review_match(self) -> None:
        seed_entities = [
            {"type": "person", "id": "alpha", "name": "Alpha", "aliases": []},
            {"type": "person", "id": "m-teacher", "name": "M氏@講師", "aliases": ["M氏"]},
        ]
        graph = build_graph_from_sources(seed_entities, [])
        generated_snapshots = [
            {
                "account_id": "alpha",
                "summary": "Alpha is taking #こりらM氏講習 and mentions M字 in profile.",
                "profile_text": "Alpha is taking #こりらM氏講習 and mentions M字 in profile.",
                "profile_url": "https://example.com/alpha",
                "links": [],
            }
        ]

        payload = generate_review_candidates(seed_entities, generated_snapshots, graph)

        self.assertEqual(payload["candidates"], [])

    def test_deshi_phrase_materializes_as_influence_edge(self) -> None:
        seed_entities = [
            {"type": "person", "id": "konamon", "name": "こなモン", "aliases": []},
            {"type": "person", "id": "atsutaro", "name": "あつ太郎の本音bot", "aliases": ["あつ太郎"]},
        ]
        graph = build_graph_from_sources(seed_entities, [])
        generated_snapshots = [
            {
                "account_id": "konamon",
                "summary": "35歳。職歴空白。あつ太郎の弟子。",
                "profile_text": "こなモン (@konamon_nampa)\n35歳。職歴空白。あつ太郎の弟子。",
                "profile_url": "https://example.com/konamon",
                "links": [],
            }
        ]

        added = materialize_inferred_social_edges(graph, seed_entities, generated_snapshots)

        self.assertEqual(added, 1)
        self.assertTrue(
            any(edge.source == "konamon" and edge.target == "atsutaro" and edge.type == "influence" for edge in graph.edges)
        )

    def test_mbh_keyword_cluster_prefers_gureran_anchor(self) -> None:
        seed_entities = [
            {"type": "person", "id": "gureran-m", "name": "まーぼーMBH@ナンパ講師", "aliases": ["gureran_m"]},
            {"type": "person", "id": "gureran-m3", "name": "まーぼー@MBHナンパコーチ", "aliases": ["gureran_m3"]},
            {"type": "person", "id": "ds-mbh", "name": "@DS_MBH", "aliases": ["DS_MBH"]},
            {"type": "person", "id": "amenbo-mbh", "name": "@amenbo_MBH", "aliases": ["amenbo_MBH"]},
            {"type": "person", "id": "mbh-hal", "name": "はる@MBH", "aliases": ["mbh_hal"]},
            {"type": "person", "id": "mayuge-mbh", "name": "まゆげ@MBH", "aliases": ["mayuge_mbh"]},
        ]
        graph = build_graph_from_sources(seed_entities, [])

        infer_keyword_cluster_edges(graph)

        gureran_neighbors = {
            edge.target if edge.source == "gureran-m" else edge.source
            for edge in graph.edges
            if edge.source == "gureran-m" or edge.target == "gureran-m"
        }
        self.assertGreaterEqual(len(gureran_neighbors & {"ds-mbh", "amenbo-mbh", "mbh-hal", "mayuge-mbh"}), 3)

    def test_set_review_candidate_decision_persists_candidate_metadata(self) -> None:
        decisions_payload = {"updated_at": "", "decisions": {}}
        candidate = {
            "id": "alpha__beta__collaboration__profile_text",
            "source": "alpha",
            "target": "beta",
            "type": "collaboration",
            "basis": "profile_text",
            "matched_text": "Beta",
            "evidence_text": "Alpha plans a joint stream with Beta.",
            "source_urls": ["https://example.com/alpha"],
        }

        decision = set_review_candidate_decision(
            decisions_payload,
            candidate,
            status="dismissed",
            note="already reviewed",
        )

        self.assertEqual(decision["candidate_id"], candidate["id"])
        self.assertEqual(decision["source"], "alpha")
        self.assertEqual(decision["target"], "beta")
        self.assertEqual(decision["type"], "collaboration")
        self.assertEqual(decision["basis"], "profile_text")
        self.assertEqual(decision["matched_text"], "Beta")
        self.assertEqual(decision["evidence_text"], "Alpha plans a joint stream with Beta.")
        self.assertEqual(decision["source_urls"], ["https://example.com/alpha"])


if __name__ == "__main__":
    unittest.main()
