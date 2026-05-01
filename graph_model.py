from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import escape
from itertools import combinations
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
    "follow",
    "profile_mention",
)
EVIDENCE_KINDS = ("fact", "interpretation", "mixed")
ACCOUNT_NODE_TYPES = frozenset({"person", "community"})
CLUSTER_MEMBER_NODE_TYPES = frozenset({"person"})
GENERIC_CLUSTER_CONTEXT_IDS = frozenset({"x", "line", "note", "youtube", "instagram", "brain", "tips"})
CLUSTER_MODE_DEFINITIONS = {
    "connectivity": {
        "label": "つながりの近さでまとめる",
        "description": "相互リンクや共通のつながりが濃い人たちを自動でまとめます。",
        "min_size": 3,
    },
    "relation_pattern": {
        "label": "関係パターンでまとめる",
        "description": "師弟・相互言及・同地域など、似た関係が重なる人たちをまとめます。",
        "min_size": 3,
    },
    "keyword_group": {
        "label": "キーワードでまとめる",
        "description": "MBH や セクシーコマンドー、ピカ講習、アツスト など、公開プロフィールのキーワードでまとめます。",
        "min_size": 2,
    },
}
CONNECTIVITY_DIRECT_WEIGHTS = {
    "influence": 2.8,
    "affiliation": 2.4,
    "collaboration": 2.2,
    "criticism": 1.4,
    "monetization": 1.8,
    "activity": 1.2,
    "follow": 1.9,
    "profile_mention": 1.4,
}
CONNECTIVITY_CONTEXT_WEIGHTS = {
    "community": 2.0,
    "location": 1.8,
    "platform": 0.45,
    "content": 0.8,
}
RELATION_PATTERN_DIRECT_WEIGHTS = {
    "influence": 3.4,
    "affiliation": 3.0,
    "collaboration": 2.8,
    "criticism": 1.2,
    "monetization": 2.0,
    "activity": 1.6,
    "follow": 2.6,
    "profile_mention": 1.8,
}
RELATION_PATTERN_CONTEXT_WEIGHTS = {
    "community": 2.8,
    "location": 2.4,
    "platform": 0.3,
    "content": 1.1,
}
CLUSTER_PRUNE_CONFIG = {
    "connectivity": {
        "min_weight": 1.25,
        "max_neighbors": 6,
    },
    "relation_pattern": {
        "min_weight": 1.55,
        "max_neighbors": 5,
    },
}
KEYWORD_CLUSTER_RULES = (
    {"id": "mbh", "label": "MBH", "patterns": ("mbh",), "priority": 100},
    {
        "id": "pika_lessons",
        "label": "ピカ講習 / ピカ外見コンサル",
        "patterns": ("ピカ講習", "ピカ外見コンサル", "ピカチュウ メンズ外見コンサル", "pika_stochi"),
        "priority": 99,
    },
    {"id": "m_street_club", "label": "mスト部", "patterns": ("mスト部", "mスト"), "priority": 98},
    {
        "id": "sexy_commando",
        "label": "セクシーコマンドー",
        "patterns": ("セクシーコマンドー", "sc一門"),
        "priority": 97,
    },
    {"id": "wing_longterm", "label": "wing長期", "patterns": ("wing長期",), "priority": 96},
    {"id": "atsust", "label": "アツスト", "patterns": ("アツスト",), "priority": 94},
    {"id": "hancho", "label": "はんちょう", "patterns": ("はんちょう", "hancho"), "priority": 92},
    {"id": "juru_family", "label": "ジュルマ一門", "patterns": ("ジュルマ一門", "juru"), "priority": 91},
    {"id": "yutty", "label": "ゆってぃ", "patterns": ("ゆってぃ", "yutty"), "priority": 90},
    {"id": "wing", "label": "wing", "patterns": ("wing",), "priority": 89},
    {"id": "kurita", "label": "栗田", "patterns": ("栗田", "kurita"), "priority": 88},
    {
        "id": "korilla_m_lessons",
        "label": "こりらm氏講習",
        "patterns": ("こりらm氏講習", "こりら", "m氏講習"),
        "priority": 87,
    },
    {"id": "nonchama", "label": "のんちゃま", "patterns": ("のんちゃま", "nonchama"), "priority": 86},
    {"id": "yuka_gundan", "label": "雄華軍団", "patterns": ("雄華軍団",), "priority": 85},
    {"id": "pochama", "label": "ポチャマ", "patterns": ("ポチャマ", "pochama"), "priority": 84},
    {"id": "golazo", "label": "ゴラッソ長期", "patterns": ("ゴラッソ長期", "ゴラッソ"), "priority": 83},
    {"id": "next", "label": "ネクステ", "patterns": ("ネクステ",), "priority": 80},
    {"id": "miso", "label": "味噌", "patterns": ("味噌", "みそ"), "priority": 78},
    {"id": "otaku", "label": "オタク", "patterns": ("オタク", "otaku"), "priority": 74},
)


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


def _add_weighted_account_edge(account_graph: Any, source: str, target: str, weight: float) -> None:
    if source == target or weight <= 0:
        return
    if account_graph.has_edge(source, target):
        account_graph[source][target]["weight"] += round(weight, 4)
    else:
        account_graph.add_edge(source, target, weight=round(weight, 4))


