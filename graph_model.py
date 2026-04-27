from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any

NODE_TYPES = ("person", "community", "platform", "location", "content")
EDGE_TYPES = (
    "influence",
    "affiliation",
    "collaboration",
    "criticism",
    "monetization",
    "activity",
    "reference",
)
EVIDENCE_KINDS = ("fact", "interpretation", "mixed")


def _normalize_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [part.strip() for part in value.split(",")]
    elif isinstance(value, list):
        items = [str(part).strip() for part in value]
    else:
        raise TypeError(f"Unsupported list value: {value!r}")
    unique_items: list[str] = []
    for item in items:
        if item and item not in unique_items:
            unique_items.append(item)
    return unique_items


def _validate_confidence(value: float) -> float:
    if not 0.0 <= value <= 1.0:
        raise ValueError("confidence must be between 0.0 and 1.0")
    return round(float(value), 2)


def _normalize_evidence_kind(value: Any) -> str:
    normalized = str(value or "fact").strip().lower()
    if normalized not in EVIDENCE_KINDS:
        raise ValueError(f"Unsupported evidence kind: {value}")
    return normalized


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off", ""}:
            return False
    raise ValueError(f"Unsupported boolean value: {value!r}")


@dataclass(slots=True)
class Node:
    id: str
    type: str
    name: str
    aliases: list[str] = field(default_factory=list)
    description: str = ""
    icon_url: str = ""
    source_urls: list[str] = field(default_factory=list)
    confidence: float = 0.5
    evidence_kind: str = "fact"
    needs_review: bool = False
    review_notes: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Node":
        return cls(
            id=str(payload["id"]).strip(),
            type=str(payload["type"]).strip(),
            name=str(payload["name"]).strip(),
            aliases=_normalize_text_list(payload.get("aliases", [])),
            description=str(payload.get("description", "")).strip(),
            icon_url=str(payload.get("icon_url", "")).strip(),
            source_urls=_normalize_text_list(payload.get("source_urls", [])),
            confidence=_validate_confidence(float(payload.get("confidence", 0.5))),
            evidence_kind=_normalize_evidence_kind(payload.get("evidence_kind", "fact")),
            needs_review=_normalize_bool(payload.get("needs_review", False)),
            review_notes=str(payload.get("review_notes", "")).strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "name": self.name,
            "aliases": self.aliases,
            "description": self.description,
            "icon_url": self.icon_url,
            "source_urls": self.source_urls,
            "confidence": self.confidence,
            "evidence_kind": self.evidence_kind,
            "needs_review": self.needs_review,
            "review_notes": self.review_notes,
        }


@dataclass(slots=True)
class Edge:
    source: str
    target: str
    type: str
    description: str = ""
    source_urls: list[str] = field(default_factory=list)
    confidence: float = 0.5
    evidence_kind: str = "fact"
    needs_review: bool = False
    review_notes: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Edge":
        return cls(
            source=str(payload["source"]).strip(),
            target=str(payload["target"]).strip(),
            type=str(payload["type"]).strip(),
            description=str(payload.get("description", "")).strip(),
            source_urls=_normalize_text_list(payload.get("source_urls", [])),
            confidence=_validate_confidence(float(payload.get("confidence", 0.5))),
            evidence_kind=_normalize_evidence_kind(payload.get("evidence_kind", "fact")),
            needs_review=_normalize_bool(payload.get("needs_review", False)),
            review_notes=str(payload.get("review_notes", "")).strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "type": self.type,
            "description": self.description,
            "source_urls": self.source_urls,
            "confidence": self.confidence,
            "evidence_kind": self.evidence_kind,
            "needs_review": self.needs_review,
            "review_notes": self.review_notes,
        }


@dataclass(slots=True)
class GraphData:
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)

    def to_dict(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
        }


def _validate_node(node: Node) -> None:
    if not node.id:
        raise ValueError("node.id is required")
    if node.type not in NODE_TYPES:
        raise ValueError(f"Unsupported node type: {node.type}")
    if not node.name:
        raise ValueError("node.name is required")
    _validate_confidence(node.confidence)
    _normalize_evidence_kind(node.evidence_kind)


def _validate_edge(edge: Edge, node_ids: set[str]) -> None:
    if edge.type not in EDGE_TYPES:
        raise ValueError(f"Unsupported edge type: {edge.type}")
    if edge.source not in node_ids or edge.target not in node_ids:
        raise ValueError("edge references unknown nodes")
    _validate_confidence(edge.confidence)
    _normalize_evidence_kind(edge.evidence_kind)


def add_node(graph: GraphData, node_payload: Node | dict[str, Any]) -> Node:
    node = node_payload if isinstance(node_payload, Node) else Node.from_dict(node_payload)
    _validate_node(node)
    if any(existing.id == node.id for existing in graph.nodes):
        raise ValueError(f"Duplicate node id: {node.id}")
    graph.nodes.append(node)
    return node


def add_edge(graph: GraphData, edge_payload: Edge | dict[str, Any]) -> Edge:
    edge = edge_payload if isinstance(edge_payload, Edge) else Edge.from_dict(edge_payload)
    node_ids = {node.id for node in graph.nodes}
    _validate_edge(edge, node_ids)
    duplicate = next(
        (
            existing
            for existing in graph.edges
            if existing.source == edge.source
            and existing.target == edge.target
            and existing.type == edge.type
            and existing.description == edge.description
        ),
        None,
    )
    if duplicate is not None:
        raise ValueError(
            f"Duplicate edge: {edge.source}->{edge.target} ({edge.type})"
        )
    graph.edges.append(edge)
    return edge


def load_graph(
    nodes_path: str | Path = "data/nodes.json",
    edges_path: str | Path = "data/edges.json",
) -> GraphData:
    nodes_file = Path(nodes_path)
    edges_file = Path(edges_path)
    graph = GraphData()

    if nodes_file.exists():
        for payload in json.loads(nodes_file.read_text(encoding="utf-8")):
            add_node(graph, payload)
    if edges_file.exists():
        for payload in json.loads(edges_file.read_text(encoding="utf-8")):
            add_edge(graph, payload)
    return graph


