from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from graph_model import (
    GraphData,
    _absorb_small_clusters,
    _assign_unassigned_people,
    _build_account_projection,
    _fold_semantic_clusters,
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
        self.assertIn("overflow-wrap: anywhere", html)
        self.assertIn('onerror="this.hidden=true"', html)
        self.assertNotIn("実データ成長目標", html)
        self.assertIn("表示方針", html)
        self.assertIn("const visibleNodeIds = new Set();", html)
        self.assertIn("つながりの近さで島分けします", html)
        self.assertIn('id="relevance-filter-toggle"', html)
        self.assertIn("関連人物だけ表示", html)
        self.assertIn("薄い候補", html)
        self.assertIn("薄い候補レビュー", html)
        self.assertIn('id="thin-review-summary"', html)
        self.assertIn("renderThinReviewSummary", html)
        self.assertIn("表示中候補", html)
        self.assertIn("現在の検索条件で薄い候補は残っていません。", html)
        self.assertIn("最新判断:", html)
        self.assertIn('id="thin-candidates-table"', html)
        self.assertIn("data-thin-node-id", html)
        self.assertIn("revealAndFocusNode", html)
        self.assertIn("rawThinCandidateDecisions", html)
        self.assertIn("thinCandidateDecisionStatus", html)
        self.assertIn("thinDecisionCommand", html)
        self.assertIn("data-copy-command", html)
        self.assertIn("copyTextToClipboard", html)
        self.assertIn("薄い候補判断ログ", html)
        self.assertIn('id="thin-candidate-decisions-table"', html)
        self.assertIn("renderThinCandidateDecisionTable", html)
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
        self.assertIn('id="zoom-in"', html)
        self.assertIn('id="zoom-out"', html)
        self.assertIn("window.graphNetwork = network", html)
        self.assertIn("zoomSpeed: 1.4", html)
        self.assertIn('id="keyword-cluster-picker"', html)
        self.assertIn('id="keyword-cluster-select"', html)
        self.assertIn("つながりの近さでまとめる", html)
        self.assertIn("関係パターンでまとめる", html)
        self.assertIn("キーワードでまとめる", html)
        self.assertIn("すべてのキーワード群", html)
        self.assertIn("空欄なら全島を同時に表示します", html)
        self.assertIn('const shouldCluster = clusterMode !== "off"', html)
        self.assertNotIn("const shouldCluster = false", html)
        self.assertIn("shouldCluster ? `${clusterMode}:other`", html)
        self.assertIn("isOtherBucket", html)
        self.assertIn("dragNodes: false", html)
        self.assertIn('network.on("beforeDrawing"', html)
        self.assertIn("allowAutoFit", html)
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
                thin_candidate_decisions_payload={
                    "updated_at": "2026-05-24T00:00:00+00:00",
                    "decisions": {"thin-1": {"status": "exclude", "node_id": "thin-1"}},
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
        self.assertEqual(payload["thin_candidate_decisions"]["decisions"]["thin-1"]["status"], "exclude")
        self.assertIn("clusters", payload)
        self.assertEqual(payload["clusters"]["default_mode"], "connectivity")
        self.assertIn("connectivity", payload["clusters"]["modes"])
        self.assertIn("relation_pattern", payload["clusters"]["modes"])
        self.assertIn("keyword_group", payload["clusters"]["modes"])
        self.assertIn("region_group", payload["clusters"]["modes"])

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

    def test_export_html_includes_atsu_chill_keyword_cluster(self) -> None:
        graph = GraphData()
        for node_id, name, aliases, description in [
            ("pua-chilll", "あつ代表", ["pua_chilll"], "@eroeromancotin @sub_chilll @palace_chilll"),
            ("sub-chilll", "あつ太郎の本音bot", ["sub_chilll", "あつ太郎"], "あつ代表 周辺"),
            ("eroeromancotin", "△▽男優兼監督", ["eroeromancotin"], "△▽"),
        ]:
            add_node(
                graph,
                {
                    "id": node_id,
                    "type": "person",
                    "name": name,
                    "aliases": aliases,
                    "description": description,
                    "source_urls": ["https://example.com"],
                    "confidence": 0.8,
                },
            )

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "index.html"
            export_html(graph, output_path=output_path)
            payload = json.loads((Path(tmp_dir) / "graph-data.json").read_text(encoding="utf-8"))

        keyword_clusters = payload["clusters"]["modes"]["keyword_group"]["clusters"]
        atsu_labels = [info["label"] for info in keyword_clusters.values() if "あつ代表/△▽" in info["label"]]
        self.assertEqual(len(atsu_labels), 1)
        for mode in ("connectivity", "relation_pattern"):
            self.assertEqual(
                payload["clusters"]["modes"][mode]["assignments"]["sub-chilll"],
                "keyword_group:atsu_chill",
            )

    def test_export_html_backfills_weak_app_profiles_into_semantic_cluster(self) -> None:
        graph = GraphData()
        for node_id, name in [
            ("app-one", "アプリ攻略A"),
            ("app-two", "アプリ攻略B"),
            ("app-three", "アプリ攻略C"),
        ]:
            add_node(
                graph,
                {
                    "id": node_id,
                    "type": "person",
                    "name": name,
                    "aliases": [],
                    "description": "マッチングアプリ攻略",
                    "source_urls": ["https://example.com"],
                    "confidence": 0.8,
                },
            )
        for source, target in [("app-one", "app-two"), ("app-two", "app-three")]:
            add_edge(
                graph,
                {
                    "source": source,
                    "target": target,
                    "type": "affiliation",
                    "description": "Weak profile bridge",
                    "source_urls": ["https://example.com"],
                    "confidence": 0.23,
                    "evidence_kind": "interpretation",
                    "needs_review": True,
                    "review_notes": "Profile bridge auto-edge for low-degree node coverage. Shared profile tags: アプリ/オンライン.",
                },
            )

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "index.html"
            export_html(graph, output_path=output_path)
            payload = json.loads((Path(tmp_dir) / "graph-data.json").read_text(encoding="utf-8"))

        connectivity = payload["clusters"]["modes"]["connectivity"]
        labels = [info["label"] for info in connectivity["clusters"].values()]
        self.assertIn("アプリ/オンライン 補助 (3)", labels)
        self.assertEqual(
            {connectivity["assignments"][node_id] for node_id in ("app-one", "app-two", "app-three")},
            {"connectivity:semantic:app_online"},
        )

    def test_anchor_affinity_keeps_atsust_profiles_together(self) -> None:
        graph = GraphData()
        for node_id, name, description in [
            ("wing", "wing師範", "アツストサロン代表"),
            ("mixed", "ジム", "ピカ講習 味噌 元アツスト"),
            # Keep アツスト as the shared affinity anchor (not wing長期 alone).
            ("longterm", "おちゃめ", "アツストサロン 長期"),
            ("emoji", "絵文字勢", "🐶🦁 合流歓迎"),
        ]:
            add_node(
                graph,
                {
                    "id": node_id,
                    "type": "person",
                    "name": name,
                    "aliases": [],
                    "description": description,
                    "source_urls": ["https://example.com"],
                    "confidence": 0.8,
                },
            )

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "index.html"
            export_html(graph, output_path=output_path)
            payload = json.loads((Path(tmp_dir) / "graph-data.json").read_text(encoding="utf-8"))

        connectivity = payload["clusters"]["modes"]["connectivity"]
        self.assertEqual(
            {connectivity["assignments"][node_id] for node_id in ("wing", "mixed", "longterm", "emoji")},
            {"keyword_group:atsust"},
        )

    def test_absorb_small_clusters_merges_tiny_island_into_large_neighbor(self) -> None:
        graph = GraphData()
        for index in range(12):
            add_node(
                graph,
                {
                    "id": f"big-{index}",
                    "type": "person",
                    "name": f"Big {index}",
                    "aliases": [],
                    "description": "core",
                    "source_urls": ["https://example.com"],
                    "confidence": 0.8,
                },
            )
        for index in range(3):
            add_node(
                graph,
                {
                    "id": f"tiny-{index}",
                    "type": "person",
                    "name": f"Tiny {index}",
                    "aliases": [],
                    "description": "wing長期",
                    "source_urls": ["https://example.com"],
                    "confidence": 0.8,
                },
            )
        add_edge(
            graph,
            {
                "source": "tiny-0",
                "target": "big-0",
                "type": "follow",
                "description": "follows",
                "source_urls": ["https://example.com"],
                "confidence": 0.7,
            },
        )
        payload = {
            "assignments": {
                **{f"big-{index}": "connectivity:1" for index in range(12)},
                **{f"tiny-{index}": "keyword_group:wing_longterm" for index in range(3)},
            },
            "clusters": {
                "connectivity:1": {"label": "大きい島 (12)", "size": 12},
                "keyword_group:wing_longterm": {"label": "wing長期 (3)", "size": 3},
            },
        }
        _absorb_small_clusters(graph, payload, min_size=10)
        self.assertNotIn("keyword_group:wing_longterm", payload["clusters"])
        self.assertEqual(payload["clusters"]["connectivity:1"]["size"], 15)
        self.assertEqual(
            {payload["assignments"][f"tiny-{index}"] for index in range(3)},
            {"connectivity:1"},
        )

    def test_fold_semantic_clusters_merges_connected_members_into_large_neighbor(self) -> None:
        graph = GraphData()
        for index in range(12):
            add_node(
                graph,
                {
                    "id": f"big-{index}",
                    "type": "person",
                    "name": f"Big {index}",
                    "aliases": [],
                    "description": "core",
                    "source_urls": ["https://example.com"],
                    "confidence": 0.8,
                },
            )
        for index in range(3):
            add_node(
                graph,
                {
                    "id": f"app-{index}",
                    "type": "person",
                    "name": f"App {index}",
                    "aliases": [],
                    "description": "マッチングアプリ攻略",
                    "source_urls": ["https://example.com"],
                    "confidence": 0.8,
                },
            )
        add_edge(
            graph,
            {
                "source": "app-0",
                "target": "big-0",
                "type": "follow",
                "description": "follows",
                "source_urls": ["https://example.com"],
                "confidence": 0.7,
            },
        )
        payload = {
            "assignments": {
                **{f"big-{index}": "connectivity:1" for index in range(12)},
                **{f"app-{index}": "connectivity:semantic:app_online" for index in range(3)},
            },
            "clusters": {
                "connectivity:1": {"label": "大きい島 (12)", "size": 12},
                "connectivity:semantic:app_online": {"label": "アプリ/オンライン 補助 (3)", "size": 3},
            },
        }
        _fold_semantic_clusters(graph, payload, min_size=10)
        self.assertNotIn("connectivity:semantic:app_online", payload["clusters"])
        self.assertEqual(payload["assignments"]["app-0"], "connectivity:1")
        self.assertNotIn("app-1", payload["assignments"])
        self.assertNotIn("app-2", payload["assignments"])

    def test_fold_semantic_clusters_keeps_tiny_graphs(self) -> None:
        graph = GraphData()
        for index in range(3):
            add_node(
                graph,
                {
                    "id": f"app-{index}",
                    "type": "person",
                    "name": f"App {index}",
                    "aliases": [],
                    "description": "マッチングアプリ攻略",
                    "source_urls": ["https://example.com"],
                    "confidence": 0.8,
                },
            )
        payload = {
            "assignments": {f"app-{index}": "connectivity:semantic:app_online" for index in range(3)},
            "clusters": {
                "connectivity:semantic:app_online": {"label": "アプリ/オンライン 補助 (3)", "size": 3},
            },
        }
        _fold_semantic_clusters(graph, payload, min_size=10)
        self.assertEqual(
            payload["clusters"]["connectivity:semantic:app_online"]["label"],
            "アプリ/オンライン 補助 (3)",
        )

    def test_assign_unassigned_people_gathers_leftovers_into_other(self) -> None:
        graph = GraphData()
        for index in range(12):
            add_node(
                graph,
                {
                    "id": f"big-{index}",
                    "type": "person",
                    "name": f"Big {index}",
                    "aliases": [],
                    "description": "core",
                    "source_urls": ["https://example.com"],
                    "confidence": 0.8,
                },
            )
        for index in range(2):
            add_node(
                graph,
                {
                    "id": f"lone-{index}",
                    "type": "person",
                    "name": f"Lone {index}",
                    "aliases": [],
                    "description": "",
                    "source_urls": ["https://example.com"],
                    "confidence": 0.8,
                },
            )
        add_edge(
            graph,
            {
                "source": "lone-0",
                "target": "big-0",
                "type": "follow",
                "description": "follows",
                "source_urls": ["https://example.com"],
                "confidence": 0.7,
            },
        )
        payload = {
            "assignments": {f"big-{index}": "connectivity:1" for index in range(12)},
            "clusters": {
                "connectivity:1": {"label": "大きい島 (12)", "size": 12},
            },
        }
        _assign_unassigned_people(graph, payload, "connectivity")
        self.assertEqual(payload["assignments"]["lone-0"], "connectivity:1")
        self.assertEqual(payload["assignments"]["lone-1"], "connectivity:other")
        self.assertEqual(payload["clusters"]["connectivity:other"]["label"], "その他 (1)")
        self.assertEqual(payload["clusters"]["connectivity:1"]["size"], 13)

    def test_export_html_includes_region_cluster_with_keyword_summary(self) -> None:
        graph = GraphData()
        for node_id, name, description in [
            ("tokyo-one", "東京MBH", "東京 MBH 講習"),
            ("tokyo-two", "渋谷味噌", "渋谷 味噌"),
            ("tokyo-three", "新宿一門", "新宿 一門"),
            ("nagoya-one", "名古屋PUA", "名古屋 ナンパ"),
        ]:
            add_node(
                graph,
                {
                    "id": node_id,
                    "type": "person",
                    "name": name,
                    "aliases": [],
                    "description": description,
                    "source_urls": ["https://example.com"],
                    "confidence": 0.8,
                },
            )

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "index.html"
            export_html(graph, output_path=output_path)
            payload = json.loads((Path(tmp_dir) / "graph-data.json").read_text(encoding="utf-8"))

        region_clusters = payload["clusters"]["modes"]["region_group"]["clusters"]
        tokyo_clusters = [info for info in region_clusters.values() if info["label"] == "東京 大分類"]
        self.assertEqual(len(tokyo_clusters), 1)
        self.assertIn("中分類", tokyo_clusters[0]["title"])
        self.assertIn("MBH", tokyo_clusters[0]["title"])

    def test_weak_profile_bridge_has_low_cluster_weight(self) -> None:
        graph = GraphData()
        for node_id in ("alpha", "beta", "gamma"):
            add_node(
                graph,
                {
                    "id": node_id,
                    "type": "person",
                    "name": node_id,
                    "aliases": [],
                    "description": "",
                    "source_urls": ["https://example.com"],
                    "confidence": 0.8,
                },
            )
        add_edge(
            graph,
            {
                "source": "alpha",
                "target": "beta",
                "type": "affiliation",
                "description": "プロフィール特徴語（PUA）が重なるため、近い人物候補として補助接続（自動）。",
                "confidence": 0.23,
                "evidence_kind": "interpretation",
                "needs_review": True,
                "review_notes": "Profile bridge auto-edge for low-degree node coverage. Shared profile tags: PUA. Score: 0.35.",
            },
        )
        add_edge(
            graph,
            {
                "source": "alpha",
                "target": "gamma",
                "type": "affiliation",
                "description": "プロフィール特徴語（PUA、講習）が重なるため、近い人物候補として補助接続（自動）。",
                "confidence": 0.23,
                "evidence_kind": "interpretation",
                "needs_review": True,
                "review_notes": "Profile bridge auto-edge for low-degree node coverage. Shared profile tags: PUA, 講習. Score: 2.60.",
            },
        )

        account_graph, _ = _build_account_projection(graph, "connectivity")

        self.assertLess(account_graph["alpha"]["beta"]["weight"], 0.2)
        self.assertGreater(account_graph["alpha"]["gamma"]["weight"], 0.8)


if __name__ == "__main__":
    unittest.main()