def _build_account_projection(
    graph: GraphData,
    mode_key: str,
) -> tuple[Any, dict[str, set[str]]]:
    import networkx as nx

    if mode_key == "connectivity":
        direct_weights = CONNECTIVITY_DIRECT_WEIGHTS
        context_weights = CONNECTIVITY_CONTEXT_WEIGHTS
    elif mode_key == "relation_pattern":
        direct_weights = RELATION_PATTERN_DIRECT_WEIGHTS
        context_weights = RELATION_PATTERN_CONTEXT_WEIGHTS
    else:
        raise ValueError(f"Unsupported cluster mode: {mode_key}")

    nodes_by_id = {node.id: node for node in graph.nodes}
    account_graph = nx.Graph()
    for node in graph.nodes:
        if node.type in ACCOUNT_NODE_TYPES:
            account_graph.add_node(node.id, name=node.name, type=node.type)

    direct_pair_directions: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    direct_pair_types: dict[tuple[str, str], set[str]] = defaultdict(set)
    context_links: dict[str, dict[str, list[Edge]]] = defaultdict(lambda: defaultdict(list))

    for edge in graph.edges:
        source_node = nodes_by_id.get(edge.source)
        target_node = nodes_by_id.get(edge.target)
        if source_node is None or target_node is None:
            continue
        source_is_account = source_node.type in ACCOUNT_NODE_TYPES
        target_is_account = target_node.type in ACCOUNT_NODE_TYPES
        if source_is_account and target_is_account:
            pair_key = tuple(sorted((edge.source, edge.target)))
            direct_pair_directions[pair_key].add((edge.source, edge.target))
            direct_pair_types[pair_key].add(edge.type)
            base_weight = direct_weights.get(edge.type, 1.0) * max(edge.confidence, 0.35)
            _add_weighted_account_edge(account_graph, edge.source, edge.target, base_weight)
            continue
        if source_is_account == target_is_account:
            continue
        account_id = edge.source if source_is_account else edge.target
        context_id = edge.target if source_is_account else edge.source
        context_node = nodes_by_id.get(context_id)
        if context_node is None or context_node.type not in context_weights:
            continue
        context_links[context_id][account_id].append(edge)

    if mode_key == "relation_pattern":
        for pair_key, directions in direct_pair_directions.items():
            bonus = 0.0
            if len(directions) > 1:
                bonus += 1.0
            bonus += max(0, len(direct_pair_types[pair_key]) - 1) * 0.45
            if bonus:
                _add_weighted_account_edge(account_graph, pair_key[0], pair_key[1], bonus)

    account_contexts: dict[str, set[str]] = defaultdict(set)
    for context_id, account_edge_map in context_links.items():
        context_node = nodes_by_id.get(context_id)
        if context_node is None:
            continue
        account_items = [(account_id, edges) for account_id, edges in account_edge_map.items() if account_id in account_graph]
        for account_id, _ in account_items:
            account_contexts[account_id].add(context_id)
        if len(account_items) < 2:
            continue
        if context_node.type == "platform" and context_id in GENERIC_CLUSTER_CONTEXT_IDS:
            continue
        if context_node.type == "platform" and len(account_items) > 12:
            continue

        rarity_scale = 1 / max(1.0, (len(account_items) - 1) ** 0.5)
        context_weight = context_weights.get(context_node.type, 0.0)
        for (source_id, source_edges), (target_id, target_edges) in combinations(account_items, 2):
            combined_edges = [*source_edges, *target_edges]
            confidence = (
                sum(edge.confidence for edge in combined_edges) / len(combined_edges)
                if combined_edges
                else 0.5
            )
            weight = context_weight * rarity_scale * max(confidence, 0.35)
            if mode_key == "relation_pattern":
                source_types = {edge.type for edge in source_edges}
                target_types = {edge.type for edge in target_edges}
                shared_edge_types = source_types & target_types
                if "activity" in shared_edge_types:
                    weight += 1.0 * rarity_scale
                if "affiliation" in shared_edge_types:
                    weight += 0.9 * rarity_scale
                if "follow" in shared_edge_types:
                    weight += 0.6 * rarity_scale
                if "profile_mention" in shared_edge_types:
                    weight += 0.35 * rarity_scale
                if context_node.type == "location":
                    weight += 0.65 * rarity_scale
                elif context_node.type == "content":
                    weight += 0.25 * rarity_scale
            _add_weighted_account_edge(account_graph, source_id, target_id, weight)

    return account_graph, account_contexts


def _cluster_context_priority(node_type: str) -> int:
    return {
        "location": 3,
        "content": 2,
        "platform": 1,
    }.get(node_type, 0)