def save_graph(
    graph: GraphData,
    nodes_path: str | Path = "data/nodes.json",
    edges_path: str | Path = "data/edges.json",
) -> None:
    nodes_file = Path(nodes_path)
    edges_file = Path(edges_path)
    nodes_file.parent.mkdir(parents=True, exist_ok=True)
    edges_file.parent.mkdir(parents=True, exist_ok=True)

    nodes_payload = sorted(
        (node.to_dict() for node in graph.nodes),
        key=lambda item: (item["type"], item["name"], item["id"]),
    )
    edges_payload = sorted(
        (edge.to_dict() for edge in graph.edges),
        key=lambda item: (item["type"], item["source"], item["target"]),
    )

    nodes_file.write_text(
        json.dumps(nodes_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    edges_file.write_text(
        json.dumps(edges_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def query_relations(
    graph: GraphData,
    search_term: str = "",
    node_type: str | None = None,
    edge_type: str | None = None,
    node_id: str | None = None,
    direction: str = "both",
) -> dict[str, Any]:
    if direction not in {"both", "incoming", "outgoing"}:
        raise ValueError(f"Unsupported direction: {direction}")

    allowed_node_types = {node_type} if node_type else set(NODE_TYPES)
    allowed_edge_types = {edge_type} if edge_type else set(EDGE_TYPES)
    filtered_nodes = [node for node in graph.nodes if node.type in allowed_node_types]
    nodes_by_id = {node.id: node for node in filtered_nodes}
    allowed_node_ids = set(nodes_by_id)
    filtered_edges = [
        edge
        for edge in graph.edges
        if edge.type in allowed_edge_types
        and edge.source in allowed_node_ids
        and edge.target in allowed_node_ids
    ]

    if node_id:
        matched_ids = {node_id} if node_id in allowed_node_ids else set()
    elif search_term:
        lowered = search_term.casefold()
        matched_ids = {
            node.id
            for node in filtered_nodes
            if lowered in " ".join(
                [node.id, node.name, node.description, *node.aliases]
            ).casefold()
        }
    else:
        matched_ids = set(allowed_node_ids)

    if search_term or node_id:
        visible_edges = [
            edge
            for edge in filtered_edges
            if (
                direction == "both"
                and (edge.source in matched_ids or edge.target in matched_ids)
            )
            or (direction == "outgoing" and edge.source in matched_ids)
            or (direction == "incoming" and edge.target in matched_ids)
        ]
        visible_node_ids = set(matched_ids)
        for edge in visible_edges:
            visible_node_ids.add(edge.source)
            visible_node_ids.add(edge.target)
    else:
        visible_edges = filtered_edges
        visible_node_ids = allowed_node_ids

    visible_nodes = [
        nodes_by_id[node_id_value]
        for node_id_value in sorted(visible_node_ids, key=lambda item: nodes_by_id[item].name)
    ]
    return {
        "nodes": [node.to_dict() for node in visible_nodes],
        "edges": [edge.to_dict() for edge in visible_edges],
        "matched_node_ids": sorted(matched_ids),
        "direction": direction,
    }


def export_csv(
    graph: GraphData,
    nodes_csv_path: str | Path = "data/nodes.csv",
    edges_csv_path: str | Path = "data/edges.csv",
) -> None:
    nodes_file = Path(nodes_csv_path)
    edges_file = Path(edges_csv_path)
    nodes_file.parent.mkdir(parents=True, exist_ok=True)
    edges_file.parent.mkdir(parents=True, exist_ok=True)

    with nodes_file.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "id",
                "type",
                "name",
                "aliases",
                "description",
                "icon_url",
                "source_urls",
                "confidence",
                "evidence_kind",
                "needs_review",
                "review_notes",
            ],
        )
        writer.writeheader()
        for node in graph.nodes:
            writer.writerow(
                {
                    "id": node.id,
                    "type": node.type,
                    "name": node.name,
                    "aliases": " | ".join(node.aliases),
                    "description": node.description,
                    "icon_url": node.icon_url,
                    "source_urls": " | ".join(node.source_urls),
                    "confidence": node.confidence,
                    "evidence_kind": node.evidence_kind,
                    "needs_review": node.needs_review,
                    "review_notes": node.review_notes,
                }
            )

    with edges_file.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "source",
                "target",
                "type",
                "description",
                "source_urls",
                "confidence",
                "evidence_kind",
                "needs_review",
                "review_notes",
            ],
        )
        writer.writeheader()
        for edge in graph.edges:
            writer.writerow(
                {
                    "source": edge.source,
                    "target": edge.target,
                    "type": edge.type,
                    "description": edge.description,
                    "source_urls": " | ".join(edge.source_urls),
                    "confidence": edge.confidence,
                    "evidence_kind": edge.evidence_kind,
                    "needs_review": edge.needs_review,
                    "review_notes": edge.review_notes,
                }
            )


def build_networkx_graph(graph: GraphData):
    import networkx as nx

    digraph = nx.DiGraph()
    for node in graph.nodes:
        digraph.add_node(node.id, **node.to_dict())
    for edge in graph.edges:
        digraph.add_edge(edge.source, edge.target, **edge.to_dict())
    return digraph


