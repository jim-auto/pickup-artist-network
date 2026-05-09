from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from graph_model import (
    GraphData,
    add_edge,
    add_node,
    export_html,
    export_networkx_metrics,
    export_sqlite,
    query_relations,
    render_html,
)


class GraphModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = GraphData()
        add_node(
            self.graph,
            {
                "id": "alpha",
                "type": "person",
                "name": "Alpha",
                "aliases": ["A"],
                "description": "Alpha person",
                "icon_url": "https://example.com/alpha.png",
                "source_urls": ["https://example.com/alpha"],
                "confidence": 0.8,
                "needs_review": True,
                "review_notes": "Needs a second pass.",
            },
        )
        add_node(
            self.graph,
            {
                "id": "beta",
                "type": "community",
                "name": "Beta Community",
                "aliases": [],
                "description": "Beta group",
                "source_urls": ["https://example.com/beta"],
                "confidence": 0.7,
            },
        )
        add_edge(
            self.graph,
            {
                "source": "alpha",
                "target": "beta",
                "type": "affiliation",
                "description": "Alpha belongs to Beta",
                "source_urls": ["https://example.com/edge"],
                "confidence": 0.6,
                "evidence_kind": "interpretation",
                "needs_review": True,
                "review_notes": "Interpretive example edge.",
            },
        )

    def test_query_relations_returns_neighbor_context(self) -> None:
        result = query_relations(self.graph, search_term="Alpha")
        node_ids = {node["id"] for node in result["nodes"]}
        self.assertEqual(node_ids, {"alpha", "beta"})
        self.assertEqual(len(result["edges"]), 1)
        self.assertEqual(result["matched_node_ids"], ["alpha"])

    def test_query_relations_can_filter_outgoing_edges(self) -> None:
        add_node(
            self.graph,
            {
                "id": "gamma",
                "type": "person",
                "name": "Gamma",
                "aliases": [],
                "description": "Gamma person",
                "source_urls": ["https://example.com/gamma"],
                "confidence": 0.7,
            },
        )
        add_edge(
            self.graph,
            {
                "source": "gamma",
                "target": "alpha",
                "type": "profile_mention",
                "description": "Gamma mentions Alpha in profile text.",
                "source_urls": ["https://example.com/gamma-edge"],
                "confidence": 0.61,
            },
        )

        result = query_relations(self.graph, node_id="alpha", direction="outgoing")

        self.assertEqual(len(result["edges"]), 1)
        self.assertEqual(result["edges"][0]["source"], "alpha")
        self.assertEqual(result["edges"][0]["target"], "beta")

    def test_query_relations_can_filter_incoming_edges(self) -> None:
        add_node(
            self.graph,
            {
                "id": "gamma",
                "type": "person",
                "name": "Gamma",
                "aliases": [],
                "description": "Gamma person",
                "source_urls": ["https://example.com/gamma"],
                "confidence": 0.7,
            },
        )
        add_edge(
            self.graph,
            {
                "source": "gamma",
                "target": "alpha",
                "type": "profile_mention",
                "description": "Gamma mentions Alpha in profile text.",
                "source_urls": ["https://example.com/gamma-edge"],
                "confidence": 0.61,
            },
        )

        result = query_relations(self.graph, node_id="alpha", direction="incoming")

        self.assertEqual(len(result["edges"]), 1)
        self.assertEqual(result["edges"][0]["source"], "gamma")
        self.assertEqual(result["edges"][0]["target"], "alpha")

    def test_duplicate_node_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            add_node(
                self.graph,
                {
                    "id": "alpha",
                    "type": "person",
                    "name": "Duplicate Alpha",
                    "aliases": [],
                    "description": "",
                    "source_urls": [],
                    "confidence": 0.5,
                },
            )

    def test_export_networkx_metrics_creates_scores(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "metrics.json"
            export_networkx_metrics(self.graph, output_path)
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["nodes"]), 2)
            self.assertTrue(any(item["id"] == "alpha" for item in payload["nodes"]))

    def test_export_networkx_metrics_falls_back_without_numpy(self) -> None:
        missing_numpy = ModuleNotFoundError("No module named 'numpy'")
        missing_numpy.name = "numpy"
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "metrics.json"
            with patch("networkx.pagerank", side_effect=missing_numpy):
                export_networkx_metrics(self.graph, output_path)
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(len(payload["nodes"]), 2)
        self.assertTrue(all("pagerank" in item for item in payload["nodes"]))

    def test_export_sqlite_creates_relation_view(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "graph.db"
            export_sqlite(self.graph, db_path)
            connection = sqlite3.connect(db_path)
            try:
                row = connection.execute(
                    "SELECT source_name, target_name, type FROM relation_view"
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(row, ("Alpha", "Beta Community", "affiliation"))

    def test_render_html_contains_detail_panel_and_inspect_button(self) -> None:
        html = render_html(
            self.graph,
            review_candidates_payload={
                "generated_at": "2026-04-24T00:00:00+00:00",
                "candidates": [
                    {
                        "id": "alpha__beta__profile_mention__profile_text",
                        "source": "alpha",
                        "target": "beta",
                        "type": "profile_mention",
                        "basis": "profile_text",
                        "matched_text": "Beta",
                        "evidence_text": "Alpha mentions Beta in a generated profile hint.",
                        "source_urls": ["https://example.com/generated"],
                        "confidence": 0.4,
                        "needs_review": True,
                        "review_notes": "Generated review candidate.",
                    }
                ],
            },
            review_candidate_decisions_payload={
                "updated_at": "2026-04-24T01:00:00+00:00",
                "decisions": {
                    "alpha__beta__profile_mention__profile_text": {
                        "candidate_id": "alpha__beta__profile_mention__profile_text",
                        "status": "dismissed",
                        "note": "Reviewed and skipped.",
                        "source": "alpha",
                        "target": "beta",
                        "type": "profile_mention",
                        "basis": "profile_text",
                        "matched_text": "Beta",
                        "evidence_text": "Alpha mentions Beta in a generated profile hint.",
                        "source_urls": ["https://example.com/generated"],
                        "updated_at": "2026-04-24T01:00:00+00:00",
                    }
                },
            },
            growth_targets_payload={
                "headline": {"label": "Real person target", "current": 2, "target": 1000},
                "phases": [
                    {"label": "Phase 1", "real_person_target": 20},
                    {"label": "Phase 2", "real_person_target": 50},
                ],
                "types": [
                    {"type": "person", "current": 2, "target_min": 1000, "target_max": 1000},
                ],
            },
        )
        self.assertIn("アカウント相関ビュー", html)
        self.assertIn('rel="icon" href="icon.svg"', html)
        self.assertIn('class="header-icon" src="icon.svg"', html)
        self.assertIn("avatar-thumb", html)
        self.assertNotIn("実データ成長目標", html)
        self.assertIn("表示方針", html)
        self.assertIn("const visibleNodeIds = new Set();", html)
        self.assertIn("初期表示は全人物を表示します。", html)
        self.assertNotIn('name="graph-view-mode"', html)
        self.assertNotIn("全体グラフ", html)
        self.assertIn("選択ノード詳細", html)
        self.assertIn("つながっているノード", html)
        self.assertIn("connected-node-list", html)
        self.assertIn("connected-type-group", html)
        self.assertIn("要確認ノード一覧", html)
        self.assertIn("レビュー候補一覧", html)
        self.assertIn("レビュー判断ログ", html)
        self.assertIn("data-node-id=", html)
        self.assertNotIn('tag-review">要確認', html)
        self.assertIn("renderNodeTypeTag", html)
        self.assertIn('fetch(path, { cache: "no-store" })', html)
        self.assertIn("graph-data.json", html)
        self.assertIn("siteAssetUrl", html)
        self.assertIn('.join("\\n")', html)

        self.assertIn('id="cluster-mode"', html)
        self.assertIn('id="keyword-cluster-picker"', html)
        self.assertIn('id="keyword-cluster-select"', html)
        self.assertIn("つながりの近さでまとめる", html)
        self.assertIn("関係パターンでまとめる", html)
        self.assertIn("キーワードでまとめる", html)
        self.assertIn("すべてのキーワード群", html)
        self.assertIn("その塊だけに絞って見られます", html)
        self.assertIn('id="nodes-table-more"', html)
        self.assertIn('id="edges-table-more"', html)
        self.assertIn("enabled: false", html)
        self.assertNotIn("stabilizationIterationsDone", html)
        self.assertIn("network.openCluster(selectedId)", html)
        self.assertIn('document.getElementById("detail-panel").addEventListener("click"', html)
        self.assertIn("updateKeywordClusterControl()", html)
        self.assertIn("却下", html)
        self.assertNotIn("2 / 1000", html)

    def test_export_html_writes_companion_graph_data_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "index.html"
            export_html(
                self.graph,
                output_path=output_path,
                review_candidates_payload={
                    "generated_at": "2026-04-24T00:00:00+00:00",
                    "candidates": [{"id": "candidate-1", "source": "alpha", "target": "beta", "type": "profile_mention"}],
                },
                review_candidate_decisions_payload={
                    "updated_at": "2026-04-24T01:00:00+00:00",
                    "decisions": {"candidate-1": {"status": "approved", "source": "alpha", "target": "beta", "type": "profile_mention"}},
                },
            )

            html = output_path.read_text(encoding="utf-8")
            data_path = Path(tmp_dir) / "graph-data.json"
            payload = json.loads(data_path.read_text(encoding="utf-8"))

        self.assertIn("graph-data.json", html)
        self.assertEqual(len(payload["graph"]["nodes"]), 2)
        self.assertEqual(len(payload["graph"]["edges"]), 1)
        self.assertEqual(payload["review_candidates"]["candidates"][0]["id"], "candidate-1")
        self.assertEqual(
            payload["review_candidate_decisions"]["decisions"]["candidate-1"]["status"],
            "approved",
        )
        self.assertIn("clusters", payload)
        self.assertIn("connectivity", payload["clusters"]["modes"])
        self.assertIn("relation_pattern", payload["clusters"]["modes"])
        self.assertIn("keyword_group", payload["clusters"]["modes"])

    def test_export_html_includes_elsta_keyword_cluster(self) -> None:
        graph = GraphData()
        add_node(
            graph,
            {
                "id": "elsta",
                "type": "community",
                "name": "えるスタ",
                "aliases": ["Elsta"],
                "description": "えるスタ community",
                "source_urls": ["https://example.com/elsta"],
                "confidence": 0.8,
            },
        )
        add_node(
            graph,
            {
                "id": "utopua2",
                "type": "person",
                "name": "ゆーとぴあ@えるスタ",
                "aliases": ["utopua2"],
                "description": "えるスタ サブ講師",
                "source_urls": ["https://example.com/utopua2"],
                "confidence": 0.8,
            },
        )
        add_node(
            graph,
            {
                "id": "xcandee",
                "type": "person",
                "name": "まっちゃ",
                "aliases": ["_xCandee"],
                "description": "えるスタ講師",
                "source_urls": ["https://example.com/xcandee"],
                "confidence": 0.8,
            },
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "index.html"
            export_html(graph, output_path=output_path)
            payload = json.loads((Path(tmp_dir) / "graph-data.json").read_text(encoding="utf-8"))

        keyword_clusters = payload["clusters"]["modes"]["keyword_group"]["clusters"]
        elsta_labels = [info["label"] for info in keyword_clusters.values() if "えるスタ" in info["label"]]
        self.assertEqual(len(elsta_labels), 1)


if __name__ == "__main__":
    unittest.main()