def _summarize_cluster(
    cluster_members: set[str],
    account_graph: Any,
    nodes_by_id: dict[str, Node],
    account_contexts: dict[str, set[str]],
) -> str:
    context_counts: Counter[str] = Counter()
    for member_id in cluster_members:
        for context_id in account_contexts.get(member_id, set()):
            if context_id in GENERIC_CLUSTER_CONTEXT_IDS:
                continue
            context_node = nodes_by_id.get(context_id)
            if context_node is None or context_node.type not in {"location", "content", "platform"}:
                continue
            context_counts[context_id] += 1

    if context_counts:
        best_context_id = max(
            context_counts,
            key=lambda context_id: (
                context_counts[context_id],
                _cluster_context_priority(nodes_by_id[context_id].type),
                nodes_by_id[context_id].name,
            ),
        )
        if context_counts[best_context_id] >= max(2, len(cluster_members) // 3):
            return f"{nodes_by_id[best_context_id].name} 周辺"

    anchor_id = max(
        cluster_members,
        key=lambda node_id: (account_graph.degree(node_id, weight="weight"), nodes_by_id[node_id].name),
    )
    return f"{nodes_by_id[anchor_id].name} 周辺"


def _keyword_text(node: Node) -> str:
    return " ".join(
        [
            node.id,
            node.name,
            node.description,
            *node.aliases,
        ]
    ).casefold()


def _build_keyword_cluster_mode_payload(
    graph: GraphData,
    definition: dict[str, Any],
) -> dict[str, Any]:
    nodes_by_id = {node.id: node for node in graph.nodes}
    buckets: dict[str, list[str]] = defaultdict(list)

    for node in graph.nodes:
        if node.type not in CLUSTER_MEMBER_NODE_TYPES:
            continue
        text = _keyword_text(node)
        best_rule: dict[str, Any] | None = None
        best_score = 0
        best_priority = -1
        for rule in KEYWORD_CLUSTER_RULES:
            score = sum(1 for pattern in rule["patterns"] if str(pattern).casefold() in text)
            if score <= 0:
                continue
            if score > best_score or (score == best_score and int(rule["priority"]) > best_priority):
                best_rule = rule
                best_score = score
                best_priority = int(rule["priority"])
        if best_rule is not None:
            buckets[str(best_rule["id"])].append(node.id)

    mode_payload = {
        "label": definition["label"],
        "description": definition["description"],
        "assignments": {},
        "clusters": {},
    }
    min_size = int(definition.get("min_size", 3))
    for rule in KEYWORD_CLUSTER_RULES:
        member_ids = sorted(
            buckets.get(str(rule["id"]), []),
            key=lambda node_id: nodes_by_id[node_id].name,
        )
        if len(member_ids) < min_size:
            continue
        cluster_id = f"keyword_group:{rule['id']}"
        preview = [nodes_by_id[node_id].name for node_id in member_ids[:4]]
        preview_suffix = f" ほか {len(member_ids) - len(preview)} 件" if len(member_ids) > len(preview) else ""
        mode_payload["clusters"][cluster_id] = {
            "label": f"{rule['label']} ({len(member_ids)})",
            "title": f"キーワード {rule['label']}: {', '.join(preview)}{preview_suffix}",
            "size": len(member_ids),
        }
        for node_id in member_ids:
            mode_payload["assignments"][node_id] = cluster_id

    return mode_payload


def build_relation_cluster_payload(graph: GraphData) -> dict[str, Any]:
    import networkx as nx

    nodes_by_id = {node.id: node for node in graph.nodes}
    payload = {"default_mode": "off", "modes": {}}

    for mode_key, definition in CLUSTER_MODE_DEFINITIONS.items():
        if mode_key == "keyword_group":
            payload["modes"][mode_key] = _build_keyword_cluster_mode_payload(graph, definition)
            continue
        account_graph, account_contexts = _build_account_projection(graph, mode_key)
        mode_payload = {
            "label": definition["label"],
            "description": definition["description"],
            "assignments": {},
            "clusters": {},
        }
        if account_graph.number_of_nodes():
            if account_graph.number_of_edges():
                communities = [
                    set(community)
                    for community in nx.community.greedy_modularity_communities(
                        account_graph,
                        weight="weight",
                    )
                ]
            else:
                communities = [{node_id} for node_id in account_graph.nodes]

            cluster_index = 1
            for community in sorted(communities, key=lambda item: (-len(item), sorted(item))):
                if len(community) < int(definition["min_size"]):
                    continue
                cluster_id = f"{mode_key}:{cluster_index}"
                summary = _summarize_cluster(community, account_graph, nodes_by_id, account_contexts)
                member_names = sorted(nodes_by_id[node_id].name for node_id in community if node_id in nodes_by_id)
                preview = member_names[:4]
                preview_suffix = f" ほか {len(member_names) - len(preview)} 件" if len(member_names) > len(preview) else ""
                mode_payload["clusters"][cluster_id] = {
                    "label": f"{summary} ({len(community)})",
                    "title": f"{summary}: {', '.join(preview)}{preview_suffix}",
                    "size": len(community),
                }
                for node_id in community:
                    mode_payload["assignments"][node_id] = cluster_id
                cluster_index += 1

        payload["modes"][mode_key] = mode_payload

    return payload


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
    site_data_path: str = "graph-data.json",
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

    growth_targets = growth_targets_payload or {"headline": {}, "phases": [], "types": []}
    headline = growth_targets.get("headline", {})
    growth_headline = (
        f"{headline.get('current', 0)} / {headline.get('target', 0)}"
        if headline
        else "-"
    )
    growth_description = (
        "公開プロフィールと公式ページを手動優先で整理しながら、実在人物の関係グラフを段階的に広げています。"
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
    .foldout {
      overflow: hidden;
    }
    .foldout summary {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      cursor: pointer;
      font-weight: 700;
      list-style: none;
    }
    .foldout summary::-webkit-details-marker {
      display: none;
    }
    .foldout-summary-text {
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    .foldout-content {
      margin-top: 16px;
    }
    .foldout .panel {
      background: #f9fbff;
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
    .control-select {
      width: 100%;
      box-sizing: border-box;
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 10px 12px;
      font-size: 14px;
      background: #fff;
      color: var(--text);
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
    .table-footer {
      margin-top: 10px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    .table-status {
      font-size: 12px;
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
    .connected-node-list {
      display: grid;
      gap: 10px;
    }
    .connected-type-group {
      display: grid;
      gap: 8px;
    }
    .connected-type-heading {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      font-size: 13px;
      color: var(--muted);
    }
    .connected-node-card {
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 10px;
      background: #f9fbff;
    }
    .connected-node-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
    }
    .connected-node-body {
      min-width: 0;
      flex: 1 1 auto;
    }
    .connected-node-tags {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 8px;
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
      <p class="muted">__GROWTH_DESCRIPTION__</p>
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
        <strong>表示方針</strong>
        <div class="muted">人物・コミュニティを中心に、関係が見やすいアカウント相関へ絞っています。</div>
      </div>
      <div>
        <label for="cluster-mode"><strong>関係クラスタ</strong></label>
        <select id="cluster-mode" class="control-select">
          <option value="off">まとめない</option>
          <option value="connectivity">つながりの近さでまとめる</option>
          <option value="relation_pattern">関係パターンでまとめる</option>
          <option value="keyword_group">キーワードでまとめる</option>
        </select>
        <div id="cluster-mode-help" class="muted">相互リンクや共通のつながりが濃い人たちを自動でまとめます。</div>
      </div>
      <div id="keyword-cluster-picker" hidden>
        <label for="keyword-cluster-select"><strong>キーワード群を選ぶ</strong></label>
        <select id="keyword-cluster-select" class="control-select"></select>
        <div class="muted">キーワード群を 1 つ選ぶと、その塊だけに絞って見られます。</div>
      </div>
      <details class="foldout">
        <summary>
          <span class="foldout-summary-text">
            <span>詳細フィルタ</span>
            <span class="muted">必要なときだけ、種別や関係で絞り込めます。</span>
          </span>
        </summary>
        <div class="foldout-content">
          <div>
            <strong>ノード種別</strong>
            <div id="node-type-filters" class="filter-group"></div>
          </div>
          <div style="margin-top: 16px;">
            <strong>関係種別</strong>
            <div id="edge-type-filters" class="filter-group"></div>
          </div>
        </div>
      </details>
    </section>

    <details class="panel foldout">
      <summary>
        <span class="foldout-summary-text">
          <span>内部メトリクス</span>
          <span class="muted">通常は閉じたまま使えるようにしました。</span>
        </span>
      </summary>
      <div class="foldout-content">
        <section class="stats">
          <div class="stat"><span class="muted">表示ノード数</span><strong id="visible-nodes">0</strong></div>
          <div class="stat"><span class="muted">表示エッジ数</span><strong id="visible-edges">0</strong></div>
          <div class="stat"><span class="muted">要確認ノード数</span><strong id="review-nodes">0</strong></div>
          <div class="stat"><span class="muted">要確認エッジ数</span><strong id="review-edges">0</strong></div>
          <div class="stat"><span class="muted">レビュー候補数</span><strong id="review-candidates">0</strong></div>
          <div class="stat"><span class="muted">総ノード数</span><strong id="total-nodes">0</strong></div>
          <div class="stat"><span class="muted">総エッジ数</span><strong id="total-edges">0</strong></div>
        </section>
      </div>
    </details>

    <section class="graph-layout">
      <section class="panel network-panel">
        <h2>アカウント相関ビュー</h2>
        <p class="muted">人物・コミュニティ同士のつながりを優先して、相関の見やすさを保っています。</p>
        <p class="muted">※ 現在の公開版は、公開プロフィールや公式ページで確認できた実在ノードのみを掲載しています。関係は明示的な記述を優先し、未確認の推測は含めません。</p>
        <div id="network"></div>
      </section>

      <aside class="panel detail-panel">
        <h2>選択ノード詳細</h2>
        <div id="detail-panel" class="detail-empty">相関図かノード一覧から 1 件選ぶと、右側に説明とつながっているノードを表示します。</div>
      </aside>
    </section>

    <details class="panel foldout">
      <summary>
        <span class="foldout-summary-text">
          <span>レビュー / 要確認データ</span>
          <span class="muted">普段は閉じ、必要なときだけ開けるようにしました。</span>
        </span>
      </summary>
      <div class="foldout-content">
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
            <div class="table-footer">
              <span id="review-nodes-table-status" class="table-status muted"></span>
              <button type="button" id="review-nodes-table-more" class="inspect-button" hidden>さらに表示</button>
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
            <div class="table-footer">
              <span id="review-edges-table-status" class="table-status muted"></span>
              <button type="button" id="review-edges-table-more" class="inspect-button" hidden>さらに表示</button>
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
          <div class="table-footer">
            <span id="review-candidates-table-status" class="table-status muted"></span>
            <button type="button" id="review-candidates-table-more" class="inspect-button" hidden>さらに表示</button>
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
          <div class="table-footer">
            <span id="review-candidate-decisions-table-status" class="table-status muted"></span>
            <button type="button" id="review-candidate-decisions-table-more" class="inspect-button" hidden>さらに表示</button>
          </div>
        </section>
      </div>
    </details>

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
        <div class="table-footer">
          <span id="nodes-table-status" class="table-status muted"></span>
          <button type="button" id="nodes-table-more" class="inspect-button" hidden>さらに表示</button>
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
        <div class="table-footer">
          <span id="edges-table-status" class="table-status muted"></span>
          <button type="button" id="edges-table-more" class="inspect-button" hidden>さらに表示</button>
        </div>
      </section>
    </section>
  </main>

  <script src="https://unpkg.com/vis-network@9.1.9/dist/vis-network.min.js"></script>
  <script>
    async function loadSiteData(path) {
      const response = await fetch(path, { cache: "no-store" });
      if (!response.ok) {
        throw new Error(`Failed to load graph data: ${response.status}`);
      }
      return response.json();
    }

    (async () => {
    const rawSiteData = await loadSiteData("__SITE_DATA_PATH__");
    const rawGraph = rawSiteData.graph || { nodes: [], edges: [] };
    const rawReviewCandidates = rawSiteData.review_candidates || { generated_at: "", candidates: [] };
    const rawReviewCandidateDecisions = rawSiteData.review_candidate_decisions || { updated_at: "", decisions: {} };
    const rawClusters = rawSiteData.clusters || { default_mode: "off", modes: {} };
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
      follow: "フォロー",
      profile_mention: "プロフィール言及"
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
    const clusterModeDefinitions = {
      off: {
        label: "まとめない",
        description: "通常表示です。人やコミュニティをまとめずに相関を見ます。"
      },
      connectivity: rawClusters.modes?.connectivity || {
        label: "つながりの近さでまとめる",
        description: "相互リンクや共通のつながりが濃い人たちを自動でまとめます。"
      },
      relation_pattern: rawClusters.modes?.relation_pattern || {
        label: "関係パターンでまとめる",
        description: "師弟・相互言及・同地域など、似た関係が重なる人たちをまとめます。"
      },
      keyword_group: rawClusters.modes?.keyword_group || {
        label: "キーワードでまとめる",
        description: "MBH や セクシーコマンドー など、公開プロフィールのキーワードでまとめます。"
      }
    };

    const allNodeTypes = [...new Set(rawGraph.nodes.map((node) => node.type))];
    const allEdgeTypes = [...new Set(rawGraph.edges.map((edge) => edge.type))];
    const rawNodeById = new Map(rawGraph.nodes.map((node) => [node.id, node]));
    let currentVisibleNodes = [];
    let currentVisibleEdges = [];
    let currentVisibleReviewNodes = [];
    let currentVisibleReviewEdges = [];
    let currentVisibleReviewCandidates = [];
    let currentVisibleReviewCandidateDecisions = [];
    let currentNodeNameById = new Map();
    let selectedNodeId = null;
    let activeClusterIds = new Set();
    let lastTableFilterKey = "";
    const tablePageSizes = {
      reviewNodes: 60,
      reviewEdges: 120,
      reviewCandidates: 80,
      reviewCandidateDecisions: 80,
      nodes: 120,
      edges: 200
    };
    const tableRenderState = { ...tablePageSizes };

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
    const clusterModeInput = document.getElementById("cluster-mode");
    const keywordClusterPicker = document.getElementById("keyword-cluster-picker");
    const keywordClusterInput = document.getElementById("keyword-cluster-select");
    if (clusterModeInput) {
      clusterModeInput.value = rawClusters.default_mode || "off";
    }

    const nodesDataSet = new vis.DataSet([]);
    const edgesDataSet = new vis.DataSet([]);
    const network = new vis.Network(
      document.getElementById("network"),
      { nodes: nodesDataSet, edges: edgesDataSet },
      {
        layout: {
          improvedLayout: true
        },
        physics: {
          enabled: true,
          stabilization: {
            enabled: true,
            iterations: 200,
            updateInterval: 25,
            fit: true
          },
          barnesHut: {
            gravitationalConstant: -10000,
            springLength: 130,
            springConstant: 0.03,
            damping: 0.18
          }
        },
        nodes: {
          shape: "dot",
          borderWidth: 1,
          font: { face: "Arial", size: 14 }
        },
        edges: {
          arrows: "to",
          color: { color: "#94a3b8", highlight: "#2f6feb" },
          font: { align: "top", size: 11 },
          smooth: false
        },
        interaction: {
          hover: true,
          navigationButtons: true,
          keyboard: true
        }
      }
    );
    network.once("stabilizationIterationsDone", () => {
      network.setOptions({ physics: false });
    });

    function selectedValues(selector, attributeName) {
      return new Set(
        Array.from(document.querySelectorAll(selector))
          .filter((input) => input.checked)
          .map((input) => input.getAttribute(attributeName))
      );
    }

    function getClusterMode() {
      return clusterModeInput ? clusterModeInput.value : "off";
    }

    function keywordClusterEntries() {
      return Object.entries(rawClusters.modes?.keyword_group?.clusters || {}).sort((left, right) =>
        String(left[1]?.label || left[0]).localeCompare(String(right[1]?.label || right[0]), "ja")
      );
    }

    function updateKeywordClusterOptions() {
      if (!keywordClusterInput) {
        return;
      }
      const currentValue = keywordClusterInput.value;
      const options = keywordClusterEntries();
      keywordClusterInput.innerHTML = [
        '<option value="">すべてのキーワード群</option>',
        ...options.map(
          ([clusterId, clusterInfo]) =>
            `<option value="${escapeHtml(clusterId)}">${escapeHtml(clusterInfo?.label || clusterId)}</option>`
        )
      ].join("");
      keywordClusterInput.value = options.some(([clusterId]) => clusterId === currentValue)
        ? currentValue
        : "";
    }

    function getSelectedKeywordClusterId() {
      if (getClusterMode() !== "keyword_group" || !keywordClusterInput) {
        return "";
      }
      return keywordClusterInput.value || "";
    }

    function updateKeywordClusterControl() {
      if (!keywordClusterPicker || !keywordClusterInput) {
        return;
      }
      const isKeywordMode = getClusterMode() === "keyword_group";
      keywordClusterPicker.hidden = !isKeywordMode;
      keywordClusterInput.disabled = !isKeywordMode;
    }

    function updateClusterModeHelp() {
      const help = document.getElementById("cluster-mode-help");
      if (!help) {
        return;
      }
      const mode = getClusterMode();
      const definition = clusterModeDefinitions[mode] || clusterModeDefinitions.off;
      help.textContent = definition.description;
    }

    function getClusterNodeId(clusterId) {
      return `cluster:${clusterId}`;
    }

    function resetTableRenderState() {
      Object.keys(tablePageSizes).forEach((key) => {
        tableRenderState[key] = tablePageSizes[key];
      });
    }

    function renderTableSlice({ items, tableKey, tbodyId, statusId, moreButtonId, emptyHtml, renderRow }) {
      const tbody = document.getElementById(tbodyId);
      const status = document.getElementById(statusId);
      const moreButton = document.getElementById(moreButtonId);
      if (!items.length) {
        tbody.innerHTML = emptyHtml;
        status.textContent = "0 件";
        moreButton.hidden = true;
        return;
      }
      const limit = tableRenderState[tableKey];
      const visibleItems = items.slice(0, limit);
      tbody.innerHTML = visibleItems.map(renderRow).join("");
      status.textContent = `${visibleItems.length} / ${items.length} 件表示`;
      const remaining = items.length - visibleItems.length;
      moreButton.hidden = remaining <= 0;
      moreButton.textContent = remaining > 0
        ? `さらに ${Math.min(tablePageSizes[tableKey], remaining)} 件表示`
        : "さらに表示";
    }

    function debounce(func, waitMs) {
      let timerId = null;
      return (...args) => {
        window.clearTimeout(timerId);
        timerId = window.setTimeout(() => func(...args), waitMs);
      };
    }

    function formatNodeType(value) {
      return nodeTypeLabels[value] || value;
    }

    function formatEdgeType(value) {
      return edgeTypeLabels[value] || value;
    }

    function renderNodeTypeTag(value) {
      if (!value || value === "person") {
        return "";
      }
      return `<span class="tag">${escapeHtml(formatNodeType(value))}</span>`;
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
              <br><span>${escapeHtml(edge.description || "")}</span><br>
              <span class="muted">確信度: ${escapeHtml(edge.confidence)}</span>
              ${edge.review_notes ? `<br><span class="muted">確認メモ: ${escapeHtml(edge.review_notes)}</span>` : ""}
              ${formatLinkList(edge.source_urls || [])}
            </li>
          `;
        })
        .join("");
    }

    function renderConnectedNodes(outgoingEdges, incomingEdges) {
      const grouped = new Map();
      const typeOrder = ["person", "community", "platform", "location", "content"];
      function addEdge(edge, direction) {
        const otherId = direction === "outgoing" ? edge.target : edge.source;
        const otherNode = rawNodeById.get(otherId);
        if (!otherNode) {
          return;
        }
        const entry = grouped.get(otherId) || {
          node: otherNode,
          incoming: new Set(),
          outgoing: new Set(),
          edgeCount: 0
        };
        entry[direction].add(formatEdgeType(edge.type));
        entry.edgeCount += 1;
        grouped.set(otherId, entry);
      }

      outgoingEdges.forEach((edge) => addEdge(edge, "outgoing"));
      incomingEdges.forEach((edge) => addEdge(edge, "incoming"));

      const entries = Array.from(grouped.values()).sort((left, right) =>
        right.edgeCount - left.edgeCount || left.node.name.localeCompare(right.node.name, "ja")
      );
      if (!entries.length) {
        return '<div class="detail-empty">現在表示中のつながりノードはありません。</div>';
      }

      const entriesByType = new Map();
      entries.forEach((entry) => {
        const nodeType = entry.node.type || "person";
        if (!entriesByType.has(nodeType)) {
          entriesByType.set(nodeType, []);
        }
        entriesByType.get(nodeType).push(entry);
      });

      return `
        <div class="connected-node-list">
          ${typeOrder
            .filter((nodeType) => entriesByType.has(nodeType))
            .map((nodeType) => `
              <section class="connected-type-group">
                <div class="connected-type-heading">
                  <strong>${escapeHtml(formatNodeType(nodeType))}</strong>
                  <span>${escapeHtml(entriesByType.get(nodeType).length)} 件</span>
                </div>
                ${entriesByType.get(nodeType).map((entry) => `
                  <div class="connected-node-card">
                    <div class="connected-node-header">
                      <div class="node-name-cell connected-node-body">
                        ${formatNodeAvatar(entry.node, "avatar-thumb")}
                        <div class="node-name-text">
                          <strong>${escapeHtml(entry.node.name)}</strong><br>
                          <span class="muted">${escapeHtml(entry.node.id)}</span>
                        </div>
                      </div>
                      <button type="button" class="inspect-button" data-node-id="${escapeHtml(entry.node.id)}">見る</button>
                    </div>
                    <div class="connected-node-tags">
                      ${Array.from(entry.outgoing).map((type) => `<span class="tag">→ ${escapeHtml(type)}</span>`).join("")}
                      ${Array.from(entry.incoming).map((type) => `<span class="tag">← ${escapeHtml(type)}</span>`).join("")}
                    </div>
                  </div>
                `).join("")}
              </section>
            `).join("")}
        </div>
      `;
    }

    function renderDetailPanel(nodeId) {
      const panel = document.getElementById("detail-panel");
      const node = currentVisibleNodes.find((candidate) => candidate.id === nodeId);
      if (!node) {
        selectedNodeId = null;
        panel.innerHTML =
          '<div class="detail-empty">相関図かノード一覧から 1 件選ぶと、右側に説明とつながっているノードを表示します。</div>';
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
            ${renderNodeTypeTag(node.type)}
            <span class="muted">${escapeHtml(node.id)}</span><br>
            <span class="muted">確信度: ${escapeHtml(node.confidence)}</span>
            ${node.review_notes ? `<br><span class="muted">確認メモ: ${escapeHtml(node.review_notes)}</span>` : ""}
          </div>

          <div class="detail-section">
            <h4>つながっているノード (${new Set([...outgoingEdges.map((edge) => edge.target), ...incomingEdges.map((edge) => edge.source)]).size})</h4>
            ${renderConnectedNodes(outgoingEdges, incomingEdges)}
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
      const clusterMode = getClusterMode();
      const clusterAssignment = rawClusters.modes?.[clusterMode]?.assignments?.[nodeId];
      const clusterId = clusterAssignment ? getClusterNodeId(clusterAssignment) : null;
      if (clusterId && network.isCluster(clusterId)) {
        network.openCluster(clusterId);
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
      renderTableSlice({
        items: nodes,
        tableKey: "nodes",
        tbodyId: "nodes-table",
        statusId: "nodes-table-status",
        moreButtonId: "nodes-table-more",
        emptyHtml: '<tr><td colspan="7" class="muted">現在の表示条件に一致するノードはありません。</td></tr>',
        renderRow: (node) => `
          <tr>
            <td><button type="button" class="inspect-button" data-node-id="${escapeHtml(node.id)}">詳細</button></td>
            <td><div class="node-name-cell">${formatNodeAvatar(node, "avatar-thumb")}<div class="node-name-text"><strong>${escapeHtml(node.name)}</strong><br><span class="muted">${escapeHtml(node.id)}</span></div></div></td>
            <td>${renderNodeTypeTag(node.type)}</td>
            <td>${escapeHtml((node.aliases || []).join(", "))}</td>
            <td>${escapeHtml(node.description || "")}${node.review_notes ? `<br><span class="muted">確認メモ: ${escapeHtml(node.review_notes)}</span>` : ""}</td>
            <td>${escapeHtml(node.confidence)}</td>
            <td>${formatLinkList(node.source_urls || [])}</td>
          </tr>
        `
      });
    }

    function renderEdgeTable(edges, nodeNameById) {
      renderTableSlice({
        items: edges,
        tableKey: "edges",
        tbodyId: "edges-table",
        statusId: "edges-table-status",
        moreButtonId: "edges-table-more",
        emptyHtml: '<tr><td colspan="6" class="muted">現在の表示条件に一致するエッジはありません。</td></tr>',
        renderRow: (edge) => `
          <tr>
            <td>${escapeHtml(nodeNameById.get(edge.source) || edge.source)}</td>
            <td><span class="tag">${escapeHtml(formatEdgeType(edge.type))}</span></td>
            <td>${escapeHtml(nodeNameById.get(edge.target) || edge.target)}</td>
            <td>${escapeHtml(edge.description || "")}${edge.review_notes ? `<br><span class="muted">確認メモ: ${escapeHtml(edge.review_notes)}</span>` : ""}</td>
            <td>${escapeHtml(edge.confidence)}</td>
            <td>${formatLinkList(edge.source_urls || [])}</td>
          </tr>
        `
      });
    }

    function renderReviewNodeTable(nodes) {
      renderTableSlice({
        items: nodes,
        tableKey: "reviewNodes",
        tbodyId: "review-nodes-table",
        statusId: "review-nodes-table-status",
        moreButtonId: "review-nodes-table-more",
        emptyHtml: '<tr><td colspan="5" class="muted">現在表示中の要確認ノードはありません。</td></tr>',
        renderRow: (node) => `
          <tr>
            <td><button type="button" class="inspect-button" data-node-id="${escapeHtml(node.id)}">詳細</button></td>
            <td><div class="node-name-cell">${formatNodeAvatar(node, "avatar-thumb")}<div class="node-name-text"><strong>${escapeHtml(node.name)}</strong><br><span class="muted">${escapeHtml(node.id)}</span></div></div></td>
            <td>${renderNodeTypeTag(node.type)}</td>
            <td>${escapeHtml(node.review_notes || "-")}</td>
            <td>${formatLinkList(node.source_urls || [])}</td>
          </tr>
        `
      });
    }

    function renderReviewEdgeTable(edges, nodeNameById) {
      renderTableSlice({
        items: edges,
        tableKey: "reviewEdges",
        tbodyId: "review-edges-table",
        statusId: "review-edges-table-status",
        moreButtonId: "review-edges-table-more",
        emptyHtml: '<tr><td colspan="5" class="muted">現在表示中の要確認エッジはありません。</td></tr>',
        renderRow: (edge) => `
          <tr>
            <td>${escapeHtml(nodeNameById.get(edge.source) || edge.source)}</td>
            <td><span class="tag">${escapeHtml(formatEdgeType(edge.type))}</span></td>
            <td>${escapeHtml(nodeNameById.get(edge.target) || edge.target)}</td>
            <td>${escapeHtml(edge.review_notes || "-")}</td>
            <td>${formatLinkList(edge.source_urls || [])}</td>
          </tr>
        `
      });
    }

    function renderReviewCandidateTable(candidates, nodeNameById) {
      renderTableSlice({
        items: candidates,
        tableKey: "reviewCandidates",
        tbodyId: "review-candidates-table",
        statusId: "review-candidates-table-status",
        moreButtonId: "review-candidates-table-more",
        emptyHtml: '<tr><td colspan="6" class="muted">現在の表示条件に一致するレビュー候補はありません。</td></tr>',
        renderRow: (candidate) => `
          <tr>
            <td>${escapeHtml(nodeNameById.get(candidate.source) || candidate.source)}</td>
            <td><span class="tag">${escapeHtml(formatEdgeType(candidate.type))}</span></td>
            <td>${escapeHtml(nodeNameById.get(candidate.target) || candidate.target)}</td>
            <td><span class="tag">${escapeHtml(formatBasis(candidate.basis))}</span><br><span class="muted">一致語: ${escapeHtml(candidate.matched_text || "-")}</span></td>
            <td>${escapeHtml(candidate.review_notes || "-")}<br><span class="muted">${escapeHtml(candidate.evidence_text || "")}</span></td>
            <td>${formatLinkList(candidate.source_urls || [])}</td>
          </tr>
        `
      });
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
      renderTableSlice({
        items: decisionEntries,
        tableKey: "reviewCandidateDecisions",
        tbodyId: "review-candidate-decisions-table",
        statusId: "review-candidate-decisions-table-status",
        moreButtonId: "review-candidate-decisions-table-more",
        emptyHtml: '<tr><td colspan="6" class="muted">現在の表示条件に一致するレビュー判断はありません。</td></tr>',
        renderRow: (entry) => `
          <tr>
            <td>${escapeHtml(nodeNameById.get(entry.source) || entry.source || "-")}<br><span class="muted">${escapeHtml(formatEdgeType(entry.type || "-"))}</span></td>
            <td><span class="tag ${entry.status === "approved" ? "tag-evidence-fact" : "tag-review"}">${escapeHtml(formatDecisionStatus(entry.status || "-"))}</span><br><span class="muted">${escapeHtml(entry.updated_at || "-")}</span></td>
            <td>${escapeHtml(nodeNameById.get(entry.target) || entry.target || "-")}</td>
            <td><span class="tag">${escapeHtml(formatBasis(entry.basis || "-"))}</span>${entry.matched_text ? `<br><span class="muted">一致語: ${escapeHtml(entry.matched_text)}</span>` : ""}</td>
            <td>${escapeHtml(entry.note || "-")}${entry.evidence_text ? `<br><span class="muted">${escapeHtml(entry.evidence_text)}</span>` : ""}${entry.candidate_id ? `<br><span class="muted">${escapeHtml(entry.candidate_id)}</span>` : ""}</td>
            <td>${formatLinkList(entry.source_urls || [])}</td>
          </tr>
        `
      });
    }

    function renderVisibleTables() {
      renderNodeTable(currentVisibleNodes);
      renderEdgeTable(currentVisibleEdges, currentNodeNameById);
      renderReviewNodeTable(currentVisibleReviewNodes);
      renderReviewEdgeTable(currentVisibleReviewEdges, currentNodeNameById);
      renderReviewCandidateTable(currentVisibleReviewCandidates, currentNodeNameById);
      renderReviewCandidateDecisionTable(currentVisibleReviewCandidateDecisions, currentNodeNameById);
    }

    function resetClusters() {
      Array.from(activeClusterIds).forEach((clusterId) => {
        if (network.isCluster(clusterId)) {
          network.openCluster(clusterId);
        }
      });
      activeClusterIds = new Set();
    }

    function applyRelationClusters(visibleNodes, clusterMode) {
      const modePayload = rawClusters.modes?.[clusterMode];
      if (!modePayload || !modePayload.assignments) {
        return;
      }
      const bucketMap = new Map();
      visibleNodes.forEach((node) => {
        const clusterId = modePayload.assignments[node.id];
        if (!clusterId) {
          return;
        }
        if (!bucketMap.has(clusterId)) {
          bucketMap.set(clusterId, []);
        }
        bucketMap.get(clusterId).push(node);
      });
      bucketMap.forEach((members, clusterId) => {
        if (members.length < 3) {
          return;
        }
        const memberIds = new Set(members.map((node) => node.id));
        const personCount = members.filter((node) => node.type === "person").length;
        const dominantType = personCount >= (members.length - personCount) ? "person" : "community";
        const clusterNodeId = getClusterNodeId(clusterId);
        const clusterInfo = modePayload.clusters?.[clusterId] || {};
        const definition = clusterModeDefinitions[clusterMode] || clusterModeDefinitions.off;
        network.cluster({
          joinCondition(nodeOptions) {
            return memberIds.has(nodeOptions.id);
          },
          clusterNodeProperties: {
            id: clusterNodeId,
            label: clusterInfo.label || `${definition.label} (${members.length})`,
            group: dominantType,
            value: 18 + members.length,
            shape: "dot",
            color: {
              background: nodeColors[dominantType] || "#64748b",
              border: "#ffffff",
              highlight: { background: nodeColors[dominantType] || "#64748b", border: "#111827" }
            },
            title: clusterInfo.title || `${definition.label}: ${members.length} 件`
          }
        });
        activeClusterIds.add(clusterNodeId);
      });
    }

    function applyFilters() {
      const allowedNodeTypes = selectedValues("[data-node-type]", "data-node-type");
      const allowedEdgeTypes = selectedValues("[data-edge-type]", "data-edge-type");
      const term = document.getElementById("search").value.trim().toLowerCase();
      const clusterMode = getClusterMode();
      const selectedKeywordClusterId = getSelectedKeywordClusterId();
      const keywordAssignments = rawClusters.modes?.keyword_group?.assignments || {};
      const shouldCluster = clusterMode !== "off" && !term && !selectedKeywordClusterId;
      const tableFilterKey = JSON.stringify({
        nodeTypes: [...allowedNodeTypes].sort(),
        edgeTypes: [...allowedEdgeTypes].sort(),
        term,
        clusterMode,
        keywordCluster: selectedKeywordClusterId
      });
      if (tableFilterKey !== lastTableFilterKey) {
        resetTableRenderState();
        lastTableFilterKey = tableFilterKey;
      }

      const eligibleNodes = rawGraph.nodes.filter((node) => {
        if (!allowedNodeTypes.has(node.type)) {
          return false;
        }
        if (!accountNodeTypes.has(node.type)) {
          return false;
        }
        if (selectedKeywordClusterId && keywordAssignments[node.id] !== selectedKeywordClusterId) {
          return false;
        }
        return true;
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

      const visibleNodeIds = new Set();
      if (term) {
        matchedIds.forEach((nodeId) => visibleNodeIds.add(nodeId));
      }
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
      currentVisibleReviewNodes = visibleNodes.filter((node) => node.needs_review);
      currentVisibleReviewEdges = visibleEdges.filter((edge) => edge.needs_review);
      currentVisibleReviewCandidates = visibleReviewCandidates;
      currentVisibleReviewCandidateDecisions = visibleReviewCandidateDecisions;
      currentNodeNameById = nodeNameById;

      resetClusters();
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
          title: [node.name + (node.type === "person" ? "" : ` (${formatNodeType(node.type)})`), node.description || ""]
            .filter((value) => value)
            .join("\n")
        }))
      );

      edgesDataSet.add(
        visibleEdges.map((edge, index) => ({
          id: `${edge.source}-${edge.target}-${edge.type}-${index}`,
          from: edge.source,
          to: edge.target,
          label: visibleEdges.length <= 320 ? formatEdgeType(edge.type) : undefined,
          title: `${formatEdgeType(edge.type)}: ${edge.description || ""}`
        }))
      );

      if (shouldCluster) {
        applyRelationClusters(visibleNodes, clusterMode);
      }

      document.getElementById("visible-nodes").textContent = visibleNodes.length;
      document.getElementById("visible-edges").textContent = visibleEdges.length;
      document.getElementById("review-nodes").textContent = currentVisibleReviewNodes.length;
      document.getElementById("review-edges").textContent = currentVisibleReviewEdges.length;
      document.getElementById("review-candidates").textContent = visibleReviewCandidates.length;
      renderVisibleTables();

      if (selectedNodeId) {
        renderDetailPanel(selectedNodeId);
      } else {
        renderDetailPanel(null);
      }
    }

    const debouncedApplyFilters = debounce(applyFilters, 120);
    document.getElementById("search").addEventListener("input", debouncedApplyFilters);
    document.querySelectorAll("[data-node-type], [data-edge-type]").forEach((input) => {
      input.addEventListener("change", applyFilters);
    });
    if (clusterModeInput) {
      clusterModeInput.addEventListener("change", () => {
        updateClusterModeHelp();
        updateKeywordClusterControl();
        applyFilters();
      });
    }
    if (keywordClusterInput) {
      updateKeywordClusterOptions();
      keywordClusterInput.addEventListener("change", applyFilters);
    }
    [
      ["reviewNodes", "review-nodes-table-more"],
      ["reviewEdges", "review-edges-table-more"],
      ["reviewCandidates", "review-candidates-table-more"],
      ["reviewCandidateDecisions", "review-candidate-decisions-table-more"],
      ["nodes", "nodes-table-more"],
      ["edges", "edges-table-more"]
    ].forEach(([tableKey, buttonId]) => {
      document.getElementById(buttonId).addEventListener("click", () => {
        tableRenderState[tableKey] += tablePageSizes[tableKey];
        renderVisibleTables();
      });
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
    document.getElementById("detail-panel").addEventListener("click", (event) => {
      const button = event.target.closest("[data-node-id]");
      if (!button) {
        return;
      }
      focusNode(button.getAttribute("data-node-id"));
    });
    network.on("selectNode", (params) => {
      if (params.nodes.length) {
        const selectedId = params.nodes[0];
        if (network.isCluster(selectedId)) {
          network.openCluster(selectedId);
          renderDetailPanel(null);
        } else {
          renderDetailPanel(selectedId);
        }
      }
    });
    network.on("deselectNode", () => {
      renderDetailPanel(null);
    });

    updateClusterModeHelp();
    updateKeywordClusterControl();
    applyFilters();
    })().catch((error) => {
      console.error(error);
      const networkElement = document.getElementById("network");
      if (networkElement) {
        networkElement.innerHTML = '<p class="muted">グラフデータの読み込みに失敗しました。</p>';
      }
    });
  </script>
</body>
</html>
"""
    return (
        template.replace("__TITLE__", title)
        .replace("__SITE_DATA_PATH__", site_data_path)
        .replace("__GROWTH_HEADLINE__", growth_headline)
        .replace("__GROWTH_DESCRIPTION__", growth_description)
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
    site_data_file = output_file.with_name("graph-data.json")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    relation_clusters_payload = build_relation_cluster_payload(graph)
    site_data_payload = {
        "graph": graph.to_dict(),
        "review_candidates": review_candidates_payload or {"generated_at": "", "candidates": []},
        "review_candidate_decisions": review_candidate_decisions_payload
        or {"updated_at": "", "decisions": {}},
        "clusters": relation_clusters_payload,
    }
    site_data_file.write_text(
        json.dumps(site_data_payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    output_file.write_text(
        render_html(
            graph,
            title=title,
            review_candidates_payload=review_candidates_payload,
            review_candidate_decisions_payload=review_candidate_decisions_payload,
            growth_targets_payload=growth_targets_payload,
            site_data_path=site_data_file.name,
        ),
        encoding="utf-8",
    )