def export_networkx_metrics(
    graph: GraphData,
    output_path: str | Path = "data/networkx_metrics.json",
) -> None:
    import networkx as nx
    from networkx.algorithms.link_analysis import pagerank_alg

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    digraph = build_networkx_graph(graph)

    if digraph.number_of_nodes():
        degree_centrality = nx.degree_centrality(digraph)
        betweenness_centrality = nx.betweenness_centrality(digraph)
        try:
            pagerank = nx.pagerank(digraph)
        except ModuleNotFoundError as exc:
            if exc.name != "numpy":
                raise
            pagerank = pagerank_alg._pagerank_python(digraph)
    else:
        degree_centrality = {}
        betweenness_centrality = {}
        pagerank = {}

    nodes_by_id = {node.id: node for node in graph.nodes}
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "nodes": [
            {
                "id": node.id,
                "name": node.name,
                "type": node.type,
                "degree_centrality": round(degree_centrality.get(node.id, 0.0), 4),
                "betweenness_centrality": round(
                    betweenness_centrality.get(node.id, 0.0), 4
                ),
                "pagerank": round(pagerank.get(node.id, 0.0), 4),
            }
            for node in sorted(nodes_by_id.values(), key=lambda item: item.name)
        ],
    }
    output_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def export_sqlite(
    graph: GraphData,
    sqlite_path: str | Path = "data/graph.db",
) -> None:
    db_path = Path(sqlite_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(
            """
            DROP VIEW IF EXISTS relation_view;
            DROP TABLE IF EXISTS edge_sources;
            DROP TABLE IF EXISTS node_sources;
            DROP TABLE IF EXISTS node_aliases;
            DROP TABLE IF EXISTS edges;
            DROP TABLE IF EXISTS nodes;

            CREATE TABLE nodes (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                icon_url TEXT NOT NULL,
                confidence REAL NOT NULL,
                evidence_kind TEXT NOT NULL,
                needs_review INTEGER NOT NULL,
                review_notes TEXT NOT NULL
            );

            CREATE TABLE node_aliases (
                node_id TEXT NOT NULL,
                alias TEXT NOT NULL,
                PRIMARY KEY (node_id, alias),
                FOREIGN KEY (node_id) REFERENCES nodes(id)
            );

            CREATE TABLE node_sources (
                node_id TEXT NOT NULL,
                source_url TEXT NOT NULL,
                PRIMARY KEY (node_id, source_url),
                FOREIGN KEY (node_id) REFERENCES nodes(id)
            );

            CREATE TABLE edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                target TEXT NOT NULL,
                type TEXT NOT NULL,
                description TEXT NOT NULL,
                confidence REAL NOT NULL,
                evidence_kind TEXT NOT NULL,
                needs_review INTEGER NOT NULL,
                review_notes TEXT NOT NULL,
                FOREIGN KEY (source) REFERENCES nodes(id),
                FOREIGN KEY (target) REFERENCES nodes(id)
            );

            CREATE TABLE edge_sources (
                edge_id INTEGER NOT NULL,
                source_url TEXT NOT NULL,
                PRIMARY KEY (edge_id, source_url),
                FOREIGN KEY (edge_id) REFERENCES edges(id)
            );

            CREATE VIEW relation_view AS
            SELECT
                e.id,
                e.type,
                s.name AS source_name,
                t.name AS target_name,
                e.description,
                e.confidence,
                e.evidence_kind,
                e.needs_review,
                e.review_notes
            FROM edges e
            JOIN nodes s ON s.id = e.source
            JOIN nodes t ON t.id = e.target;
            """
        )

        for node in graph.nodes:
            connection.execute(
                """
                INSERT INTO nodes (id, type, name, description, icon_url, confidence, evidence_kind, needs_review, review_notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node.id,
                    node.type,
                    node.name,
                    node.description,
                    node.icon_url,
                    node.confidence,
                    node.evidence_kind,
                    int(node.needs_review),
                    node.review_notes,
                ),
            )
            connection.executemany(
                "INSERT INTO node_aliases (node_id, alias) VALUES (?, ?)",
                [(node.id, alias) for alias in node.aliases],
            )
            connection.executemany(
                "INSERT INTO node_sources (node_id, source_url) VALUES (?, ?)",
                [(node.id, url) for url in dict.fromkeys(node.source_urls)],
            )

        for edge in graph.edges:
            cursor = connection.execute(
                """
                INSERT INTO edges (source, target, type, description, confidence, evidence_kind, needs_review, review_notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    edge.source,
                    edge.target,
                    edge.type,
                    edge.description,
                    edge.confidence,
                    edge.evidence_kind,
                    int(edge.needs_review),
                    edge.review_notes,
                ),
            )
            edge_id = cursor.lastrowid
            connection.executemany(
                "INSERT INTO edge_sources (edge_id, source_url) VALUES (?, ?)",
                [(edge_id, url) for url in dict.fromkeys(edge.source_urls)],
            )

        connection.commit()
    finally:
        connection.close()


def render_html(
    graph: GraphData,
    title: str = "Pickup Artist Network",
    review_candidates_payload: dict[str, Any] | None = None,
    review_candidate_decisions_payload: dict[str, Any] | None = None,
    growth_targets_payload: dict[str, Any] | None = None,
) -> str:
    node_type_labels = {
        "person": "人物",
        "community": "コミュニティ",
        "platform": "媒体",
        "location": "場所",
        "content": "コンテンツ",
    }

    def localize_phase_label(label: Any) -> str:
        text = str(label or "-")
        if text.startswith("Phase "):
            suffix = text.split(" ", 1)[1]
            return f"第{suffix}段階"
        return text

    graph_json = json.dumps(graph.to_dict(), ensure_ascii=False)
    review_candidates_json = json.dumps(
        review_candidates_payload or {"generated_at": "", "candidates": []},
        ensure_ascii=False,
    )
    review_candidate_decisions_json = json.dumps(
        review_candidate_decisions_payload or {"updated_at": "", "decisions": {}},
        ensure_ascii=False,
    )
    growth_targets = growth_targets_payload or {"headline": {}, "phases": [], "types": []}
    headline = growth_targets.get("headline", {})
    growth_headline = (
        f"{headline.get('current', 0)} / {headline.get('target', 0)}"
        if headline
        else "-"
    )
    growth_phase_cards = "".join(
        (
            f'<div class="stat"><span class="muted">{escape(localize_phase_label(phase.get("label", "-")))}</span>'
            f'<strong>{escape(str(phase.get("real_person_target", "-")))}</strong></div>'
        )
        for phase in growth_targets.get("phases", [])
        if isinstance(phase, dict)
    )
    growth_type_rows = "".join(
        (
            "<tr>"
            f"<td><strong>{escape(node_type_labels.get(str(item.get('type', '-')), str(item.get('type', '-'))))}</strong></td>"
            f"<td>{escape(str(item.get('current', 0)))}</td>"
            f"<td>{escape(str(item.get('target_min', 0)))}"
            f"{'' if item.get('target_min') == item.get('target_max') else ' - ' + escape(str(item.get('target_max', 0)))}</td>"
            "</tr>"
        )
        for item in growth_targets.get("types", [])
        if isinstance(item, dict)
    )
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    template = """<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>__TITLE__</title>
  <link rel="icon" href="icon.svg" type="image/svg+xml">
  <link rel="stylesheet" href="https://unpkg.com/vis-network@9.1.9/styles/vis-network.min.css">
  <style>
    :root {
      color-scheme: light;
      --bg: #f5f7fb;
      --panel: #ffffff;
      --border: #dbe2ea;
      --text: #17212b;
      --muted: #5c6b7a;
      --accent: #2f6feb;
    }
    body {
      margin: 0;
      font-family: Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    header {
      padding: 24px;
      background: linear-gradient(135deg, #17212b, #234f9d);
      color: #fff;
    }
    .header-brand {
      display: flex;
      align-items: center;
      gap: 14px;
    }
    .header-icon {
      width: 56px;
      height: 56px;
      border-radius: 16px;
      background: rgba(255, 255, 255, 0.08);
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.18);
      flex: 0 0 auto;
    }
    .header-copy h1 {
      margin: 0;
    }
    .avatar-thumb {
      width: 36px;
      height: 36px;
      border-radius: 50%;
      object-fit: cover;
      background: #eef4ff;
      border: 1px solid var(--border);
      flex: 0 0 auto;
    }
    .detail-avatar {
      width: 52px;
      height: 52px;
      border-radius: 50%;
      object-fit: cover;
      background: #eef4ff;
      border: 1px solid var(--border);
      flex: 0 0 auto;
    }
    .node-name-cell,
    .detail-heading {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .node-name-text {
      min-width: 0;
    }
    header p {
      margin: 8px 0 0;
      color: rgba(255, 255, 255, 0.82);
    }
    main {
      padding: 20px;
      display: grid;
      gap: 20px;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 16px;
      box-shadow: 0 10px 24px rgba(23, 33, 43, 0.06);
    }
    .controls {
      display: grid;
      gap: 16px;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    }
    .filter-group {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 8px;
    }
    .chip {
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 6px 10px;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: #fff;
      color: var(--muted);
      font-size: 13px;
    }
    .stats {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 12px;
    }
    .stat {
      background: #f9fbff;
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 12px;
    }
    .stat strong {
      display: block;
      font-size: 24px;
      margin-top: 6px;
    }
    #network {
      height: 620px;
      border: 1px solid var(--border);
      border-radius: 12px;
      background: #fff;
    }
    input[type="search"] {
      width: 100%;
      box-sizing: border-box;
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 10px 12px;
      font-size: 14px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    th, td {
      border-bottom: 1px solid var(--border);
      padding: 10px 8px;
      text-align: left;
      vertical-align: top;
    }
    th {
      color: var(--muted);
      background: #fafcff;
      position: sticky;
      top: 0;
    }
    .table-wrap {
      max-height: 360px;
      overflow: auto;
      border: 1px solid var(--border);
      border-radius: 12px;
    }
    .tag {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 700;
      background: #eef4ff;
      color: #224a8f;
      margin-right: 6px;
      margin-bottom: 6px;
    }
    .tag-evidence-fact {
      background: #eef7ee;
      color: #1f7a3d;
    }
    .tag-evidence-interpretation {
      background: #fff3e8;
      color: #b35c00;
    }
    .tag-evidence-mixed {
      background: #f4edff;
      color: #6842c2;
    }
    .tag-review {
      background: #ffe8ee;
      color: #b4234d;
    }
    .muted {
      color: var(--muted);
    }
    .two-column {
      display: grid;
      gap: 20px;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
    }
    .graph-layout {
      display: grid;
      gap: 20px;
      grid-template-columns: minmax(0, 2fr) minmax(280px, 1fr);
      align-items: start;
    }
    .network-panel {
      min-width: 0;
    }
    .detail-panel {
      position: sticky;
      top: 20px;
    }
    .detail-empty {
      color: var(--muted);
      line-height: 1.6;
    }
    .detail-card h3 {
      margin: 0 0 8px;
    }
    .detail-meta {
      margin-bottom: 12px;
    }
    .detail-section + .detail-section {
      margin-top: 16px;
    }
    .detail-section h4 {
      margin: 0 0 8px;
      font-size: 14px;
    }
    .detail-list,
    .source-list {
      margin: 0;
      padding-left: 18px;
    }
    .detail-list li,
    .source-list li {
      margin-bottom: 8px;
      line-height: 1.5;
    }
    .inspect-button {
      border: 1px solid var(--border);
      border-radius: 999px;
      background: #fff;
      color: var(--accent);
      padding: 6px 10px;
      font-size: 12px;
      cursor: pointer;
    }
    .inspect-button:hover {
      border-color: var(--accent);
      background: #f6f9ff;
    }
    a {
      color: var(--accent);
    }
    @media (max-width: 1024px) {
      .graph-layout {
        grid-template-columns: 1fr;
      }
      .detail-panel {
        position: static;
      }
    }
  </style>
</head>
<body>
  <header>
    <div class="header-brand">
      <img class="header-icon" src="icon.svg" alt="Pickup Artist Network icon">
      <div class="header-copy">
        <h1>__TITLE__</h1>
        <p>`sokusuu-ranking` を参考にした手動優先の関係グラフ試作版です。生成時刻: __GENERATED_AT__。</p>
      </div>
    </div>
  </header>
  <main>
    <section class="panel">
      <h2>実データ成長目標</h2>
      <p class="muted">まずはレビュー可能な粒度を保ちながら、実在人物ノード 20 件を最初の目標にします。</p>
      <section class="stats">
        <div class="stat"><span class="muted">実在人物</span><strong>__GROWTH_HEADLINE__</strong></div>
        __GROWTH_PHASE_CARDS__
      </section>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>種別</th>
              <th>現在の実データ数</th>
              <th>目標レンジ</th>
            </tr>
          </thead>
          <tbody>
            __GROWTH_TYPE_ROWS__
          </tbody>
        </table>
      </div>
    </section>

    <section class="panel controls">
      <div>
        <label for="search"><strong>名前検索</strong></label>
        <input id="search" type="search" placeholder="名前 / id / 別名 / 説明">
      </div>
      <div>
        <strong>表示モード</strong>
        <div class="filter-group">
          <label class="chip">
            <input type="radio" name="graph-view-mode" value="account" checked>
            <span>アカウント相関</span>
          </label>
          <label class="chip">
            <input type="radio" name="graph-view-mode" value="full">
            <span>全体グラフ</span>
          </label>
        </div>
        <div class="muted">既定では人物・コミュニティを優先表示し、媒体 / 場所 / コンテンツは全体グラフで見られます。</div>
      </div>
      <div>
        <strong>ノード種別</strong>
        <div id="node-type-filters" class="filter-group"></div>
      </div>
      <div>
        <strong>関係種別</strong>
        <div id="edge-type-filters" class="filter-group"></div>
      </div>
    </section>

    <section class="panel stats">
      <div class="stat"><span class="muted">表示ノード数</span><strong id="visible-nodes">0</strong></div>
      <div class="stat"><span class="muted">表示エッジ数</span><strong id="visible-edges">0</strong></div>
      <div class="stat"><span class="muted">要確認ノード数</span><strong id="review-nodes">0</strong></div>
      <div class="stat"><span class="muted">要確認エッジ数</span><strong id="review-edges">0</strong></div>
      <div class="stat"><span class="muted">レビュー候補数</span><strong id="review-candidates">0</strong></div>
      <div class="stat"><span class="muted">総ノード数</span><strong id="total-nodes">0</strong></div>
      <div class="stat"><span class="muted">総エッジ数</span><strong id="total-edges">0</strong></div>
    </section>

    <section class="graph-layout">
      <section class="panel network-panel">
        <h2>アカウント相関ビュー</h2>
        <p class="muted">まずはアカウント同士のつながりを見やすくし、必要なときだけ全体グラフへ広げます。</p>
        <p class="muted">※ 現在の公開版はサンプル構成です。架空の人物ノードと安全寄りの公開ノードを含み、実在ナンパ師アカウントの確定相関図ではありません。</p>
        <div id="network"></div>
      </section>

      <aside class="panel detail-panel">
        <h2>選択ノード詳細</h2>
        <div id="detail-panel" class="detail-empty">相関図かノード一覧から 1 件選ぶと、説明・source URLs・入出力エッジをここに表示します。</div>
      </aside>
    </section>

    <section class="two-column">
      <section class="panel">
        <h2>要確認ノード一覧</h2>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>詳細</th>
                <th>名前</th>
                <th>種別</th>
                <th>確認メモ</th>
                <th>出典</th>
              </tr>
            </thead>
            <tbody id="review-nodes-table"></tbody>
          </table>
        </div>
      </section>

      <section class="panel">
        <h2>要確認エッジ一覧</h2>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>発信元</th>
                <th>関係</th>
                <th>対象</th>
                <th>確認メモ</th>
                <th>出典</th>
              </tr>
            </thead>
            <tbody id="review-edges-table"></tbody>
          </table>
        </div>
      </section>
    </section>

    <section class="panel">
      <h2>レビュー候補一覧</h2>
      <p class="muted">プロフィール / 概要 / 固定ポストのヒントから機械的に作った候補です。これはレビュー専用で、確定データにはまだ入りません。</p>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>発信元</th>
              <th>提案関係</th>
              <th>対象</th>
              <th>根拠テキスト</th>
              <th>確認メモ</th>
              <th>出典</th>
            </tr>
          </thead>
          <tbody id="review-candidates-table"></tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <h2>レビュー判断ログ</h2>
      <p class="muted">承認 / 却下 の判断は <code>data/review_candidate_decisions.json</code> に保持されます。</p>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>発信元</th>
              <th>状態</th>
              <th>対象</th>
              <th>根拠テキスト</th>
              <th>確認メモ</th>
              <th>出典</th>
            </tr>
          </thead>
          <tbody id="review-candidate-decisions-table"></tbody>
        </table>
      </div>
    </section>

    <section class="two-column">
      <section class="panel">
        <h2>ノード一覧</h2>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>詳細</th>
                <th>名前</th>
                <th>種別</th>
                <th>別名</th>
                <th>説明</th>
                <th>確信度</th>
                <th>出典</th>
              </tr>
            </thead>
            <tbody id="nodes-table"></tbody>
          </table>
        </div>
      </section>

      <section class="panel">
        <h2>エッジ一覧</h2>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>発信元</th>
                <th>関係</th>
                <th>対象</th>
                <th>説明</th>
                <th>確信度</th>
                <th>出典</th>
              </tr>
            </thead>
            <tbody id="edges-table"></tbody>
          </table>
        </div>
      </section>
    </section>
  </main>

  <script id="graph-data" type="application/json">__GRAPH_JSON__</script>
  <script id="review-candidates-data" type="application/json">__REVIEW_CANDIDATES_JSON__</script>
  <script id="review-candidate-decisions-data" type="application/json">__REVIEW_CANDIDATE_DECISIONS_JSON__</script>
  <script src="https://unpkg.com/vis-network@9.1.9/dist/vis-network.min.js"></script>
  <script>
    const rawGraph = JSON.parse(document.getElementById("graph-data").textContent);
    const rawReviewCandidates = JSON.parse(document.getElementById("review-candidates-data").textContent);
    const rawReviewCandidateDecisions = JSON.parse(document.getElementById("review-candidate-decisions-data").textContent);
    const nodeColors = {
      person: "#2f6feb",
      community: "#7e57c2",
      platform: "#2e8b57",
      location: "#f39c12",
      content: "#d14d72"
    };
    const nodeTypeLabels = {
      person: "人物",
      community: "コミュニティ",
      platform: "媒体",
      location: "場所",
      content: "コンテンツ"
    };
    const edgeTypeLabels = {
      influence: "影響",
      affiliation: "所属・関係",
      collaboration: "交流・コラボ",
      criticism: "批判・対立",
      monetization: "収益・商品",
      activity: "活動場所",
      reference: "言及・紹介"
    };
    const evidenceKindLabels = {
      fact: "事実",
      interpretation: "解釈",
      mixed: "混合"
    };
    const basisLabels = {
      profile_text: "プロフィール",
      summary: "概要",
      pinned_post_text: "固定ポスト"
    };
    const decisionStatusLabels = {
      approved: "承認",
      dismissed: "却下"
    };
    const accountNodeTypes = new Set(["person", "community"]);

    const allNodeTypes = [...new Set(rawGraph.nodes.map((node) => node.type))];
    const allEdgeTypes = [...new Set(rawGraph.edges.map((edge) => edge.type))];
    const rawNodeById = new Map(rawGraph.nodes.map((node) => [node.id, node]));
    let currentVisibleNodes = [];
    let currentVisibleEdges = [];
    let selectedNodeId = null;

    document.getElementById("total-nodes").textContent = rawGraph.nodes.length;
    document.getElementById("total-edges").textContent = rawGraph.edges.length;

    function escapeHtml(value) {
      return String(value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
    }

    function buildFilters(containerId, values, attributeName) {
      const container = document.getElementById(containerId);
      container.innerHTML = values
        .map((value) => `
          <label class="chip">
            <input type="checkbox" ${attributeName}="${value}" checked>
            <span>${escapeHtml(attributeName === "data-node-type" ? (nodeTypeLabels[value] || value) : (edgeTypeLabels[value] || value))}</span>
          </label>
        `)
        .join("");
    }

    buildFilters("node-type-filters", allNodeTypes, "data-node-type");
    buildFilters("edge-type-filters", allEdgeTypes, "data-edge-type");

    const nodesDataSet = new vis.DataSet([]);
    const edgesDataSet = new vis.DataSet([]);
    const network = new vis.Network(
      document.getElementById("network"),
      { nodes: nodesDataSet, edges: edgesDataSet },
      {
        physics: { stabilization: false },
        nodes: {
          shape: "dot",
          borderWidth: 1,
          font: { face: "Arial", size: 14 }
        },
        edges: {
          arrows: "to",
          color: { color: "#94a3b8", highlight: "#2f6feb" },
          font: { align: "top", size: 11 },
          smooth: { type: "dynamic" }
        },
        interaction: {
          hover: true,
          navigationButtons: true,
          keyboard: true
        }
      }
    );

    function selectedValues(selector, attributeName) {
      return new Set(
        Array.from(document.querySelectorAll(selector))
          .filter((input) => input.checked)
          .map((input) => input.getAttribute(attributeName))
      );
    }

    function getGraphViewMode() {
      const selected = document.querySelector('input[name="graph-view-mode"]:checked');
      return selected ? selected.value : "account";
    }

    function formatNodeType(value) {
      return nodeTypeLabels[value] || value;
    }

    function formatEdgeType(value) {
      return edgeTypeLabels[value] || value;
    }

    function formatEvidenceKind(value) {
      return evidenceKindLabels[value] || value || "事実";
    }

    function formatBasis(value) {
      return basisLabels[value] || value || "-";
    }

    function formatDecisionStatus(value) {
      return decisionStatusLabels[value] || value || "-";
    }

    function matchesSearch(node, term) {
      if (!term) {
        return true;
      }
      const haystack = [node.id, node.name, node.description, ...(node.aliases || [])]
        .join(" ")
        .toLowerCase();
      return haystack.includes(term);
    }

    function formatLinkList(urls) {
      if (!urls || !urls.length) {
        return '<span class="muted">-</span>';
      }
      return `
        <ul class="source-list">
          ${urls
            .map(
              (url) =>
                `<li><a href="${escapeHtml(url)}" target="_blank" rel="noopener">${escapeHtml(url)}</a></li>`
            )
            .join("")}
        </ul>
      `;
    }

    function formatNodeAvatar(node, className) {
      if (!node || !node.icon_url) {
        return "";
      }
      return `<img class="${escapeHtml(className)}" src="${escapeHtml(node.icon_url)}" alt="${escapeHtml(node.name)} icon" loading="lazy">`;
    }

    function formatEvidenceBadges(item) {
      const badges = [
        `<span class="tag tag-evidence-${escapeHtml(item.evidence_kind || "fact")}">${escapeHtml(formatEvidenceKind(item.evidence_kind || "fact"))}</span>`
      ];
      if (item.needs_review) {
        badges.push('<span class="tag tag-review">要確認</span>');
      }
      return badges.join("");
    }

    function renderDetailList(edges, direction) {
      if (!edges.length) {
        return '<li class="muted">-</li>';
      }
      return edges
        .map((edge) => {
          const otherId = direction === "outgoing" ? edge.target : edge.source;
          const otherNode = rawNodeById.get(otherId);
          return `
            <li>
              <strong>${escapeHtml(otherNode ? otherNode.name : otherId)}</strong>
              <span class="tag">${escapeHtml(formatEdgeType(edge.type))}</span>
              ${formatEvidenceBadges(edge)}<br>
              <span>${escapeHtml(edge.description || "")}</span><br>
              <span class="muted">確信度: ${escapeHtml(edge.confidence)}</span>
              ${edge.review_notes ? `<br><span class="muted">確認メモ: ${escapeHtml(edge.review_notes)}</span>` : ""}
              ${formatLinkList(edge.source_urls || [])}
            </li>
          `;
        })
        .join("");
    }

    function renderDetailPanel(nodeId) {
      const panel = document.getElementById("detail-panel");
      const node = currentVisibleNodes.find((candidate) => candidate.id === nodeId);
      if (!node) {
        selectedNodeId = null;
        panel.innerHTML =
          '<div class="detail-empty">相関図かノード一覧から 1 件選ぶと、説明・source URLs・入出力エッジをここに表示します。</div>';
        return;
      }

      selectedNodeId = nodeId;
      const outgoingEdges = currentVisibleEdges.filter((edge) => edge.source === nodeId);
      const incomingEdges = currentVisibleEdges.filter((edge) => edge.target === nodeId);
      panel.innerHTML = `
        <div class="detail-card">
          <div class="detail-heading">
            ${formatNodeAvatar(node, "detail-avatar")}
            <h3>${escapeHtml(node.name)}</h3>
          </div>
          <div class="detail-meta">
            <span class="tag">${escapeHtml(formatNodeType(node.type))}</span>
            ${formatEvidenceBadges(node)}
            <span class="muted">${escapeHtml(node.id)}</span><br>
            <span class="muted">確信度: ${escapeHtml(node.confidence)}</span>
            ${node.review_notes ? `<br><span class="muted">確認メモ: ${escapeHtml(node.review_notes)}</span>` : ""}
          </div>

          <div class="detail-section">
            <h4>説明</h4>
            <div>${escapeHtml(node.description || "-")}</div>
          </div>

          <div class="detail-section">
            <h4>別名</h4>
            <div>${escapeHtml((node.aliases || []).join(", ") || "-")}</div>
          </div>

          <div class="detail-section">
            <h4>出典</h4>
            ${formatLinkList(node.source_urls || [])}
          </div>

          <div class="detail-section">
            <h4>出力関係 (${outgoingEdges.length})</h4>
            <ul class="detail-list">${renderDetailList(outgoingEdges, "outgoing")}</ul>
          </div>

          <div class="detail-section">
            <h4>入力関係 (${incomingEdges.length})</h4>
            <ul class="detail-list">${renderDetailList(incomingEdges, "incoming")}</ul>
          </div>
        </div>
      `;
    }

    function focusNode(nodeId) {
      if (!currentVisibleNodes.some((node) => node.id === nodeId)) {
        return;
      }
      network.selectNodes([nodeId]);
      network.focus(nodeId, {
        scale: 1.05,
        animation: {
          duration: 300,
          easingFunction: "easeInOutQuad"
        }
      });
      renderDetailPanel(nodeId);
    }

    function renderNodeTable(nodes) {
      const tbody = document.getElementById("nodes-table");
      tbody.innerHTML = nodes
        .map((node) => {
          return `
            <tr>
              <td><button type="button" class="inspect-button" data-node-id="${escapeHtml(node.id)}">詳細</button></td>
              <td><div class="node-name-cell">${formatNodeAvatar(node, "avatar-thumb")}<div class="node-name-text"><strong>${escapeHtml(node.name)}</strong><br><span class="muted">${escapeHtml(node.id)}</span></div></div></td>
              <td><span class="tag">${escapeHtml(formatNodeType(node.type))}</span></td>
              <td>${escapeHtml((node.aliases || []).join(", "))}</td>
              <td>${formatEvidenceBadges(node)}<br>${escapeHtml(node.description || "")}${node.review_notes ? `<br><span class="muted">確認メモ: ${escapeHtml(node.review_notes)}</span>` : ""}</td>
              <td>${escapeHtml(node.confidence)}</td>
              <td>${formatLinkList(node.source_urls || [])}</td>
            </tr>
          `;
        })
        .join("");
    }

    function renderEdgeTable(edges, nodeNameById) {
      const tbody = document.getElementById("edges-table");
      tbody.innerHTML = edges
        .map((edge) => {
          return `
            <tr>
              <td>${escapeHtml(nodeNameById.get(edge.source) || edge.source)}</td>
              <td><span class="tag">${escapeHtml(formatEdgeType(edge.type))}</span></td>
              <td>${escapeHtml(nodeNameById.get(edge.target) || edge.target)}</td>
              <td>${formatEvidenceBadges(edge)}<br>${escapeHtml(edge.description || "")}${edge.review_notes ? `<br><span class="muted">確認メモ: ${escapeHtml(edge.review_notes)}</span>` : ""}</td>
              <td>${escapeHtml(edge.confidence)}</td>
              <td>${formatLinkList(edge.source_urls || [])}</td>
            </tr>
          `;
        })
        .join("");
    }

    function renderReviewNodeTable(nodes) {
      const tbody = document.getElementById("review-nodes-table");
      if (!nodes.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="muted">現在表示中の要確認ノードはありません。</td></tr>';
        return;
      }
      tbody.innerHTML = nodes
        .map((node) => `
          <tr>
            <td><button type="button" class="inspect-button" data-node-id="${escapeHtml(node.id)}">詳細</button></td>
            <td><div class="node-name-cell">${formatNodeAvatar(node, "avatar-thumb")}<div class="node-name-text"><strong>${escapeHtml(node.name)}</strong><br><span class="muted">${escapeHtml(node.id)}</span></div></div></td>
            <td><span class="tag">${escapeHtml(formatNodeType(node.type))}</span></td>
            <td>${formatEvidenceBadges(node)}<br>${escapeHtml(node.review_notes || "-")}</td>
            <td>${formatLinkList(node.source_urls || [])}</td>
          </tr>
        `)
        .join("");
    }

    function renderReviewEdgeTable(edges, nodeNameById) {
      const tbody = document.getElementById("review-edges-table");
      if (!edges.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="muted">現在表示中の要確認エッジはありません。</td></tr>';
        return;
      }
      tbody.innerHTML = edges
        .map((edge) => `
          <tr>
            <td>${escapeHtml(nodeNameById.get(edge.source) || edge.source)}</td>
            <td><span class="tag">${escapeHtml(formatEdgeType(edge.type))}</span></td>
            <td>${escapeHtml(nodeNameById.get(edge.target) || edge.target)}</td>
            <td>${formatEvidenceBadges(edge)}<br>${escapeHtml(edge.review_notes || "-")}</td>
            <td>${formatLinkList(edge.source_urls || [])}</td>
          </tr>
        `)
        .join("");
    }

    function renderReviewCandidateTable(candidates, nodeNameById) {
      const tbody = document.getElementById("review-candidates-table");
      if (!candidates.length) {
        tbody.innerHTML = '<tr><td colspan="6" class="muted">現在の表示条件に一致するレビュー候補はありません。</td></tr>';
        return;
      }
      tbody.innerHTML = candidates
        .map((candidate) => `
          <tr>
            <td>${escapeHtml(nodeNameById.get(candidate.source) || candidate.source)}</td>
            <td><span class="tag">${escapeHtml(formatEdgeType(candidate.type))}</span></td>
            <td>${escapeHtml(nodeNameById.get(candidate.target) || candidate.target)}</td>
            <td><span class="tag">${escapeHtml(formatBasis(candidate.basis))}</span><br><span class="muted">一致語: ${escapeHtml(candidate.matched_text || "-")}</span></td>
            <td><span class="tag tag-review">要確認</span><br>${escapeHtml(candidate.review_notes || "-")}<br><span class="muted">${escapeHtml(candidate.evidence_text || "")}</span></td>
            <td>${formatLinkList(candidate.source_urls || [])}</td>
          </tr>
        `)
        .join("");
    }

    function normalizeDecisionEntry(candidateId, decision) {
      const parts = String(candidateId || "").split("__");
      return {
        candidate_id: String(candidateId || "").trim(),
        status: String(decision.status || "").trim(),
        note: String(decision.note || "").trim(),
        source: String(decision.source || parts[0] || "").trim(),
        target: String(decision.target || parts[1] || "").trim(),
        type: String(decision.type || parts[2] || "").trim(),
        basis: String(decision.basis || parts[3] || "").trim(),
        matched_text: String(decision.matched_text || "").trim(),
        evidence_text: String(decision.evidence_text || "").trim(),
        updated_at: String(decision.updated_at || "").trim(),
        source_urls: Array.isArray(decision.source_urls) ? decision.source_urls : []
      };
    }

    function renderReviewCandidateDecisionTable(decisionEntries, nodeNameById) {
      const tbody = document.getElementById("review-candidate-decisions-table");
      if (!decisionEntries.length) {
        tbody.innerHTML = '<tr><td colspan="6" class="muted">現在の表示条件に一致するレビュー判断はありません。</td></tr>';
        return;
      }
      tbody.innerHTML = decisionEntries
        .map((entry) => `
          <tr>
            <td>${escapeHtml(nodeNameById.get(entry.source) || entry.source || "-")}<br><span class="muted">${escapeHtml(formatEdgeType(entry.type || "-"))}</span></td>
            <td><span class="tag ${entry.status === "approved" ? "tag-evidence-fact" : "tag-review"}">${escapeHtml(formatDecisionStatus(entry.status || "-"))}</span><br><span class="muted">${escapeHtml(entry.updated_at || "-")}</span></td>
            <td>${escapeHtml(nodeNameById.get(entry.target) || entry.target || "-")}</td>
            <td><span class="tag">${escapeHtml(formatBasis(entry.basis || "-"))}</span>${entry.matched_text ? `<br><span class="muted">一致語: ${escapeHtml(entry.matched_text)}</span>` : ""}</td>
            <td>${escapeHtml(entry.note || "-")}${entry.evidence_text ? `<br><span class="muted">${escapeHtml(entry.evidence_text)}</span>` : ""}${entry.candidate_id ? `<br><span class="muted">${escapeHtml(entry.candidate_id)}</span>` : ""}</td>
            <td>${formatLinkList(entry.source_urls || [])}</td>
          </tr>
        `)
        .join("");
    }

    function applyFilters() {
      const allowedNodeTypes = selectedValues("[data-node-type]", "data-node-type");
      const allowedEdgeTypes = selectedValues("[data-edge-type]", "data-edge-type");
      const term = document.getElementById("search").value.trim().toLowerCase();
      const graphViewMode = getGraphViewMode();

      const eligibleNodes = rawGraph.nodes.filter((node) => {
        if (!allowedNodeTypes.has(node.type)) {
          return false;
        }
        if (graphViewMode === "full") {
          return true;
        }
        return accountNodeTypes.has(node.type);
      });
      const eligibleIds = new Set(eligibleNodes.map((node) => node.id));
      const matchedIds = new Set(
        eligibleNodes.filter((node) => matchesSearch(node, term)).map((node) => node.id)
      );

      const visibleEdges = rawGraph.edges.filter((edge) => {
        if (!allowedEdgeTypes.has(edge.type)) {
          return false;
        }
        if (!eligibleIds.has(edge.source) || !eligibleIds.has(edge.target)) {
          return false;
        }
        if (!term) {
          return true;
        }
        return matchedIds.has(edge.source) || matchedIds.has(edge.target);
      });

      const visibleNodeIds = new Set(term ? matchedIds : eligibleIds);
      visibleEdges.forEach((edge) => {
        visibleNodeIds.add(edge.source);
        visibleNodeIds.add(edge.target);
      });

      const visibleNodes = eligibleNodes.filter((node) => visibleNodeIds.has(node.id));
      const nodeNameById = new Map(rawGraph.nodes.map((node) => [node.id, node.name]));
      const visibleReviewCandidates = (rawReviewCandidates.candidates || []).filter((candidate) =>
        visibleNodeIds.has(candidate.source) && visibleNodeIds.has(candidate.target)
      );
      const visibleReviewCandidateDecisions = Object.entries(rawReviewCandidateDecisions.decisions || {})
        .map(([candidateId, decision]) => normalizeDecisionEntry(candidateId, decision || {}))
        .filter((entry) => visibleNodeIds.has(entry.source) && visibleNodeIds.has(entry.target));
      currentVisibleNodes = visibleNodes;
      currentVisibleEdges = visibleEdges;

      nodesDataSet.clear();
      edgesDataSet.clear();

      nodesDataSet.add(
        visibleNodes.map((node) => ({
          id: node.id,
          label: node.name,
          group: node.type,
          value: 12 + Math.round((node.confidence || 0) * 12),
          shape: node.icon_url ? "circularImage" : "dot",
          image: node.icon_url || undefined,
          brokenImage: "icon.svg",
          color: {
            background: nodeColors[node.type] || "#64748b",
            border: "#ffffff",
            highlight: { background: nodeColors[node.type] || "#64748b", border: "#111827" }
          },
          title: `${node.name} (${formatNodeType(node.type)})\n${node.description || ""}\n${formatEvidenceKind(node.evidence_kind || "fact")}${node.needs_review ? " / 要確認" : ""}`
        }))
      );

      edgesDataSet.add(
        visibleEdges.map((edge, index) => ({
          id: `${edge.source}-${edge.target}-${edge.type}-${index}`,
          from: edge.source,
          to: edge.target,
          label: formatEdgeType(edge.type),
          title: `${formatEdgeType(edge.type)}: ${edge.description || ""}\n${formatEvidenceKind(edge.evidence_kind || "fact")}${edge.needs_review ? " / 要確認" : ""}`
        }))
      );

      document.getElementById("visible-nodes").textContent = visibleNodes.length;
      document.getElementById("visible-edges").textContent = visibleEdges.length;
      document.getElementById("review-nodes").textContent = visibleNodes.filter((node) => node.needs_review).length;
      document.getElementById("review-edges").textContent = visibleEdges.filter((edge) => edge.needs_review).length;
      document.getElementById("review-candidates").textContent = visibleReviewCandidates.length;
      renderNodeTable(visibleNodes);
      renderEdgeTable(visibleEdges, nodeNameById);
      renderReviewNodeTable(visibleNodes.filter((node) => node.needs_review));
      renderReviewEdgeTable(visibleEdges.filter((edge) => edge.needs_review), nodeNameById);
      renderReviewCandidateTable(visibleReviewCandidates, nodeNameById);
      renderReviewCandidateDecisionTable(visibleReviewCandidateDecisions, nodeNameById);

      if (selectedNodeId) {
        renderDetailPanel(selectedNodeId);
      } else {
        renderDetailPanel(null);
      }
    }

    document.getElementById("search").addEventListener("input", applyFilters);
    document.querySelectorAll("[data-node-type], [data-edge-type]").forEach((input) => {
      input.addEventListener("change", applyFilters);
    });
    document.querySelectorAll('input[name="graph-view-mode"]').forEach((input) => {
      input.addEventListener("change", applyFilters);
    });
    document.getElementById("nodes-table").addEventListener("click", (event) => {
      const button = event.target.closest("[data-node-id]");
      if (!button) {
        return;
      }
      focusNode(button.getAttribute("data-node-id"));
    });
    document.getElementById("review-nodes-table").addEventListener("click", (event) => {
      const button = event.target.closest("[data-node-id]");
      if (!button) {
        return;
      }
      focusNode(button.getAttribute("data-node-id"));
    });
    network.on("selectNode", (params) => {
      if (params.nodes.length) {
        renderDetailPanel(params.nodes[0]);
      }
    });
    network.on("deselectNode", () => {
      renderDetailPanel(null);
    });

    applyFilters();
  </script>
</body>
</html>
"""
    return (
        template.replace("__TITLE__", title)
        .replace("__GRAPH_JSON__", graph_json)
        .replace("__REVIEW_CANDIDATES_JSON__", review_candidates_json)
        .replace("__REVIEW_CANDIDATE_DECISIONS_JSON__", review_candidate_decisions_json)
        .replace("__GROWTH_HEADLINE__", growth_headline)
        .replace("__GROWTH_PHASE_CARDS__", growth_phase_cards)
        .replace("__GROWTH_TYPE_ROWS__", growth_type_rows)
        .replace("__GENERATED_AT__", generated_at)
    )


def export_html(
    graph: GraphData,
    output_path: str | Path = "docs/index.html",
    title: str = "Pickup Artist Network",
    review_candidates_payload: dict[str, Any] | None = None,
    review_candidate_decisions_payload: dict[str, Any] | None = None,
    growth_targets_payload: dict[str, Any] | None = None,
) -> None:
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(
        render_html(
            graph,
            title=title,
            review_candidates_payload=review_candidates_payload,
            review_candidate_decisions_payload=review_candidate_decisions_payload,
            growth_targets_payload=growth_targets_payload,
        ),
        encoding="utf-8",
    )
