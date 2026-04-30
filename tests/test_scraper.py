from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from graph_model import query_relations
from scraper import (
    approve_review_candidate,
    build_graph_from_sources,
    build_growth_targets_payload,
    candidate_to_observation,
    format_growth_targets_output,
    format_review_candidate_decisions_output,
    format_review_candidates_output,
    format_query_output,
    generate_review_candidates,
    load_generated_snapshots,
    load_seed_entities,
    merge_snapshots_by_account,
    set_review_candidate_decision,
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

    def test_generate_review_candidates_uses_reference_for_content_to_location(self) -> None:
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
        self.assertEqual(payload["candidates"][0]["type"], "reference")

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

    def test_candidate_to_observation_marks_approved_manual_interpretation(self) -> None:
        observation = candidate_to_observation(
            {
                "id": "alpha__beta__reference__profile_text",
                "source": "alpha",
                "target": "beta",
                "type": "reference",
                "basis": "profile_text",
                "matched_text": "Beta",
                "evidence_text": "Alpha profile references Beta.",
                "source_urls": ["https://example.com/source"],
                "confidence": 0.4,
            },
            approval_note="Reviewed manually.",
        )

        self.assertEqual(observation["target"], "beta")
        self.assertEqual(observation["type"], "reference")
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
            "id": "alpha__beta__reference__profile_text",
            "source": "alpha",
            "target": "beta",
            "type": "reference",
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

    def test_approve_review_candidate_seeds_new_manual_snapshot_from_reference(self) -> None:
        manual_snapshots: list[dict[str, object]] = []
        candidate = {
            "id": "alpha__beta__reference",
            "source": "alpha",
            "target": "beta",
            "type": "reference",
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
            "id": "alpha__beta__reference__profile_text",
            "source": "alpha",
            "target": "beta",
            "type": "reference",
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
