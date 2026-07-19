from __future__ import annotations

import csv
import json
import re
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
        "description": "MBH や セクシーコマンドー、ピカ講習、いわし長期、アツスト など、公開プロフィールのキーワードでまとめます。",
        "min_size": 2,
    },
    "region_group": {
        "label": "地域で大分類",
        "description": "東京・名古屋・大阪などの大きな地域でまとめ、講習や一門を中分類として見ます。",
        "min_size": 3,
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
        # Slightly more permissive weak-edge cut so Louvain sees enough structure without star-dominated graphs.
        "min_weight": 1.18,
        "max_neighbors": 7,
    },
    "relation_pattern": {
        # Keep fewer strongest ties per node so pattern-based communities stay interpretable.
        "min_weight": 1.48,
        "max_neighbors": 6,
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
    {"id": "iwashi_longterm", "label": "いわし長期", "patterns": ("いわし長期",), "priority": 95},
    {
        "id": "atsust",
        "label": "アツスト",
        "patterns": ("アツスト", "atsustreet", "🐶🦁", "犬住み"),
        "priority": 94,
    },
    {
        "id": "atsu_chill",
        "label": "あつ代表/△▽",
        "patterns": ("あつ代表", "あつ太郎", "sub_chilll", "pua_chilll", "eroeromancotin", "△▽"),
        "priority": 93,
    },
    {
        "id": "tokyo_stonan_kai",
        "label": "東京ストナン会",
        "patterns": ("東京ストナン会", "#東京ストナン会"),
        "priority": 92,
    },
    {"id": "elsta", "label": "えるスタ", "patterns": ("えるスタ", "elsta"), "priority": 93},
    {"id": "hancho", "label": "はんちょう", "patterns": ("はんちょう", "hancho"), "priority": 92},
    {"id": "juru_family", "label": "ジュルマ一門", "patterns": ("ジュルマ一門", "juru"), "priority": 91},
    {
        "id": "yutty",
        "label": "ゆってぃ",
        "patterns": ("ゆってぃ", "yutty", "ゆってぃ長期", "yutty_pua"),
        "priority": 90,
    },
    {
        "id": "ochimpo",
        "label": "おちんぽ侍",
        "patterns": (
            "おちんぽ侍",
            "ochimpo",
            "ochimpo_samurai",
            "ochimpo-samurai",
            "道玄坂おちんぽ",
            "道玄坂さん",
            "侍長期",
            "samurai_ochimpo",
        ),
        "priority": 95,
    },
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
    {"id": "teito", "label": "帝都", "patterns": ("帝都",), "priority": 82},
    {"id": "kurosaki_consult", "label": "黒崎コンサル", "patterns": ("黒崎コンサル",), "priority": 81},
    {"id": "miso", "label": "味噌", "patterns": ("味噌", "みそ"), "priority": 78},
    {"id": "otaku", "label": "オタク", "patterns": ("オタク", "otaku"), "priority": 74},
    {"id": "rio_lessons", "label": "りお講習", "patterns": ("りお講習",), "priority": 77},
    {"id": "ssb", "label": "SSB", "patterns": ("SSB", "ssb"), "priority": 76},
    {"id": "mentaiko", "label": "明太子", "patterns": ("明太子", "mentaiko"), "priority": 75},
    {"id": "toutaotoko", "label": "淘汰男", "patterns": ("淘汰男",), "priority": 73},
    {"id": "krt", "label": "KRT", "patterns": ("KRT",), "priority": 72},
    {"id": "toukare", "label": "東カレ", "patterns": ("東カレ",), "priority": 71},
    {"id": "rise_up_lab", "label": "RiseUpLab", "patterns": ("RiseUpLab", "rise_up"), "priority": 70},
    {"id": "nano_lessons", "label": "ナノ講習", "patterns": ("ナノ講習",), "priority": 69},
    {"id": "sc_ichimon", "label": "SC一門", "patterns": ("SC一門", "sc一門"), "priority": 68},
    {"id": "onigiri_ichimon", "label": "おにぎり一門", "patterns": ("おにぎり一門",), "priority": 67},
    {"id": "men_ichimon", "label": "麺平良一門", "patterns": ("麺平良一門",), "priority": 66},
    {"id": "tokyo_stonan", "label": "ストナン会", "patterns": ("ストナン会",), "priority": 65},
    {"id": "nst", "label": "NST", "patterns": ("NST",), "priority": 64},
    {"id": "okosama_ichimon", "label": "鬼ころし一門", "patterns": ("鬼ころし一門",), "priority": 63},
)
AFFINITY_KEYWORD_CLUSTER_RULE_IDS = frozenset(
    {
        "mbh",
        "atsust",
        "wing_longterm",
        "wing",
        "atsu_chill",
        "tokyo_stonan_kai",
        "tokyo_stonan",
        "miso",
        "yutty",
        "ochimpo",
        "mentaiko",
        "elsta",
    }
)
AFFINITY_KEYWORD_CLUSTER_RULE_ORDER = (
    "mbh",
    "atsu_chill",
    "ochimpo",  # 侍長期/おちんぽ侍はアツスト絵文字より優先
    "atsust",
    "tokyo_stonan_kai",
    "tokyo_stonan",
    "yutty",
    "miso",
    "mentaiko",
    "elsta",
    "wing_longterm",
    "wing",
)
SEMANTIC_FALLBACK_CLUSTER_RULES = (
    {
        "id": "app_online",
        "label": "アプリ/オンライン",
        "tags": ("アプリ/オンライン", "マッチングアプリ", "東カレ"),
        "patterns": ("アプリ", "tinder", "with", "タップル", "東カレ", "ネトナン", "オンライン", "マチアプ", "マッチングアプリ"),
        "priority": 100,
    },
    {
        "id": "street",
        "label": "ストリート/ナンパ",
        "tags": ("ストリート", "ナンパ", "合流", "ソロ"),
        "patterns": ("ストナン", "ストリート", "street", "路上", "街", "合流", "ナンパ", "pua"),
        "priority": 95,
    },
    {
        "id": "club_night",
        "label": "クラブ/夜遊び",
        "tags": ("クラブ/箱", "夜職", "裏垢"),
        "patterns": ("クラブ", "クラナン", "箱", "相席", "バー", "夜職", "ホスト", "裏垢"),
        "priority": 90,
    },
    {
        "id": "appearance",
        "label": "外見/美容",
        "tags": ("美容/整形", "ファッション", "男磨き", "筋トレ"),
        "patterns": ("外見", "美容", "整形", "ファッション", "服", "垢抜け", "筋トレ", "マッチョ"),
        "priority": 85,
    },
    {
        "id": "lessons",
        "label": "講習/コンサル",
        "tags": ("講習", "審査制"),
        "patterns": ("講習", "コンサル", "サロン", "受講", "スクール"),
        "priority": 80,
    },
    {
        "id": "relationship",
        "label": "恋愛/関係構築",
        "tags": ("恋愛", "モテ", "デート", "関係構築"),
        "patterns": ("恋愛", "モテ", "彼女", "デート", "王道彼氏", "関係構築"),
        "priority": 75,
    },
    {
        "id": "business",
        "label": "ビジネス/SNS",
        "tags": ("ビジネス", "SNSマーケ"),
        "patterns": ("事業", "起業", "会社", "代表", "稼ぐ", "sns", "マーケ"),
        "priority": 70,
    },
    {
        "id": "travel_region",
        "label": "地方/遠征",
        "tags": ("旅ナンパ", "地方", "関西", "名古屋", "福岡", "仙台", "札幌"),
        "patterns": ("旅ナンパ", "海外", "地方", "関西", "大阪", "名古屋", "福岡", "仙台", "札幌"),
        "priority": 65,
    },
    {
        "id": "results",
        "label": "実績/攻略",
        "tags": ("即", "即報", "攻略", "経験人数", "月間実績", "美女", "女遊び", "プレイヤー"),
        "patterns": ("即", "攻略", "経験人数", "月間", "美女", "女遊び", "プレイヤー", "斬り"),
        "priority": 60,
    },
)
REGION_CLUSTER_RULES = (
    {
        "id": "tokyo",
        "label": "東京",
        "patterns": ("tokyo", "東京", "東京都", "都内", "渋谷", "新宿", "池袋", "恵比寿", "町田"),
    },
    {"id": "nagoya", "label": "名古屋", "patterns": ("nagoya", "名古屋", "愛知", "栄", "錦")},
    {"id": "osaka", "label": "大阪", "patterns": ("osaka", "大阪", "梅田", "難波", "心斎橋")},
    {"id": "kansai", "label": "関西", "patterns": ("kansai", "関西", "京都", "神戸", "兵庫", "奈良")},
    {"id": "sapporo", "label": "札幌", "patterns": ("sapporo", "札幌", "北海道")},
    {"id": "fukuoka", "label": "福岡", "patterns": ("fukuoka", "福岡", "博多", "天神")},
    {"id": "sendai", "label": "仙台", "patterns": ("sendai", "仙台", "宮城")},
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


def _normalize_non_negative_int(value: Any) -> int:
    if value in (None, ""):
        return 0
    parsed = int(value)
    if parsed < 0:
        raise ValueError(f"Expected non-negative integer: {value!r}")
    return parsed


@dataclass(slots=True)
class Node:
    id: str
    type: str
    name: str
    aliases: list[str] = field(default_factory=list)
    description: str = ""
    icon_url: str = ""
    follower_count: int = 0
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
            follower_count=_normalize_non_negative_int(payload.get("follower_count", 0)),
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
            "follower_count": self.follower_count,
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
                "follower_count",
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
                    "follower_count": node.follower_count,
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


PROFILE_BRIDGE_TAGS_RE = re.compile(r"Shared profile tags:\s*([^.]*)\.")
WEAK_PROFILE_BRIDGE_TAGS = frozenset({"PUA", "ナンパ", "ストリート"})


def _profile_bridge_tags(edge: Edge) -> list[str]:
    match = PROFILE_BRIDGE_TAGS_RE.search(edge.review_notes or "")
    if not match:
        return []
    return [tag.strip() for tag in match.group(1).split(",") if tag.strip()]


def _is_profile_bridge_edge(edge: Edge) -> bool:
    return "Profile bridge auto-edge" in (edge.review_notes or "")


def _is_weak_assistive_edge(edge: Edge) -> bool:
    if not _is_profile_bridge_edge(edge):
        return False
    tags = _profile_bridge_tags(edge)
    return len(tags) <= 1 and (tags[0] if tags else "") in WEAK_PROFILE_BRIDGE_TAGS


def _cluster_edge_weight_scale(edge: Edge) -> float:
    if _is_weak_assistive_edge(edge):
        return 0.15
    return 1.0


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
            base_weight = (
                direct_weights.get(edge.type, 1.0)
                * max(edge.confidence, 0.35)
                * _cluster_edge_weight_scale(edge)
            )
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


def _prune_account_graph_for_clustering(account_graph: Any, mode_key: str) -> Any:
    """Drop weak ties and cap degree so Louvain sees sharper modules."""
    import networkx as nx

    cfg = CLUSTER_PRUNE_CONFIG.get(mode_key)
    if cfg is None:
        return account_graph
    min_weight = float(cfg["min_weight"])
    max_neighbors = int(cfg["max_neighbors"])
    graph = account_graph.copy()
    graph.remove_edges_from(
        [(u, v) for u, v, data in graph.edges(data=True) if float(data.get("weight", 0.0)) < min_weight]
    )
    if max_neighbors <= 0:
        return graph

    def top_neighbor_ids(node_id: str) -> set[str]:
        neighbors = list(graph.neighbors(node_id))
        if len(neighbors) <= max_neighbors:
            return set(neighbors)
        ranked = sorted(
            neighbors,
            key=lambda neighbor_id: float(graph[node_id][neighbor_id].get("weight", 0.0)),
            reverse=True,
        )
        return set(ranked[:max_neighbors])

    removable: list[tuple[str, str]] = []
    for left_id, right_id in graph.edges():
        left_keeps_right = right_id in top_neighbor_ids(left_id)
        right_keeps_left = left_id in top_neighbor_ids(right_id)
        if not left_keeps_right and not right_keeps_left:
            removable.append((left_id, right_id))
    graph.remove_edges_from(removable)
    return graph


def _detect_weighted_communities(account_graph: Any) -> list[set[str]]:
    """Partition account nodes; deterministic seed for stable HTML exports."""
    import networkx as nx
    from networkx.algorithms.community import louvain_communities

    if account_graph.number_of_nodes() == 0:
        return []
    if account_graph.number_of_edges() == 0:
        return [{node_id} for node_id in account_graph.nodes]

    partition = louvain_communities(account_graph, weight="weight", seed=42)
    return [set(community) for community in partition]


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
            *node.source_urls,
        ]
    ).casefold()


def _keyword_labels_for_node(node: Node) -> list[str]:
    text = _keyword_text(node)
    labels: list[str] = []
    for rule in KEYWORD_CLUSTER_RULES:
        if any(str(pattern).casefold() in text for pattern in rule["patterns"]):
            labels.append(str(rule["label"]))
    return labels


def _best_keyword_cluster_rule_for_node(node: Node) -> dict[str, Any] | None:
    text = _keyword_text(node)
    best_rule: dict[str, Any] | None = None
    best_score = 0
    best_priority = -1
    for rule in KEYWORD_CLUSTER_RULES:
        score = sum(1 for pattern in rule["patterns"] if str(pattern).casefold() in text)
        if score <= 0:
            continue
        priority = int(rule["priority"])
        if score > best_score or (score == best_score and priority > best_priority):
            best_rule = rule
            best_score = score
            best_priority = priority
    return best_rule


def _affinity_keyword_cluster_rule_for_node(node: Node) -> dict[str, Any] | None:
    """複数 affinity が当たるときは priority が高いルールを優先する。"""
    text = _keyword_text(node)
    rules_by_id = {str(rule["id"]): rule for rule in KEYWORD_CLUSTER_RULES}
    best: dict[str, Any] | None = None
    best_priority = -1
    for rule_id in AFFINITY_KEYWORD_CLUSTER_RULE_ORDER:
        rule = rules_by_id.get(rule_id)
        if not rule:
            continue
        if not any(str(pattern).casefold() in text for pattern in rule["patterns"]):
            continue
        priority = int(rule.get("priority", 0))
        if priority > best_priority:
            best = rule
            best_priority = priority
    return best


def _build_keyword_cluster_mode_payload(
    graph: GraphData,
    definition: dict[str, Any],
) -> dict[str, Any]:
    nodes_by_id = {node.id: node for node in graph.nodes}
    buckets: dict[str, list[str]] = defaultdict(list)

    for node in graph.nodes:
        if node.type not in CLUSTER_MEMBER_NODE_TYPES:
            continue
        best_rule = _affinity_keyword_cluster_rule_for_node(node) or _best_keyword_cluster_rule_for_node(node)
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


def _build_region_cluster_mode_payload(
    graph: GraphData,
    definition: dict[str, Any],
) -> dict[str, Any]:
    nodes_by_id = {node.id: node for node in graph.nodes}
    location_text_by_account: defaultdict[str, list[str]] = defaultdict(list)
    for edge in graph.edges:
        source = nodes_by_id.get(edge.source)
        target = nodes_by_id.get(edge.target)
        if source is None or target is None:
            continue
        if source.type in ACCOUNT_NODE_TYPES and target.type == "location":
            location_text_by_account[source.id].extend([target.id, target.name, *target.aliases])
        if target.type in ACCOUNT_NODE_TYPES and source.type == "location":
            location_text_by_account[target.id].extend([source.id, source.name, *source.aliases])

    buckets: dict[str, list[str]] = defaultdict(list)
    for node in graph.nodes:
        if node.type not in CLUSTER_MEMBER_NODE_TYPES:
            continue
        text = " ".join(
            [node.id, node.name, node.description, *node.aliases, *location_text_by_account.get(node.id, [])]
        ).casefold()
        for rule in REGION_CLUSTER_RULES:
            if any(str(pattern).casefold() in text for pattern in rule["patterns"]):
                buckets[str(rule["id"])].append(node.id)
                break

    mode_payload = {
        "label": definition["label"],
        "description": definition["description"],
        "assignments": {},
        "clusters": {},
    }
    min_size = int(definition.get("min_size", 3))
    for rule in REGION_CLUSTER_RULES:
        member_ids = sorted(set(buckets.get(str(rule["id"]), [])), key=lambda node_id: nodes_by_id[node_id].name)
        if len(member_ids) < min_size:
            continue
        keyword_counts: Counter[str] = Counter()
        for node_id in member_ids:
            keyword_counts.update(_keyword_labels_for_node(nodes_by_id[node_id]))
        medium_labels = ", ".join(f"{label} {count}" for label, count in keyword_counts.most_common(5)) or "中分類なし"
        preview = [nodes_by_id[node_id].name for node_id in member_ids[:5]]
        preview_suffix = f" ほか {len(member_ids) - len(preview)} 件" if len(member_ids) > len(preview) else ""
        cluster_id = f"region_group:{rule['id']}"
        mode_payload["clusters"][cluster_id] = {
            "label": f"{rule['label']} 大分類",
            "title": f"{rule['label']} 大分類: {', '.join(preview)}{preview_suffix}\n中分類: {medium_labels}",
            "size": len(member_ids),
        }
        for node_id in member_ids:
            mode_payload["assignments"][node_id] = cluster_id
    return mode_payload


def _profile_tags_by_node(graph: GraphData) -> dict[str, Counter[str]]:
    tags_by_node: dict[str, Counter[str]] = defaultdict(Counter)
    for edge in graph.edges:
        tags = _profile_bridge_tags(edge)
        if not tags:
            continue
        tags_by_node[edge.source].update(tags)
        tags_by_node[edge.target].update(tags)
    return tags_by_node


def _semantic_fallback_rule_for_node(node: Node, tag_counts: Counter[str]) -> dict[str, Any] | None:
    text = _keyword_text(node)
    best_rule: dict[str, Any] | None = None
    best_score = 0
    best_priority = -1
    for rule in SEMANTIC_FALLBACK_CLUSTER_RULES:
        score = 0
        for tag in rule["tags"]:
            score += tag_counts.get(str(tag), 0) * 3
        score += sum(2 for pattern in rule["patterns"] if str(pattern).casefold() in text)
        if score <= 0:
            continue
        priority = int(rule["priority"])
        if score > best_score or (score == best_score and priority > best_priority):
            best_rule = rule
            best_score = score
            best_priority = priority
    return best_rule


def _account_neighbor_counts(graph: GraphData, nodes_by_id: dict[str, Node]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for edge in graph.edges:
        source = nodes_by_id.get(edge.source)
        target = nodes_by_id.get(edge.target)
        if source is None or target is None:
            continue
        if source.type in ACCOUNT_NODE_TYPES and target.type in ACCOUNT_NODE_TYPES:
            counts[edge.source] += 1
            counts[edge.target] += 1
    return counts


def _apply_affinity_keyword_clusters(
    graph: GraphData,
    mode_payload: dict[str, Any],
    min_size: int = 2,
) -> None:
    nodes_by_id = {node.id: node for node in graph.nodes}
    buckets: dict[str, list[str]] = defaultdict(list)
    rules_by_id = {str(rule["id"]): rule for rule in KEYWORD_CLUSTER_RULES}

    for node in graph.nodes:
        if node.type not in CLUSTER_MEMBER_NODE_TYPES:
            continue
        rule = _affinity_keyword_cluster_rule_for_node(node)
        if rule is None:
            continue
        rule_id = str(rule["id"])
        buckets[rule_id].append(node.id)

    for rule_id, node_ids in buckets.items():
        member_ids = sorted(set(node_ids), key=lambda node_id: nodes_by_id[node_id].name)
        if len(member_ids) < min_size:
            continue
        rule = rules_by_id[rule_id]
        cluster_id = f"keyword_group:{rule_id}"
        preview = [nodes_by_id[node_id].name for node_id in member_ids[:4]]
        preview_suffix = f" ほか {len(member_ids) - len(preview)} 件" if len(member_ids) > len(preview) else ""
        mode_payload["clusters"][cluster_id] = {
            "label": f"{rule['label']} ({len(member_ids)})",
            "title": f"キーワード {rule['label']}: {', '.join(preview)}{preview_suffix}",
            "size": len(member_ids),
        }
        for node_id in member_ids:
            mode_payload["assignments"][node_id] = cluster_id


def _has_affinity_keyword_cluster(node: Node) -> bool:
    return _affinity_keyword_cluster_rule_for_node(node) is not None


def _backfill_semantic_clusters(
    graph: GraphData,
    mode_payload: dict[str, Any],
    mode_key: str,
    min_size: int,
) -> None:
    nodes_by_id = {node.id: node for node in graph.nodes}
    account_degrees = _account_neighbor_counts(graph, nodes_by_id)
    profile_tags = _profile_tags_by_node(graph)
    assigned_sizes = Counter(mode_payload["assignments"].values())
    buckets: dict[str, list[str]] = defaultdict(list)
    node_rule_ids: dict[str, str] = {}

    for node in graph.nodes:
        if node.type not in CLUSTER_MEMBER_NODE_TYPES:
            continue
        if _has_affinity_keyword_cluster(node):
            continue
        if account_degrees[node.id] <= 0:
            continue
        current_cluster_id = mode_payload["assignments"].get(node.id)
        if current_cluster_id and assigned_sizes[current_cluster_id] >= 5:
            continue
        rule = _semantic_fallback_rule_for_node(node, profile_tags.get(node.id, Counter()))
        if rule is None:
            continue
        rule_id = str(rule["id"])
        buckets[rule_id].append(node.id)
        node_rule_ids[node.id] = rule_id

    usable_rule_ids = {rule_id for rule_id, member_ids in buckets.items() if len(set(member_ids)) >= min_size}
    if not usable_rule_ids:
        return

    for node_id, rule_id in node_rule_ids.items():
        if rule_id not in usable_rule_ids:
            continue
        mode_payload["assignments"][node_id] = f"{mode_key}:semantic:{rule_id}"

    assigned_after = Counter(mode_payload["assignments"].values())
    for cluster_id in list(mode_payload["clusters"]):
        if assigned_after[cluster_id] <= 0:
            del mode_payload["clusters"][cluster_id]

    rules_by_id = {str(rule["id"]): rule for rule in SEMANTIC_FALLBACK_CLUSTER_RULES}
    for rule_id in sorted(usable_rule_ids, key=lambda value: -int(rules_by_id[value]["priority"])):
        cluster_id = f"{mode_key}:semantic:{rule_id}"
        member_ids = sorted(
            {node_id for node_id, assigned_cluster_id in mode_payload["assignments"].items() if assigned_cluster_id == cluster_id},
            key=lambda node_id: nodes_by_id[node_id].name,
        )
        if len(member_ids) < min_size:
            continue
        label = str(rules_by_id[rule_id]["label"])
        preview = [nodes_by_id[node_id].name for node_id in member_ids[:4]]
        preview_suffix = f" ほか {len(member_ids) - len(preview)} 件" if len(member_ids) > len(preview) else ""
        mode_payload["clusters"][cluster_id] = {
            "label": f"{label} 補助 ({len(member_ids)})",
            "title": f"{label} 補助クラスタ: {', '.join(preview)}{preview_suffix}",
            "size": len(member_ids),
        }


def _backfill_neighbor_clusters(
    graph: GraphData,
    mode_payload: dict[str, Any],
    min_cluster_size: int = 5,
) -> None:
    nodes_by_id = {node.id: node for node in graph.nodes}
    assigned_sizes = Counter(mode_payload["assignments"].values())
    candidate_ids = {
        node.id
        for node in graph.nodes
        if node.type in CLUSTER_MEMBER_NODE_TYPES
        and not _has_affinity_keyword_cluster(node)
        and (
            not mode_payload["assignments"].get(node.id)
            or assigned_sizes[mode_payload["assignments"][node.id]] < min_cluster_size
        )
    }
    if not candidate_ids:
        return

    scores_by_node: dict[str, Counter[str]] = defaultdict(Counter)
    for edge in graph.edges:
        source = nodes_by_id.get(edge.source)
        target = nodes_by_id.get(edge.target)
        if source is None or target is None:
            continue
        if source.type not in ACCOUNT_NODE_TYPES or target.type not in ACCOUNT_NODE_TYPES:
            continue
        for node_id, neighbor_id in ((edge.source, edge.target), (edge.target, edge.source)):
            if node_id not in candidate_ids:
                continue
            neighbor_cluster_id = mode_payload["assignments"].get(neighbor_id)
            if not neighbor_cluster_id:
                continue
            if assigned_sizes[neighbor_cluster_id] < min_cluster_size and ":semantic:" not in neighbor_cluster_id:
                continue
            scale = 0.25 if _is_weak_assistive_edge(edge) else 1.0
            scores_by_node[node_id][neighbor_cluster_id] += max(edge.confidence, 0.2) * scale

    for node_id, cluster_scores in scores_by_node.items():
        if not cluster_scores:
            continue
        cluster_id, score = max(
            cluster_scores.items(),
            key=lambda item: (item[1], assigned_sizes[item[0]], item[0]),
        )
        if score < 0.7:
            continue
        mode_payload["assignments"][node_id] = cluster_id

    assigned_after = Counter(mode_payload["assignments"].values())
    for cluster_id, info in mode_payload["clusters"].items():
        info["size"] = assigned_after[cluster_id]
        label = str(info.get("label", ""))
        info["label"] = re.sub(r"\(\d+\)$", f"({assigned_after[cluster_id]})", label)


def build_relation_cluster_payload(graph: GraphData) -> dict[str, Any]:
    nodes_by_id = {node.id: node for node in graph.nodes}
    # 公開地図はキーワード群を初期表示にして、派閥・一門を先に読めるようにする。
    payload = {"default_mode": "keyword_group", "modes": {}}

    for mode_key, definition in CLUSTER_MODE_DEFINITIONS.items():
        if mode_key == "keyword_group":
            payload["modes"][mode_key] = _build_keyword_cluster_mode_payload(graph, definition)
            continue
        if mode_key == "region_group":
            payload["modes"][mode_key] = _build_region_cluster_mode_payload(graph, definition)
            continue
        account_graph, account_contexts = _build_account_projection(graph, mode_key)
        clustered_graph = _prune_account_graph_for_clustering(account_graph, mode_key)
        mode_payload = {
            "label": definition["label"],
            "description": definition["description"],
            "assignments": {},
            "clusters": {},
        }
        if clustered_graph.number_of_nodes():
            communities = _detect_weighted_communities(clustered_graph)

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

        _apply_affinity_keyword_clusters(graph, mode_payload)
        _backfill_semantic_clusters(graph, mode_payload, mode_key, int(definition["min_size"]))
        _backfill_neighbor_clusters(graph, mode_payload)
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
                follower_count INTEGER NOT NULL,
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
                INSERT INTO nodes (id, type, name, description, icon_url, follower_count, confidence, evidence_kind, needs_review, review_notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node.id,
                    node.type,
                    node.name,
                    node.description,
                    node.icon_url,
                    node.follower_count,
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
    thin_candidate_decisions_payload: dict[str, Any] | None = None,
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
        "1000人目標達成後は solid 関係の密度と外周ノイズ除去を優先。初期表示は確定寄りで、"
        "自動補助線と孤立ノードを抑え、公開プロフィール由来の明示関係を見やすくしています。"
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
      color-scheme: light dark;
      --bg: #eaeef6;
      --bg-accent: #dfe6f4;
      --panel: #ffffff;
      --panel-2: #f6f9ff;
      --border: #e1e8f2;
      --border-strong: #cdd7e6;
      --text: #16202b;
      --muted: #64748b;
      --accent: #2f6feb;
      --accent-strong: #1d4ed8;
      --accent-soft: #eaf1ff;
      --shadow: 0 10px 30px rgba(23, 33, 43, 0.08);
      --shadow-sm: 0 2px 8px rgba(23, 33, 43, 0.05);
      --radius: 16px;
      --ease: cubic-bezier(0.22, 1, 0.36, 1);
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #0d131b;
        --bg-accent: #111a24;
        --panel: #161f2b;
        --panel-2: #1b2636;
        --border: #2a3543;
        --border-strong: #38465a;
        --text: #e6edf5;
        --muted: #93a3b7;
        --accent: #5b93ff;
        --accent-strong: #7aa8ff;
        --accent-soft: #1a2740;
        --shadow: 0 14px 40px rgba(0, 0, 0, 0.42);
        --shadow-sm: 0 2px 10px rgba(0, 0, 0, 0.3);
      }
    }
    *,
    *::before,
    *::after {
      box-sizing: border-box;
    }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue",
        "Hiragino Sans", "Hiragino Kaku Gothic ProN", "Noto Sans JP", "Yu Gothic UI",
        Meiryo, sans-serif;
      background:
        radial-gradient(1200px 600px at 15% -10%, var(--bg-accent), transparent 60%),
        var(--bg);
      background-attachment: fixed;
      color: var(--text);
      line-height: 1.6;
      -webkit-font-smoothing: antialiased;
      text-rendering: optimizeLegibility;
    }
    h1, h2, h3, h4 {
      letter-spacing: -0.01em;
    }
    header {
      position: relative;
      overflow: hidden;
      padding: 34px 24px 30px;
      background:
        radial-gradient(900px 300px at 88% -40%, rgba(94, 148, 255, 0.55), transparent 70%),
        radial-gradient(700px 320px at 8% 130%, rgba(126, 87, 194, 0.4), transparent 70%),
        linear-gradient(135deg, #10203a 0%, #1c3a72 55%, #2450b0 100%);
      color: #fff;
      box-shadow: 0 12px 32px rgba(16, 32, 58, 0.28);
    }
    header::after {
      content: "";
      position: absolute;
      inset: 0;
      background-image: radial-gradient(rgba(255, 255, 255, 0.09) 1px, transparent 1.4px);
      background-size: 22px 22px;
      opacity: 0.5;
      pointer-events: none;
    }
    .header-brand {
      position: relative;
      z-index: 1;
    }
    .header-brand {
      display: flex;
      align-items: center;
      gap: 14px;
    }
    .header-icon {
      width: 58px;
      height: 58px;
      border-radius: 18px;
      background: rgba(255, 255, 255, 0.12);
      padding: 8px;
      backdrop-filter: blur(4px);
      border: 1px solid rgba(255, 255, 255, 0.18);
      box-shadow: 0 10px 26px rgba(0, 0, 0, 0.25);
      flex: 0 0 auto;
    }
    .header-copy h1 {
      margin: 0;
      font-size: clamp(26px, 4vw, 38px);
      font-weight: 800;
      letter-spacing: -0.02em;
      line-height: 1.1;
    }
    .avatar-thumb {
      width: 36px;
      height: 36px;
      border-radius: 50%;
      object-fit: cover;
      background: var(--accent-soft);
      border: 1px solid var(--border);
      flex: 0 0 auto;
    }
    .detail-avatar {
      width: 52px;
      height: 52px;
      border-radius: 50%;
      object-fit: cover;
      background: var(--accent-soft);
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
      font-size: 14px;
      max-width: 70ch;
    }
    header code,
    header p code {
      background: rgba(255, 255, 255, 0.14);
      border-radius: 5px;
      padding: 1px 5px;
      font-size: 0.9em;
    }
    main {
      padding: 20px;
      display: grid;
      gap: 20px;
      min-width: 0;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 18px;
      box-shadow: var(--shadow);
      min-width: 0;
    }
    h2 {
      font-size: 19px;
      font-weight: 700;
    }
    h4 {
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.06em;
      font-size: 12px;
    }
    .controls {
      display: grid;
      gap: 16px;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      align-items: start;
    }
    .sticky-controls {
      position: relative;
      z-index: 1;
    }
    .search-control {
      min-width: 0;
    }
    .filter-group {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 8px;
    }
    .relevance-control {
      align-items: center;
    }
    .relevance-control .muted {
      font-size: 12px;
      line-height: 1.4;
    }
    .chip {
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 6px 12px;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: var(--panel);
      color: var(--muted);
      font-size: 13px;
      cursor: pointer;
      transition: border-color 0.16s var(--ease), background 0.16s var(--ease), color 0.16s var(--ease);
    }
    .chip:hover {
      border-color: var(--accent);
      color: var(--text);
    }
    .chip:has(input:checked) {
      background: var(--accent-soft);
      border-color: var(--accent);
      color: var(--accent-strong);
      font-weight: 600;
    }
    .chip input {
      accent-color: var(--accent);
    }
    .action-row,
    .search-results,
    .view-summary {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .action-row {
      margin-top: 10px;
    }
    .starter-actions {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      margin-top: 10px;
    }
    .search-results {
      margin-top: 10px;
    }
    .search-result-button,
    .action-button {
      border: 1px solid var(--border);
      border-radius: 10px;
      background: var(--panel);
      color: var(--text);
      padding: 8px 12px;
      font-size: 13px;
      font-weight: 500;
      cursor: pointer;
      transition: border-color 0.16s var(--ease), background 0.16s var(--ease),
        color 0.16s var(--ease), transform 0.12s var(--ease), box-shadow 0.16s var(--ease);
    }
    .search-result-button {
      max-width: 100%;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .search-result-button:hover,
    .action-button:hover {
      border-color: var(--accent);
      background: var(--accent-soft);
      color: var(--accent-strong);
      transform: translateY(-1px);
      box-shadow: var(--shadow-sm);
    }
    .action-button:active {
      transform: translateY(0);
    }
    .action-button.is-active {
      border-color: var(--accent);
      background: var(--accent);
      color: #fff;
    }
    .view-summary {
      align-items: center;
    }
    .summary-note {
      flex-basis: 100%;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }
    .network-head {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
    }
    .network-head h2 {
      margin-bottom: 0;
    }
    .stats {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 12px;
    }
    .stat {
      background: var(--panel-2);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 14px;
    }
    .stat strong {
      display: block;
      font-size: 26px;
      font-weight: 800;
      letter-spacing: -0.02em;
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
      min-width: 0;
    }
    .foldout summary::-webkit-details-marker {
      display: none;
    }
    .foldout-summary-text {
      display: flex;
      flex-direction: column;
      gap: 4px;
      min-width: 0;
    }
    .foldout-content {
      margin-top: 16px;
      min-width: 0;
    }
    .foldout .panel {
      background: var(--panel-2);
    }
    .foldout summary {
      padding: 4px 2px;
      border-radius: 8px;
      transition: color 0.16s var(--ease);
    }
    .foldout summary:hover {
      color: var(--accent);
    }
    #network {
      height: 650px;
      border: 1px solid var(--border);
      border-radius: 12px;
      background:
        radial-gradient(circle at 30% 20%, rgba(47, 111, 235, 0.05), transparent 45%),
        var(--panel);
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.04);
    }
    input[type="search"],
    .control-select {
      width: 100%;
      box-sizing: border-box;
      border: 1px solid var(--border-strong);
      border-radius: 10px;
      padding: 10px 12px;
      font-size: 14px;
      background: var(--panel);
      color: var(--text);
      transition: border-color 0.16s var(--ease), box-shadow 0.16s var(--ease);
    }
    input[type="search"]:focus,
    .control-select:focus {
      outline: none;
      border-color: var(--accent);
      box-shadow: 0 0 0 3px var(--accent-soft);
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
      overflow-wrap: anywhere;
    }
    th {
      color: var(--muted);
      background: var(--panel-2);
      position: sticky;
      top: 0;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    .table-wrap {
      width: 100%;
      max-width: 100%;
      max-height: 360px;
      overflow-x: auto;
      overflow-y: auto;
      border: 1px solid var(--border);
      border-radius: 8px;
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
      padding: 3px 10px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 700;
      background: #eef4ff;
      color: #224a8f;
      margin-right: 6px;
      margin-bottom: 6px;
    }
    .view-summary .tag {
      background: var(--accent-soft);
      color: var(--accent-strong);
      border: 1px solid color-mix(in srgb, var(--accent) 22%, transparent);
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
    .two-column > * {
      min-width: 0;
    }
    .graph-layout {
      display: grid;
      gap: 20px;
      grid-template-columns: minmax(0, 2.8fr) minmax(300px, 0.9fr);
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
    .featured-node-list {
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      gap: 8px;
      margin-top: 12px;
      min-width: 0;
    }
    .featured-node-card {
      width: 100%;
      max-width: 100%;
      min-width: 0;
      border: 1px solid var(--border);
      border-radius: 12px;
      background: var(--panel);
      padding: 9px 11px;
      display: flex;
      align-items: center;
      gap: 11px;
      text-align: left;
      cursor: pointer;
      transition: border-color 0.16s var(--ease), background 0.16s var(--ease),
        transform 0.12s var(--ease), box-shadow 0.16s var(--ease);
    }
    .featured-node-card:hover {
      border-color: var(--accent);
      background: var(--accent-soft);
      transform: translateY(-1px);
      box-shadow: var(--shadow-sm);
    }
    .featured-node-card strong {
      font-weight: 700;
    }
    .featured-node-card .node-name-text {
      flex: 1 1 auto;
      min-width: 0;
    }
    .featured-node-card strong,
    .featured-node-card span {
      display: block;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
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
      border-radius: 10px;
      padding: 10px;
      background: var(--panel-2);
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
    .connected-node-card.is-bridge {
      background: var(--panel-2);
      border-style: dashed;
    }
    .connected-node-rank {
      color: var(--muted);
      font-size: 12px;
      margin-top: 2px;
    }
    .tag-bridge {
      background: #edf5ff;
      color: #1d4ed8;
    }
    .tag-weak-bridge {
      background: #f5f7fb;
      color: #64748b;
    }
    .tag-strong-bridge {
      background: #eef2ff;
      color: #4338ca;
    }
    .tag-solid {
      background: #edf7ee;
      color: #1f7a3d;
    }
    .detail-list,
    .source-list {
      margin: 0;
      padding-left: 18px;
      max-width: 100%;
    }
    .detail-list li,
    .source-list li {
      margin-bottom: 8px;
      line-height: 1.5;
      min-width: 0;
    }
    .source-list a {
      overflow-wrap: anywhere;
      word-break: break-word;
    }
    .inspect-button {
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--panel);
      color: var(--accent);
      padding: 6px 10px;
      font-size: 12px;
      cursor: pointer;
      transition: border-color 0.16s var(--ease), background 0.16s var(--ease);
    }
    .inspect-button:hover {
      border-color: var(--accent);
      background: var(--accent-soft);
    }
    .command-actions {
      display: grid;
      grid-template-columns: repeat(3, minmax(72px, 1fr));
      gap: 6px;
    }
    .command-hint {
      display: block;
      margin-top: 6px;
      font-size: 12px;
      line-height: 1.4;
    }
    a {
      color: var(--accent);
      text-decoration-color: color-mix(in srgb, var(--accent) 40%, transparent);
      text-underline-offset: 2px;
    }
    a:hover {
      text-decoration-color: var(--accent);
    }
    code {
      font-family: "SFMono-Regular", "Cascadia Code", Consolas, "Liberation Mono", monospace;
      font-size: 0.88em;
      background: var(--panel-2);
      border: 1px solid var(--border);
      border-radius: 5px;
      padding: 1px 5px;
    }
    ::selection {
      background: color-mix(in srgb, var(--accent) 28%, transparent);
    }
    * {
      scrollbar-width: thin;
      scrollbar-color: var(--border-strong) transparent;
    }
    ::-webkit-scrollbar {
      width: 10px;
      height: 10px;
    }
    ::-webkit-scrollbar-thumb {
      background: var(--border-strong);
      border-radius: 999px;
      border: 2px solid transparent;
      background-clip: padding-box;
    }
    ::-webkit-scrollbar-thumb:hover {
      background: var(--muted);
      background-clip: padding-box;
    }
    @media (max-width: 1024px) {
      .graph-layout {
        grid-template-columns: 1fr;
      }
      .detail-panel {
        position: static;
      }
    }
    @media (max-width: 640px) {
      header {
        padding: 22px 20px;
      }
      main {
        padding: 12px;
        gap: 14px;
      }
      .panel {
        border-radius: 12px;
        padding: 14px;
      }
      .controls {
        grid-template-columns: minmax(0, 1fr);
      }
      .two-column {
        grid-template-columns: minmax(0, 1fr);
      }
      .action-row {
        width: 100%;
      }
      .action-button {
        flex: 1 1 calc(50% - 8px);
        min-width: 0;
        white-space: normal;
      }
      .starter-actions {
        grid-template-columns: 1fr;
      }
      #network {
        height: 520px;
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
    <section class="panel controls sticky-controls">
      <div class="search-control">
        <label for="search"><strong>名前検索</strong></label>
        <input id="search" type="search" list="search-suggestions" placeholder="名前 / id / 別名 / 説明">
        <datalist id="search-suggestions"></datalist>
        <div id="search-results" class="search-results"></div>
      </div>
      <div>
        <strong>表示方針</strong>
        <div class="action-row">
          <button type="button" id="reset-view" class="action-button">全体に戻す</button>
          <button type="button" id="fit-graph" class="action-button">中央へ</button>
          <button type="button" id="quick-connectivity" class="action-button">近い関係</button>
          <button type="button" id="quick-keyword" class="action-button">キーワード</button>
        </div>
        <div class="action-row">
          <button type="button" class="action-button bridge-preset-button" data-bridge-preset="solid">確定寄り</button>
          <button type="button" class="action-button bridge-preset-button" data-bridge-preset="online">アプリ/オンライン</button>
          <button type="button" class="action-button bridge-preset-button" data-bridge-preset="street">ストリート</button>
          <button type="button" class="action-button bridge-preset-button" data-bridge-preset="club">クラブ/箱</button>
          <button type="button" class="action-button bridge-preset-button" data-bridge-preset="field">実戦寄り</button>
          <button type="button" class="action-button bridge-preset-button" data-bridge-preset="community">界隈キーワード</button>
          <button type="button" class="action-button bridge-preset-button" data-bridge-preset="all">全補助</button>
        </div>
        <p class="muted">初期表示は確定寄り（自動補助線オフ）。関係線につながるアカウントだけを描画し、外周の孤立ノイズを抑えます。</p>
        <div class="filter-group relevance-control">
          <label class="chip">
            <input type="checkbox" id="relevance-filter-toggle" checked>
            <span>関連人物だけ表示</span>
          </label>
          <span class="muted">外すと収集中の薄い候補も含めます。</span>
        </div>
      </div>
      <div>
        <label for="cluster-mode"><strong>配置グループ</strong></label>
        <select id="cluster-mode" class="control-select">
          <option value="off">まとめない</option>
          <option value="connectivity">つながりの近さ</option>
          <option value="relation_pattern">関係パターン</option>
          <option value="keyword_group">キーワード</option>
          <option value="region_group">地域</option>
        </select>
        <div id="cluster-mode-help" class="muted">通常表示です。人やコミュニティをまとめずに相関を見ます。</div>
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
          <div style="margin-top: 16px;">
            <strong>補助線</strong>
            <div class="filter-group">
              <label class="chip">
                <input type="checkbox" id="profile-bridge-toggle">
                <span>自動補助線</span>
              </label>
            </div>
            <div id="bridge-category-filters" class="filter-group"></div>
            <p class="muted">初期はオフ。確定寄り関係の密度を見るときはオフのまま、探索時だけオンにします。</p>
          </div>
        </div>
      </details>
    </section>

    <span id="visible-nodes" hidden>0</span>
    <span id="visible-edges" hidden>0</span>
    <span id="review-nodes" hidden>0</span>
    <span id="review-edges" hidden>0</span>
    <span id="review-candidates" hidden>0</span>
    <span id="total-nodes" hidden>0</span>
    <span id="total-edges" hidden>0</span>

    <section class="graph-layout">
      <section class="panel network-panel">
        <div class="network-head">
          <h2>アカウント相関ビュー</h2>
          <div id="view-summary" class="view-summary"></div>
        </div>
        <p class="muted">初期表示は関連人物を優先します。名前と線は抑え、検索やクリックで近い関係を詳しく見られます。</p>
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
          <h2>薄い候補レビュー</h2>
          <p class="muted">初期表示から外した候補です。高フォロワーなのに関係線や関連語が薄いものを先に確認します。</p>
          <div id="thin-review-summary" class="view-summary"></div>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>詳細</th>
                  <th>優先度</th>
                  <th>名前</th>
                  <th>理由</th>
                  <th>フォロワー</th>
                  <th>関係</th>
                  <th>出典</th>
                  <th>判断</th>
                </tr>
              </thead>
              <tbody id="thin-candidates-table"></tbody>
            </table>
          </div>
          <div class="table-footer">
            <span id="thin-candidates-table-status" class="table-status muted"></span>
            <button type="button" id="thin-candidates-table-more" class="inspect-button" hidden>さらに表示</button>
          </div>
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

        <section class="panel">
          <h2>薄い候補判断ログ</h2>
          <p class="muted">keep / exclude / review の判断は <code>data/thin_candidate_decisions.json</code> に保持されます。</p>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>詳細</th>
                  <th>状態</th>
                  <th>名前</th>
                  <th>スコア</th>
                  <th>理由</th>
                  <th>メモ</th>
                </tr>
              </thead>
              <tbody id="thin-candidate-decisions-table"></tbody>
            </table>
          </div>
          <div class="table-footer">
            <span id="thin-candidate-decisions-table-status" class="table-status muted"></span>
            <button type="button" id="thin-candidate-decisions-table-more" class="inspect-button" hidden>さらに表示</button>
          </div>
        </section>
      </div>
    </details>

    <details class="panel foldout">
      <summary>
        <span class="foldout-summary-text">
          <span>ノード / エッジ一覧</span>
          <span class="muted">表で確認したいときだけ開けます。</span>
        </span>
      </summary>
      <div class="foldout-content two-column">
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
      </div>
    </details>
  </main>

  <script src="https://unpkg.com/vis-network@9.1.9/dist/vis-network.min.js"></script>
  <script>
    function siteAssetUrl(filename) {
      const origin = window.location.origin;
      let dir = window.location.pathname;
      if (!dir.endsWith("/")) {
        const baseName = dir.split("/").pop() || "";
        dir = baseName.includes(".") ? dir.slice(0, dir.lastIndexOf("/") + 1) : `${dir}/`;
      }
      return new URL(filename, `${origin}${dir}`).href;
    }

    async function loadSiteData(path) {
      const response = await fetch(path, { cache: "no-store" });
      if (!response.ok) {
        throw new Error(`Failed to load graph data: ${response.status}`);
      }
      return response.json();
    }

    (async () => {
    const rawSiteData = await loadSiteData(siteAssetUrl("__SITE_DATA_PATH__"));
    const rawGraph = rawSiteData.graph || { nodes: [], edges: [] };
    const rawReviewCandidates = rawSiteData.review_candidates || { generated_at: "", candidates: [] };
    const rawReviewCandidateDecisions = rawSiteData.review_candidate_decisions || { updated_at: "", decisions: {} };
    const rawThinCandidateDecisions = rawSiteData.thin_candidate_decisions || { updated_at: "", decisions: {} };
    const rawClusters = rawSiteData.clusters || { default_mode: "off", modes: {} };
    const nodeColors = {
      person: "#2f6feb",
      community: "#7e57c2",
      platform: "#2e8b57",
      location: "#f39c12",
      content: "#d14d72"
    };
    const clusterColors = [
      "#e74c3c", "#e67e22", "#f1c40f", "#2ecc71", "#1abc9c",
      "#3498db", "#9b59b6", "#e91e63", "#00bcd4", "#ff5722",
      "#795548", "#607d8b", "#8bc34a", "#ff9800", "#03a9f4",
      "#673ab7", "#cddc39", "#ffc107", "#009688", "#ff6f00",
      "#d32f2f", "#c2185b", "#7b1fa2", "#512da8", "#303f9f",
      "#1976d2", "#0288d1", "#0097a7", "#00796b", "#388e3c"
    ];
    const nodeTypeLabels = {
      person: "人物",
      community: "コミュニティ",
      platform: "媒体",
      location: "場所",
      content: "コンテンツ"
    };
    const edgeTypeLabels = {
      influence: "師弟・講習",
      affiliation: "所属・関係",
      collaboration: "交流・コラボ",
      criticism: "批判・対立",
      monetization: "収益・商品",
      activity: "活動場所",
      follow: "フォロー",
      profile_mention: "プロフィール言及"
    };
    const bridgeCategoryDefinitions = [
      { id: "miso", label: "味噌", patterns: ["味噌", "みそ"] },
      { id: "mbh", label: "MBH", patterns: ["mbh", "MBH"] },
      { id: "online", label: "アプリ/オンライン", patterns: ["アプリ/オンライン", "アプリ", "オンライン", "ネトナン", "Tinder", "tinder", "東カレ", "チャットアプリ"] },
      { id: "street", label: "ストリート", patterns: ["ストリート", "ストナン", "スト値", "スト高", "路上", "街"] },
      { id: "club", label: "クラブ/箱", patterns: ["クラブ/箱", "クラブ", "箱", "相席", "バー", "ハプバー", "夜遊び"] },
      { id: "lesson", label: "講習", patterns: ["講習", "コンサル"] },
      { id: "community", label: "界隈/一門", patterns: ["界隈", "一門", "軍団", "コミュニティ", "長期"] },
      { id: "close", label: "即", patterns: ["即", "経験人数", "月間実績"] },
      { id: "business", label: "事業", patterns: ["事業", "SNS", "マーケティング", "代表", "稼ぐ"] },
      { id: "nightlife", label: "夜職", patterns: ["夜職", "ホスト", "港区"] },
      { id: "other", label: "その他", patterns: [] }
    ];
    const basisLabels = {
      profile_text: "プロフィール",
      summary: "概要",
      pinned_post_text: "固定ポスト"
    };
    const decisionStatusLabels = {
      approved: "承認",
      dismissed: "却下"
    };
    const thinDecisionStatusLabels = {
      keep: "残す",
      exclude: "除外",
      review: "要確認"
    };
    const accountNodeTypes = new Set(["person", "community"]);
    const solidContextNodeTypes = new Set(["person", "community", "location"]);
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
      },
      region_group: rawClusters.modes?.region_group || {
        label: "地域で大分類",
        description: "東京・名古屋・大阪などの大きな地域でまとめ、講習や一門を中分類として見ます。"
      }
    };

    const allNodeTypes = [...new Set(rawGraph.nodes.map((node) => node.type))];
    const allEdgeTypes = [...new Set(rawGraph.edges.map((edge) => edge.type))];
    const rawNodeById = new Map(rawGraph.nodes.map((node) => [node.id, node]));
    const nodeDegreeById = new Map();
    const followDegreeById = new Map();
    const solidDegreeById = new Map();
    const assistiveDegreeById = new Map();
    rawGraph.edges.forEach((edge) => {
      const sourceNode = rawNodeById.get(edge.source);
      const targetNode = rawNodeById.get(edge.target);
      if (!sourceNode || !targetNode) {
        return;
      }
      const personIds = [];
      if (sourceNode.type === "person" && solidContextNodeTypes.has(targetNode.type)) {
        personIds.push(edge.source);
      }
      if (targetNode.type === "person" && solidContextNodeTypes.has(sourceNode.type)) {
        personIds.push(edge.target);
      }
      if (!personIds.length) {
        return;
      }
      const countedIds =
        sourceNode.type === "person" && targetNode.type === "person"
          ? [edge.source, edge.target]
          : personIds;
      const assistive = isAssistiveEdge(edge);
      countedIds.forEach((nodeId) => {
        nodeDegreeById.set(nodeId, (nodeDegreeById.get(nodeId) || 0) + 1);
        if (assistive) {
          assistiveDegreeById.set(nodeId, (assistiveDegreeById.get(nodeId) || 0) + 1);
        } else {
          solidDegreeById.set(nodeId, (solidDegreeById.get(nodeId) || 0) + 1);
        }
      });
      if (edge.type === "follow" && sourceNode.type === "person" && targetNode.type === "person") {
        followDegreeById.set(edge.source, (followDegreeById.get(edge.source) || 0) + 1);
        followDegreeById.set(edge.target, (followDegreeById.get(edge.target) || 0) + 1);
      }
    });
    function hasRealProfileIcon(node) {
      return Boolean(node?.icon_url) && !node.icon_url.includes("/default_profile_");
    }
    function nodeSearchText(node) {
      return [node.id, node.name, node.description, ...(node.aliases || [])].join(" ").toLocaleLowerCase("ja-JP");
    }
    function thinCandidateDecision(nodeId) {
      const decision = rawThinCandidateDecisions.decisions?.[nodeId];
      return decision && typeof decision === "object" ? decision : {};
    }
    function thinCandidateDecisionStatus(nodeId) {
      return String(thinCandidateDecision(nodeId).status || "").trim();
    }
    const networkRelevanceKeywords = [
      "ナンパ", "pua", "即", "ストナン", "ネトナン", "クラナン", "ストリート", "路上",
      "nanpa", "nannpa", "nampa", "stonan", "rojou", "suto_nan", "suto-nan", "netonan", "kuranan", "street", "tinder", "tapple",
      "pairs", "omiai", "タップル", "ペアーズ", "東カレ", "mote",
      "マッチングアプリ", "講習", "コンサル", "モテ", "攻略", "美女攻略", "恋愛", "界隈", "一門",
      "味噌", "mbh", "こりら", "アツスト", "女遊び", "経験人数", "箱", "クラブ"
    ];
    function hasNetworkRelevanceKeyword(node) {
      const text = nodeSearchText(node);
      return networkRelevanceKeywords.some((keyword) => text.includes(keyword.toLocaleLowerCase("ja-JP")));
    }
    const baseNetworkRelevantPersonIds = new Set(rawGraph.nodes
      .filter((node) =>
        node.type === "person" &&
        thinCandidateDecisionStatus(node.id) !== "exclude" &&
        (
          thinCandidateDecisionStatus(node.id) === "keep" ||
          hasNetworkRelevanceKeyword(node) ||
          (followDegreeById.get(node.id) || 0) >= 2
        )
      )
      .map((node) => node.id));
    const networkRelevantPersonIds = new Set(baseNetworkRelevantPersonIds);
    rawGraph.edges.forEach((edge) => {
      if (isAssistiveEdge(edge)) {
        return;
      }
      const sourceNode = rawNodeById.get(edge.source);
      const targetNode = rawNodeById.get(edge.target);
      if (!sourceNode || !targetNode || sourceNode.type !== "person" || targetNode.type !== "person") {
        return;
      }
      if (baseNetworkRelevantPersonIds.has(edge.source) && thinCandidateDecisionStatus(edge.target) !== "exclude") {
        networkRelevantPersonIds.add(edge.target);
      }
      if (baseNetworkRelevantPersonIds.has(edge.target) && thinCandidateDecisionStatus(edge.source) !== "exclude") {
        networkRelevantPersonIds.add(edge.source);
      }
    });
    function isNetworkRelevantPerson(node) {
      if (!node || node.type !== "person") {
        return false;
      }
      if (thinCandidateDecisionStatus(node.id) === "exclude") {
        return false;
      }
      return networkRelevantPersonIds.has(node.id);
    }
    const rankedAccountNodeIds = rawGraph.nodes
      .filter((node) => accountNodeTypes.has(node.type))
      .sort((left, right) =>
        (Number(isNetworkRelevantPerson(right)) - Number(isNetworkRelevantPerson(left))) ||
        (isNetworkRelevantPerson(right) ? (right.follower_count || 0) : 0) - (isNetworkRelevantPerson(left) ? (left.follower_count || 0) : 0) ||
        (nodeDegreeById.get(right.id) || 0) - (nodeDegreeById.get(left.id) || 0) ||
        left.name.localeCompare(right.name, "ja")
      )
      .map((node) => node.id);
    const rankedFollowNodeIds = rawGraph.nodes
      .filter((node) => accountNodeTypes.has(node.type) && hasRealProfileIcon(node) && isNetworkRelevantPerson(node))
      .sort((left, right) =>
        (right.follower_count || 0) - (left.follower_count || 0) ||
        (followDegreeById.get(right.id) || 0) - (followDegreeById.get(left.id) || 0) ||
        (nodeDegreeById.get(right.id) || 0) - (nodeDegreeById.get(left.id) || 0) ||
        left.name.localeCompare(right.name, "ja")
      )
      .map((node) => node.id);
    const followerRankById = new Map(rankedFollowNodeIds.map((nodeId, index) => [nodeId, index + 1]));
    const defaultLabelNodeIds = new Set([...rankedAccountNodeIds.slice(0, 18), ...rankedFollowNodeIds.slice(0, 16)]);
    let currentVisibleNodes = [];
    let currentVisibleEdges = [];
    let currentVisibleReviewNodes = [];
    let currentVisibleReviewEdges = [];
    let currentThinCandidateEntries = [];
    let currentThinDecisionEntries = [];
    let currentVisibleReviewCandidates = [];
    let currentVisibleReviewCandidateDecisions = [];
    let currentNodeNameById = new Map();
    let currentSearchTerm = "";
    let selectedNodeId = null;
    let activeClusterIds = new Set();
    let lastTableFilterKey = "";
    const tablePageSizes = {
      reviewNodes: 60,
      reviewEdges: 120,
      thinCandidates: 80,
      reviewCandidates: 80,
      reviewCandidateDecisions: 80,
      thinCandidateDecisions: 80,
      nodes: 120,
      edges: 200
    };
    const tableRenderState = { ...tablePageSizes };

    document.getElementById("total-nodes").textContent = rawGraph.nodes.length;
    document.getElementById("total-edges").textContent = rawGraph.edges.length;
    document.getElementById("search-suggestions").innerHTML = rawGraph.nodes
      .filter((node) => accountNodeTypes.has(node.type))
      .sort((left, right) =>
        (nodeDegreeById.get(right.id) || 0) - (nodeDegreeById.get(left.id) || 0) ||
        left.name.localeCompare(right.name, "ja")
      )
      .slice(0, 300)
      .map((node) => `<option value="${escapeHtml(node.name)}"></option>`)
      .join("");

    function escapeHtml(value) {
      return String(value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
    }

    function shellDoubleQuote(value) {
      const safeValue = String(value).replace(/[^A-Za-z0-9_.:-]/g, "-");
      return `"${safeValue}"`;
    }

    function thinDecisionCommand(nodeId, status) {
      return [
        "python",
        "scraper.py",
        "--mark-thin-candidate",
        shellDoubleQuote(nodeId),
        "--thin-status",
        status,
        "--thin-note",
        '""'
      ].join(" ");
    }

    function thinBulkDecisionCommand(entries, status) {
      const nodeIds = entries.map((entry) => shellDoubleQuote(entry.node.id));
      return [
        "python",
        "scraper.py",
        "--mark-thin-candidates",
        ...nodeIds,
        "--thin-status",
        status,
        "--thin-note",
        '""'
      ].join(" ");
    }

    async function copyTextToClipboard(text) {
      if (navigator.clipboard && window.isSecureContext) {
        try {
          await navigator.clipboard.writeText(text);
          return;
        } catch (error) {
          // Fall through to the textarea fallback for browsers that gate clipboard access.
        }
      }
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.setAttribute("readonly", "");
      textarea.style.position = "fixed";
      textarea.style.left = "-9999px";
      document.body.appendChild(textarea);
      textarea.select();
      const copied = document.execCommand("copy");
      textarea.remove();
      if (!copied) {
        throw new Error("copy command failed");
      }
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
    const bridgeCategoryContainer = document.getElementById("bridge-category-filters");
    if (bridgeCategoryContainer) {
      bridgeCategoryContainer.innerHTML = bridgeCategoryDefinitions
        .map((category) => `
          <label class="chip">
            <input type="checkbox" data-bridge-category="${category.id}" checked>
            <span>${escapeHtml(category.label)}</span>
          </label>
        `)
        .join("");
    }
    const clusterModeInput = document.getElementById("cluster-mode");
    const keywordClusterPicker = document.getElementById("keyword-cluster-picker");
    const keywordClusterInput = document.getElementById("keyword-cluster-select");
    if (clusterModeInput) {
      // solid-first + cluster-first: キーワード群があれば初期で派閥を見せる。
      const preferredCluster =
        rawClusters.default_mode ||
        (rawClusters.modes?.keyword_group ? "keyword_group" : "") ||
        (rawClusters.modes?.connectivity ? "connectivity" : "off");
      clusterModeInput.value = preferredCluster;
    }

    const nodesDataSet = new vis.DataSet([]);
    const edgesDataSet = new vis.DataSet([]);
    const prefersDarkGraph = !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
    const graphLabelColor = prefersDarkGraph ? "#e6edf5" : "#17212b";
    const graphLabelStroke = prefersDarkGraph ? "rgba(10, 15, 22, 0.85)" : "rgba(255, 255, 255, 0.92)";
    const graphEdgeColor = prefersDarkGraph ? "rgba(125, 145, 172, 0.4)" : "rgba(148, 163, 184, 0.42)";
    const graphEdgeHighlight = prefersDarkGraph ? "#7aa8ff" : "#2f6feb";
    const network = new vis.Network(
      document.getElementById("network"),
      { nodes: nodesDataSet, edges: edgesDataSet },
      {
        layout: {
          improvedLayout: false,
          randomSeed: 13
        },
        physics: {
          enabled: false,
          stabilization: {
            enabled: false,
            iterations: 0,
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
          scaling: { min: 2, max: 32 },
          font: {
            face: "Hiragino Sans, Noto Sans JP, Segoe UI, sans-serif",
            size: 13,
            color: graphLabelColor,
            strokeWidth: 3,
            strokeColor: graphLabelStroke
          }
        },
        edges: {
          arrows: "to",
          color: { color: graphEdgeColor, highlight: graphEdgeHighlight },
          font: { align: "top", size: 11, color: graphLabelColor, strokeWidth: 3, strokeColor: graphLabelStroke },
          smooth: false
        },
        interaction: {
          hover: true,
          navigationButtons: false,
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

    const affinityKeywordClusterIds = new Set([
      "keyword_group:mbh",
      "keyword_group:atsust",
      "keyword_group:wing_longterm",
      "keyword_group:wing",
      "keyword_group:atsu_chill"
    ]);

    function clusterBucketIdForNode(node, clusterMode, modePayload) {
      const keywordClusterId = rawClusters.modes?.keyword_group?.assignments?.[node.id];
      if (clusterMode !== "keyword_group" && affinityKeywordClusterIds.has(keywordClusterId)) {
        return keywordClusterId;
      }
      return modePayload?.assignments?.[node.id] || "";
    }

    function clusterInfoFor(clusterId, modePayload) {
      return modePayload?.clusters?.[clusterId] || rawClusters.modes?.keyword_group?.clusters?.[clusterId] || {};
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

    function formatNumber(value) {
      return Number(value || 0).toLocaleString("ja-JP");
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

    function formatThinDecisionStatus(value) {
      return thinDecisionStatusLabels[value] || value || "-";
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

    function renderSearchResults(term, visibleNodes) {
      const container = document.getElementById("search-results");
      if (!container) {
        return;
      }
      if (!term) {
        container.innerHTML = "";
        return;
      }
      const matches = visibleNodes
        .filter((node) => matchesSearch(node, term))
        .sort((left, right) =>
          (nodeDegreeById.get(right.id) || 0) - (nodeDegreeById.get(left.id) || 0) ||
          left.name.localeCompare(right.name, "ja")
        )
        .slice(0, 8);
      container.innerHTML = matches.length
        ? matches.map((node) => `
            <button type="button" class="search-result-button" data-node-id="${escapeHtml(node.id)}">
              ${escapeHtml(node.name)}
            </button>
          `).join("")
        : '<span class="muted">一致なし</span>';
    }

    function renderViewSummary(visibleNodes, visibleEdges) {
      const container = document.getElementById("view-summary");
      if (!container) {
        return;
      }
      const people = visibleNodes.filter((node) => node.type === "person").length;
      const communities = visibleNodes.filter((node) => node.type === "community").length;
      const allPeople = rawGraph.nodes.filter((node) => node.type === "person").length;
      const relevantPeople = rawGraph.nodes.filter((node) => node.type === "person" && isNetworkRelevantPerson(node)).length;
      const onlyRelevantAccounts = document.getElementById("relevance-filter-toggle")?.checked !== false;
      const profileBridgeEnabled = document.getElementById("profile-bridge-toggle")?.checked === true;
      const profileBridgeEdges = visibleEdges.filter((edge) => isAssistiveEdge(edge)).length;
      const solidEdges = visibleEdges.filter((edge) => !isAssistiveEdge(edge)).length;
      container.innerHTML = `
        <span class="tag">${escapeHtml(people)} 人物</span>
        <span class="tag">${escapeHtml(communities)} コミュニティ</span>
        <span class="tag">${escapeHtml(visibleEdges.length)} 関係</span>
        <span class="tag">確定 ${escapeHtml(solidEdges)} / 補助 ${escapeHtml(profileBridgeEdges)}</span>
        <span class="tag">${profileBridgeEnabled ? "補助線 ON" : "確定寄り"}</span>
        ${onlyRelevantAccounts
          ? `<span class="tag">薄い候補 ${escapeHtml(allPeople - relevantPeople)} 件非表示</span>`
          : `<span class="tag">全人物 ${escapeHtml(allPeople)} 人</span>`}
      `;
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
      return `<img class="${escapeHtml(className)}" src="${escapeHtml(node.icon_url)}" alt="${escapeHtml(node.name)} icon" loading="lazy" onerror="this.hidden=true">`;
    }

    function isProfileBridgeEdge(edge) {
      return String(edge.review_notes || "").includes("Profile bridge auto-edge");
    }

    function isKeywordBridgeEdge(edge) {
      return String(edge.review_notes || "").includes("Keyword cluster");
    }

    function isAssistiveEdge(edge) {
      const notes = String(edge.review_notes || "");
      return (
        isProfileBridgeEdge(edge) ||
        isKeywordBridgeEdge(edge) ||
        notes.includes("Shared context") ||
        notes.includes("Shared-neighbor")
      );
    }

    function profileBridgeTags(edge) {
      const notes = String(edge.review_notes || "");
      const match = notes.match(/Shared profile tags:\\s*([^.]*)\\./);
      if (!match) {
        return [];
      }
      return match[1]
        .split(",")
        .map((tag) => tag.trim())
        .filter((tag) => tag);
    }

    function isWeakAssistiveEdge(edge) {
      if (!isAssistiveEdge(edge)) {
        return false;
      }
      if (!isProfileBridgeEdge(edge)) {
        return false;
      }
      const tags = profileBridgeTags(edge);
      return tags.length <= 1 && ["PUA", "ナンパ", "ストリート"].includes(tags[0] || "");
    }

    function isStrongAssistiveEdge(edge) {
      if (!isAssistiveEdge(edge) || isWeakAssistiveEdge(edge)) {
        return false;
      }
      if (!isProfileBridgeEdge(edge)) {
        return true;
      }
      return profileBridgeTags(edge).length >= 2;
    }

    function assistiveEdgeKind(edge) {
      if (!isAssistiveEdge(edge)) {
        return "solid";
      }
      if (isWeakAssistiveEdge(edge)) {
        return "weak";
      }
      if (isStrongAssistiveEdge(edge)) {
        return "strong";
      }
      return "normal";
    }

    function assistiveEdgeColor(edge, visibleEdges) {
      const kind = assistiveEdgeKind(edge);
      if (kind === "weak") {
        return { color: visibleEdges.length > 1200 ? "rgba(100, 116, 139, 0.055)" : "rgba(100, 116, 139, 0.12)", highlight: "#64748b" };
      }
      if (kind === "strong") {
        return { color: "rgba(79, 70, 229, 0.18)", highlight: "#4338ca" };
      }
      if (kind === "normal") {
        return { color: "rgba(37, 99, 235, 0.10)", highlight: "#1d4ed8" };
      }
      return getEdgeColor(visibleEdges);
    }

    function bridgeCategoryIds(edge) {
      if (!isAssistiveEdge(edge)) {
        return [];
      }
      const text = `${edge.description || ""} ${edge.review_notes || ""}`.toLowerCase();
      const categories = bridgeCategoryDefinitions
        .filter((category) => category.id !== "other")
        .filter((category) => category.patterns.some((pattern) => text.includes(String(pattern).toLowerCase())))
        .map((category) => category.id);
      return categories.length ? categories : ["other"];
    }

    function bridgeCategoryLabel(categoryId) {
      return bridgeCategoryDefinitions.find((category) => category.id === categoryId)?.label || categoryId;
    }

    function visiblePersonDegreeById(visibleEdges) {
      const personDegrees = new Map();
      visibleEdges.forEach((edge) => {
        const source = rawNodeById.get(edge.source);
        const target = rawNodeById.get(edge.target);
        if (!source || !target || source.type !== "person" || target.type !== "person") {
          return;
        }
        personDegrees.set(edge.source, (personDegrees.get(edge.source) || 0) + 1);
        personDegrees.set(edge.target, (personDegrees.get(edge.target) || 0) + 1);
      });
      return personDegrees;
    }

    function renderFeaturedNodes() {
      const featuredNodes = currentVisibleNodes
        .filter((node) => accountNodeTypes.has(node.type) && hasRealProfileIcon(node) && isNetworkRelevantPerson(node) && (node.follower_count || 0) > 0)
        .sort((left, right) =>
          (right.follower_count || 0) - (left.follower_count || 0) ||
          (nodeDegreeById.get(right.id) || 0) - (nodeDegreeById.get(left.id) || 0) ||
          left.name.localeCompare(right.name, "ja")
        )
        .slice(0, 12);
      if (!featuredNodes.length) {
        return "";
      }
      return `
        <div class="detail-section">
          <h4>フォロワー上位</h4>
          <div class="featured-node-list">
            ${featuredNodes.map((node) => `
              <button type="button" class="featured-node-card" data-node-id="${escapeHtml(node.id)}">
                ${formatNodeAvatar(node, "avatar-thumb")}
                <span class="node-name-text">
                  <strong>#${escapeHtml(followerRankById.get(node.id) || "-")} ${escapeHtml(node.name)}</strong>
                  <span class="muted">X followers: ${escapeHtml(formatNumber(node.follower_count || 0))}</span>
                </span>
              </button>
            `).join("")}
          </div>
        </div>
      `;
    }

    function renderSparseFollowerNodes() {
      const personDegrees = visiblePersonDegreeById(currentVisibleEdges);
      const sparseNodes = currentVisibleNodes
        .filter((node) =>
          node.type === "person" &&
          isNetworkRelevantPerson(node) &&
          (node.follower_count || 0) >= 1000 &&
          (personDegrees.get(node.id) || 0) < 8
        )
        .sort((left, right) =>
          (right.follower_count || 0) - (left.follower_count || 0) ||
          (personDegrees.get(left.id) || 0) - (personDegrees.get(right.id) || 0) ||
          left.name.localeCompare(right.name, "ja")
        )
        .slice(0, 16);
      if (!sparseNodes.length) {
        return "";
      }
      return `
        <div class="detail-section">
          <h4>精査候補（フォロワー順）</h4>
          <div class="featured-node-list">
            ${sparseNodes.map((node, index) => `
              <button type="button" class="featured-node-card" data-node-id="${escapeHtml(node.id)}">
                ${formatNodeAvatar(node, "avatar-thumb")}
                <span class="node-name-text">
                  <strong>#${escapeHtml(index + 1)} ${escapeHtml(node.name)}</strong>
                  <span class="muted">人物間 ${escapeHtml(personDegrees.get(node.id) || 0)} / X followers: ${escapeHtml(formatNumber(node.follower_count || 0))}</span>
                </span>
              </button>
            `).join("")}
          </div>
        </div>
      `;
    }

    function thinCandidateScore(node) {
      const degree = nodeDegreeById.get(node.id) || 0;
      const solidDegree = solidDegreeById.get(node.id) || 0;
      const followers = node.follower_count || 0;
      let score = 0;
      if (followers >= 100000) {
        score += 72;
      } else if (followers >= 10000) {
        score += 56;
      } else if (followers >= 1000) {
        score += 38;
      } else if (followers > 0) {
        score += 18;
      } else {
        score += 8;
      }
      if (solidDegree === 0) {
        score += 26;
      } else if (solidDegree < 3) {
        score += 14;
      } else if (solidDegree < 8) {
        score += 6;
      }
      if (!hasRealProfileIcon(node)) {
        score += 10;
      }
      if (!node.description || /^X profile for\\s/i.test(String(node.description))) {
        score += 8;
      }
      return score;
    }

    function thinPriorityLabel(score) {
      if (score >= 80) {
        return "高";
      }
      if (score >= 45) {
        return "中";
      }
      return "低";
    }

    function thinPriorityClass(score) {
      if (score >= 80) {
        return "tag-review";
      }
      if (score >= 45) {
        return "tag-evidence-interpretation";
      }
      return "tag-evidence-mixed";
    }

    function thinCandidateReasons(node) {
      const reasons = ["関連語なし"];
      const degree = nodeDegreeById.get(node.id) || 0;
      const solidDegree = solidDegreeById.get(node.id) || 0;
      const assistiveDegree = assistiveDegreeById.get(node.id) || 0;
      const followers = node.follower_count || 0;
      if (followers >= 10000) {
        reasons.push("高フォロワー外れ値");
      } else if (followers === 0) {
        reasons.push("フォロワー未取得");
      }
      if (degree === 0) {
        reasons.push("関係線なし");
      } else if (solidDegree === 0) {
        reasons.push("確定寄り関係線なし");
      } else if (solidDegree < 3) {
        reasons.push(`確定寄り ${solidDegree} 本`);
      }
      if (assistiveDegree) {
        reasons.push(`自動補助 ${assistiveDegree} 本`);
      }
      if (!hasRealProfileIcon(node)) {
        reasons.push("実アイコン未取得");
      }
      if (!node.description || /^X profile for\\s/i.test(String(node.description))) {
        reasons.push("プロフィール本文が薄い");
      }
      return reasons;
    }

    function buildThinCandidateEntries(term) {
      return rawGraph.nodes
        .filter((node) => node.type === "person" && !isNetworkRelevantPerson(node))
        .filter((node) => thinCandidateDecisionStatus(node.id) !== "exclude")
        .filter((node) => matchesSearch(node, term))
        .map((node) => ({
          node,
          score: thinCandidateScore(node),
          reasons: thinCandidateReasons(node),
          solidDegree: solidDegreeById.get(node.id) || 0,
          assistiveDegree: assistiveDegreeById.get(node.id) || 0,
          decision: thinCandidateDecision(node.id)
        }))
        .sort((left, right) =>
          right.score - left.score ||
          (right.node.follower_count || 0) - (left.node.follower_count || 0) ||
          left.node.name.localeCompare(right.node.name, "ja")
        );
    }

    function normalizeThinDecisionEntry(nodeId, decision) {
      const node = rawNodeById.get(nodeId) || {};
      return {
        node_id: String(decision.node_id || nodeId || "").trim(),
        status: String(decision.status || "").trim(),
        note: String(decision.note || "").trim(),
        name: String(decision.name || node.name || nodeId || "").trim(),
        score: Number(decision.score || 0),
        degree: Number(decision.degree || 0),
        solid_degree: Number(decision.solid_degree || decision.degree || 0),
        assistive_degree: Number(decision.assistive_degree || 0),
        reasons: Array.isArray(decision.reasons) ? decision.reasons : [],
        updated_at: String(decision.updated_at || "").trim()
      };
    }

    function buildThinDecisionEntries() {
      return Object.entries(rawThinCandidateDecisions.decisions || {})
        .map(([nodeId, decision]) => normalizeThinDecisionEntry(nodeId, decision || {}))
        .filter((entry) => entry.node_id)
        .sort((left, right) =>
          right.updated_at.localeCompare(left.updated_at) ||
          right.score - left.score ||
          left.name.localeCompare(right.name, "ja")
        );
    }

    function renderThinReviewSummary(thinCandidateEntries, thinDecisionEntries) {
      const container = document.getElementById("thin-review-summary");
      if (!container) {
        return;
      }
      const statusCounts = thinDecisionEntries.reduce((counts, entry) => {
        const status = entry.status || "unknown";
        counts[status] = (counts[status] || 0) + 1;
        return counts;
      }, {});
      const reviewRemaining = thinCandidateEntries.filter((entry) => String(entry.decision?.status || "").trim() === "review").length;
      const undecidedRemaining = thinCandidateEntries.filter((entry) => !String(entry.decision?.status || "").trim()).length;
      const highPriorityRemaining = thinCandidateEntries.filter((entry) => entry.score >= 80).length;
      const highPriorityBatch = thinCandidateEntries.filter((entry) => entry.score >= 80).slice(0, 20);
      const latestDecision = thinDecisionEntries[0] || null;
      const completionNote = thinCandidateEntries.length
        ? `表示中候補 ${thinCandidateEntries.length} 件のうち、未判断 ${undecidedRemaining} 件 / 要確認 ${reviewRemaining} 件です。`
        : "現在の検索条件で薄い候補は残っていません。新しい候補が入るとこの表に表示されます。";
      const latestNote = latestDecision
        ? `最新判断: ${formatThinDecisionStatus(latestDecision.status)} / ${latestDecision.name || latestDecision.node_id} / score ${latestDecision.score || 0}`
        : "保存済み判断はまだありません。";
      const bulkActions = highPriorityBatch.length
        ? `
          <div class="command-actions">
            <button type="button" class="inspect-button" data-copy-command="${escapeHtml(thinBulkDecisionCommand(highPriorityBatch, "exclude"))}">高優先20 exclude</button>
            <button type="button" class="inspect-button" data-copy-command="${escapeHtml(thinBulkDecisionCommand(highPriorityBatch, "review"))}">高優先20 review</button>
          </div>
        `
        : "";
      container.innerHTML = `
        <span class="tag">表示中候補 ${escapeHtml(thinCandidateEntries.length)} 件</span>
        <span class="tag">未判断 ${escapeHtml(undecidedRemaining)} 件</span>
        <span class="tag tag-review">高優先 ${escapeHtml(highPriorityRemaining)} 件</span>
        <span class="tag tag-evidence-fact">keep ${escapeHtml(statusCounts.keep || 0)}</span>
        <span class="tag tag-review">exclude ${escapeHtml(statusCounts.exclude || 0)}</span>
        <span class="tag tag-evidence-interpretation">review ${escapeHtml(statusCounts.review || 0)}</span>
        <span class="summary-note">${escapeHtml(completionNote)} ${escapeHtml(latestNote)}</span>
        ${bulkActions}
      `;
    }

    function renderStarterPanel() {
      return `
        <div class="detail-section">
          <h4>おすすめビュー</h4>
          <div class="starter-actions">
            <button type="button" class="action-button" data-starter-action="connectivity">近い関係</button>
            <button type="button" class="action-button" data-starter-action="keyword">キーワード</button>
            <button type="button" class="action-button" data-starter-action="solid">確定寄り</button>
            <button type="button" class="action-button" data-starter-action="online">アプリ/オンライン</button>
            <button type="button" class="action-button" data-starter-action="all-accounts">全人物</button>
          </div>
        </div>
      `;
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
          edgeCount: 0,
          solidCount: 0,
          bridgeCount: 0,
          strongBridgeCount: 0,
          weakBridgeCount: 0,
          bridgeCategories: new Map(),
          maxConfidence: 0
        };
        entry[direction].add(formatEdgeType(edge.type));
        entry.edgeCount += 1;
        entry.maxConfidence = Math.max(entry.maxConfidence, Number(edge.confidence || 0));
        if (isAssistiveEdge(edge)) {
          entry.bridgeCount += 1;
          if (isWeakAssistiveEdge(edge)) {
            entry.weakBridgeCount += 1;
          } else if (isStrongAssistiveEdge(edge)) {
            entry.strongBridgeCount += 1;
          }
          bridgeCategoryIds(edge).forEach((categoryId) => {
            entry.bridgeCategories.set(categoryId, (entry.bridgeCategories.get(categoryId) || 0) + 1);
          });
        } else {
          entry.solidCount += 1;
        }
        grouped.set(otherId, entry);
      }

      outgoingEdges.forEach((edge) => addEdge(edge, "outgoing"));
      incomingEdges.forEach((edge) => addEdge(edge, "incoming"));

      const entries = Array.from(grouped.values()).sort((left, right) => {
        const leftIsBridgeOnly = left.solidCount === 0;
        const rightIsBridgeOnly = right.solidCount === 0;
        if (leftIsBridgeOnly !== rightIsBridgeOnly) {
          return leftIsBridgeOnly ? 1 : -1;
        }
        if (left.node.type === "person" && right.node.type === "person") {
          return (
            (right.node.follower_count || 0) - (left.node.follower_count || 0) ||
            right.edgeCount - left.edgeCount ||
            right.maxConfidence - left.maxConfidence ||
            left.node.name.localeCompare(right.node.name, "ja")
          );
        }
        return (
          right.edgeCount - left.edgeCount ||
          right.maxConfidence - left.maxConfidence ||
          left.node.name.localeCompare(right.node.name, "ja")
        );
      });
      if (!entries.length) {
        return '<div class="detail-empty">現在表示中のつながりノードはありません。</div>';
      }

      const entriesByType = new Map();
      const bridgeCategoryOrder = [
        "online",
        "street",
        "club",
        "miso",
        "mbh",
        "lesson",
        "community",
        "close",
        "business",
        "nightlife",
        "other"
      ];
      function primaryBridgeCategory(entry) {
        if (!entry.bridgeCategories.size) {
          return "other";
        }
        return bridgeCategoryOrder.find((categoryId) => entry.bridgeCategories.has(categoryId)) || "other";
      }
      function bridgeCategoryOrderIndex(categoryId) {
        const index = bridgeCategoryOrder.indexOf(categoryId);
        return index === -1 ? bridgeCategoryOrder.length : index;
      }
      entries.forEach((entry) => {
        const relationGroup = entry.solidCount > 0 ? "solid" : "bridge";
        const nodeType = relationGroup === "bridge"
          ? `${relationGroup}:${primaryBridgeCategory(entry)}:${entry.node.type || "person"}`
          : `${relationGroup}:${entry.node.type || "person"}`;
        if (!entriesByType.has(nodeType)) {
          entriesByType.set(nodeType, []);
        }
        entriesByType.get(nodeType).push(entry);
      });

      const sectionOrder = [
        ["solid:person", "人物 / 確定寄り"],
        ...bridgeCategoryOrder.map((categoryId) => [
          `bridge:${categoryId}:person`,
          `人物 / ${bridgeCategoryLabel(categoryId)}`
        ]),
        ["solid:community", "コミュニティ / 確定寄り"],
        ...bridgeCategoryOrder.map((categoryId) => [
          `bridge:${categoryId}:community`,
          `コミュニティ / ${bridgeCategoryLabel(categoryId)}`
        ]),
        ["solid:platform", "媒体 / 確定寄り"],
        ...bridgeCategoryOrder.map((categoryId) => [
          `bridge:${categoryId}:platform`,
          `媒体 / ${bridgeCategoryLabel(categoryId)}`
        ]),
        ["solid:location", "場所 / 確定寄り"],
        ...bridgeCategoryOrder.map((categoryId) => [
          `bridge:${categoryId}:location`,
          `場所 / ${bridgeCategoryLabel(categoryId)}`
        ]),
        ["solid:content", "コンテンツ / 確定寄り"],
        ...bridgeCategoryOrder.map((categoryId) => [
          `bridge:${categoryId}:content`,
          `コンテンツ / ${bridgeCategoryLabel(categoryId)}`
        ])
      ];

      return `
        <div class="connected-node-list">
          ${sectionOrder
            .filter(([sectionKey]) => entriesByType.has(sectionKey))
            .map(([sectionKey, sectionLabel]) => `
              <section class="connected-type-group">
                <div class="connected-type-heading">
                  <strong>${escapeHtml(sectionLabel)}</strong>
                  <span>${escapeHtml(entriesByType.get(sectionKey).length)} 件</span>
                </div>
                ${entriesByType.get(sectionKey).map((entry) => `
                  <div class="connected-node-card ${entry.solidCount === 0 ? "is-bridge" : ""}">
                    <div class="connected-node-header">
                      <div class="node-name-cell connected-node-body">
                        ${formatNodeAvatar(entry.node, "avatar-thumb")}
                        <div class="node-name-text">
                          <strong>${escapeHtml(entry.node.name)}</strong><br>
                          <span class="muted">${escapeHtml(entry.node.id)}</span>
                          <div class="connected-node-rank">
                            ${entry.node.follower_count ? `X followers: ${escapeHtml(formatNumber(entry.node.follower_count))} / ` : ""}
                            関係 ${escapeHtml(entry.edgeCount)} / 確定 ${escapeHtml(entry.solidCount)} / 補助 ${escapeHtml(entry.bridgeCount)}
                          </div>
                        </div>
                      </div>
                      <button type="button" class="inspect-button" data-node-id="${escapeHtml(entry.node.id)}">見る</button>
                    </div>
                    <div class="connected-node-tags">
                      ${entry.solidCount ? `<span class="tag tag-solid">確定寄り ${escapeHtml(entry.solidCount)}</span>` : ""}
                      ${entry.bridgeCount ? `<span class="tag tag-bridge">補助線 ${escapeHtml(entry.bridgeCount)}</span>` : ""}
                      ${entry.strongBridgeCount ? `<span class="tag tag-strong-bridge">強補助線 ${escapeHtml(entry.strongBridgeCount)}</span>` : ""}
                      ${entry.weakBridgeCount ? `<span class="tag tag-weak-bridge">弱補助線 ${escapeHtml(entry.weakBridgeCount)}</span>` : ""}
                      ${Array.from(entry.bridgeCategories.entries())
                        .sort((left, right) => bridgeCategoryOrderIndex(left[0]) - bridgeCategoryOrderIndex(right[0]))
                        .map(([categoryId, count]) => `<span class="tag">${escapeHtml(bridgeCategoryLabel(categoryId))} ${escapeHtml(count)}</span>`)
                        .join("")}
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

    function selectedNodeRelationEdges(nodeId) {
      // Detail / ego view: always surface solid relations for the focus node so
      // hubs like K@マチアプの王 never show "つながっているノード (0)" while
      // solidDegreeById is high. Assistive edges still respect the bridge toggle.
      const allowedEdgeTypes = selectedValues("[data-edge-type]", "data-edge-type");
      const includeProfileBridgeEdges = document.getElementById("profile-bridge-toggle")?.checked === true;
      const allowedBridgeCategories = selectedValues("[data-bridge-category]", "data-bridge-category");
      const onlyRelevantAccounts = document.getElementById("relevance-filter-toggle")?.checked !== false;
      const solidEdgeTypes = new Set([
        "follow",
        "profile_mention",
        "influence",
        "collaboration",
        "activity",
        "affiliation",
        "criticism",
        "monetization"
      ]);
      const outgoing = [];
      const incoming = [];
      rawGraph.edges.forEach((edge) => {
        if (edge.source !== nodeId && edge.target !== nodeId) {
          return;
        }
        const assistive = isAssistiveEdge(edge);
        if (assistive) {
          if (!includeProfileBridgeEdges) {
            return;
          }
          if (allowedEdgeTypes.size && !allowedEdgeTypes.has(edge.type)) {
            return;
          }
          const categories = bridgeCategoryIds(edge);
          if (!categories.some((categoryId) => allowedBridgeCategories.has(categoryId))) {
            return;
          }
        } else {
          // Solid person hub edges always show in the detail panel.
          if (!solidEdgeTypes.has(edge.type) && allowedEdgeTypes.size && !allowedEdgeTypes.has(edge.type)) {
            return;
          }
        }
        const otherId = edge.source === nodeId ? edge.target : edge.source;
        const other = rawNodeById.get(otherId);
        if (!other) {
          return;
        }
        // Keep solid person neighbors even if periphery filter is on; the user
        // explicitly opened this hub and should see who it follows.
        if (
          onlyRelevantAccounts &&
          assistive &&
          other.type === "person" &&
          !isNetworkRelevantPerson(other)
        ) {
          return;
        }
        if (edge.source === nodeId) {
          outgoing.push(edge);
        } else {
          incoming.push(edge);
        }
      });
      return { outgoing, incoming };
    }

    function renderDetailPanel(nodeId) {
      const panel = document.getElementById("detail-panel");
      const node = rawNodeById.get(nodeId) || currentVisibleNodes.find((candidate) => candidate.id === nodeId);
      if (!node) {
        selectedNodeId = null;
        panel.innerHTML = `
          <div class="detail-empty">相関図かノード一覧から 1 件選ぶと、右側に説明とつながっているノードを表示します。</div>
          ${renderStarterPanel()}
          ${renderFeaturedNodes()}
          ${renderSparseFollowerNodes()}
        `;
        return;
      }

      selectedNodeId = nodeId;
      const { outgoing: outgoingEdges, incoming: incomingEdges } = selectedNodeRelationEdges(nodeId);
      const connectedCount = new Set([
        ...outgoingEdges.map((edge) => edge.target),
        ...incomingEdges.map((edge) => edge.source)
      ]).size;
      panel.innerHTML = `
        <div class="detail-card">
          <div class="detail-heading">
            ${formatNodeAvatar(node, "detail-avatar")}
            <h3>${escapeHtml(node.name)}</h3>
          </div>
          <div class="detail-meta">
            ${renderNodeTypeTag(node.type)}
            <span class="muted">${escapeHtml(node.id)}</span><br>
            ${node.follower_count ? `<span class="muted">X followers: ${escapeHtml(formatNumber(node.follower_count))}</span><br>` : ""}
            <span class="muted">確定寄り次数: ${escapeHtml(solidDegreeById.get(node.id) || 0)} / follow ${escapeHtml(followDegreeById.get(node.id) || 0)}</span><br>
            <span class="muted">確信度: ${escapeHtml(node.confidence)}</span>
            ${node.review_notes ? `<br><span class="muted">確認メモ: ${escapeHtml(node.review_notes)}</span>` : ""}
          </div>

          <div class="detail-section">
            <h4>つながっているノード (${connectedCount})</h4>
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
      if (!rawNodeById.has(nodeId)) {
        return;
      }
      selectedNodeId = nodeId;
      // Expand ego neighborhood into the canvas before focusing.
      applyFilters();
      const clusterMode = getClusterMode();
      const clusterAssignment = rawClusters.modes?.[clusterMode]?.assignments?.[nodeId];
      const clusterId = clusterAssignment ? getClusterNodeId(clusterAssignment) : null;
      if (clusterId && network.isCluster(clusterId)) {
        network.openCluster(clusterId);
      }
      if (currentVisibleNodes.some((node) => node.id === nodeId)) {
        network.selectNodes([nodeId]);
        network.focus(nodeId, {
          scale: 1.05,
          animation: {
            duration: 300,
            easingFunction: "easeInOutQuad"
          }
        });
      }
      renderDetailPanel(nodeId);
      updateNetworkEmphasis(nodeId);
    }

    function revealAndFocusNode(nodeId) {
      const relevanceFilterToggle = document.getElementById("relevance-filter-toggle");
      if (relevanceFilterToggle) {
        relevanceFilterToggle.checked = true;
      }
      document.querySelectorAll("[data-node-type]").forEach((input) => {
        if (input.getAttribute("data-node-type") === "person") {
          input.checked = true;
        }
      });
      document.querySelectorAll("[data-edge-type]").forEach((input) => {
        input.checked = true;
      });
      document.getElementById("search").value = "";
      if (keywordClusterInput) {
        keywordClusterInput.value = "";
      }
      updateKeywordClusterControl();
      selectedNodeId = nodeId;
      applyFilters();
      window.setTimeout(() => focusNode(nodeId), 120);
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

    function renderThinCandidateTable(entries) {
      renderTableSlice({
        items: entries,
        tableKey: "thinCandidates",
        tbodyId: "thin-candidates-table",
        statusId: "thin-candidates-table-status",
        moreButtonId: "thin-candidates-table-more",
        emptyHtml: '<tr><td colspan="8" class="muted">現在の検索条件に一致する薄い候補はありません。</td></tr>',
        renderRow: (entry) => {
          const node = entry.node;
          const degree = nodeDegreeById.get(node.id) || 0;
          const followDegree = followDegreeById.get(node.id) || 0;
          const solidDegree = entry.solidDegree || 0;
          const assistiveDegree = entry.assistiveDegree || 0;
          const decisionStatus = String(entry.decision?.status || "").trim();
          const decisionNote = String(entry.decision?.note || "").trim();
          const keepCommand = thinDecisionCommand(node.id, "keep");
          const excludeCommand = thinDecisionCommand(node.id, "exclude");
          const reviewCommand = thinDecisionCommand(node.id, "review");
          return `
            <tr>
              <td><button type="button" class="inspect-button" data-thin-node-id="${escapeHtml(node.id)}">表示</button></td>
              <td><span class="tag ${thinPriorityClass(entry.score)}">${escapeHtml(thinPriorityLabel(entry.score))}</span><br><span class="muted">${escapeHtml(entry.score)}</span></td>
              <td><div class="node-name-cell">${formatNodeAvatar(node, "avatar-thumb")}<div class="node-name-text"><strong>${escapeHtml(node.name)}</strong><br><span class="muted">${escapeHtml(node.id)}</span></div></div></td>
              <td>${entry.reasons.map((reason) => `<span class="tag">${escapeHtml(reason)}</span>`).join("")}${decisionStatus ? `<br><span class="muted">判断: ${escapeHtml(decisionStatus)}${decisionNote ? ` / ${escapeHtml(decisionNote)}` : ""}</span>` : ""}</td>
              <td>${escapeHtml(formatNumber(node.follower_count || 0))}</td>
              <td>${escapeHtml(solidDegree)} 確定寄り<br><span class="muted">補助 ${escapeHtml(assistiveDegree)} / 全 ${escapeHtml(degree)} / follow ${escapeHtml(followDegree)}</span></td>
              <td>${formatLinkList(node.source_urls || [])}</td>
              <td>
                <div class="command-actions">
                  <button type="button" class="inspect-button" data-copy-command="${escapeHtml(keepCommand)}">keep</button>
                  <button type="button" class="inspect-button" data-copy-command="${escapeHtml(excludeCommand)}">exclude</button>
                  <button type="button" class="inspect-button" data-copy-command="${escapeHtml(reviewCommand)}">review</button>
                </div>
                <span class="command-hint muted">コピー後、terminal で実行します。</span>
              </td>
            </tr>
          `;
        }
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

    function renderThinCandidateDecisionTable(decisionEntries) {
      renderTableSlice({
        items: decisionEntries,
        tableKey: "thinCandidateDecisions",
        tbodyId: "thin-candidate-decisions-table",
        statusId: "thin-candidate-decisions-table-status",
        moreButtonId: "thin-candidate-decisions-table-more",
        emptyHtml: '<tr><td colspan="6" class="muted">保存済みの薄い候補判断はありません。</td></tr>',
        renderRow: (entry) => {
          const node = rawNodeById.get(entry.node_id);
          return `
            <tr>
              <td><button type="button" class="inspect-button" data-thin-node-id="${escapeHtml(entry.node_id)}">表示</button></td>
              <td><span class="tag ${entry.status === "exclude" ? "tag-review" : "tag-evidence-fact"}">${escapeHtml(formatThinDecisionStatus(entry.status || "-"))}</span><br><span class="muted">${escapeHtml(entry.updated_at || "-")}</span></td>
              <td><div class="node-name-cell">${formatNodeAvatar(node, "avatar-thumb")}<div class="node-name-text"><strong>${escapeHtml(entry.name || entry.node_id)}</strong><br><span class="muted">${escapeHtml(entry.node_id)}</span></div></div></td>
              <td>${escapeHtml(entry.score || 0)}<br><span class="muted">確定寄り ${escapeHtml(entry.solid_degree || 0)} / 補助 ${escapeHtml(entry.assistive_degree || 0)}</span></td>
              <td>${entry.reasons.map((reason) => `<span class="tag">${escapeHtml(reason)}</span>`).join("") || '<span class="muted">-</span>'}</td>
              <td>${escapeHtml(entry.note || "-")}</td>
            </tr>
          `;
        }
      });
    }

    function renderVisibleTables() {
      renderNodeTable(currentVisibleNodes);
      renderEdgeTable(currentVisibleEdges, currentNodeNameById);
      renderReviewNodeTable(currentVisibleReviewNodes);
      renderReviewEdgeTable(currentVisibleReviewEdges, currentNodeNameById);
      renderThinReviewSummary(currentThinCandidateEntries, currentThinDecisionEntries);
      renderThinCandidateTable(currentThinCandidateEntries);
      renderReviewCandidateTable(currentVisibleReviewCandidates, currentNodeNameById);
      renderReviewCandidateDecisionTable(currentVisibleReviewCandidateDecisions, currentNodeNameById);
      renderThinCandidateDecisionTable(currentThinDecisionEntries);
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
        const clusterId = clusterBucketIdForNode(node, clusterMode, modePayload);
        if (!clusterId) {
          return;
        }
        if (!bucketMap.has(clusterId)) {
          bucketMap.set(clusterId, []);
        }
        bucketMap.get(clusterId).push(node);
      });
      const clusterIdsInOrder = Array.from(bucketMap.entries())
        .filter(([, members]) => members.length >= 3)
        .sort(([, left], [, right]) => right.length - left.length)
        .map(([clusterId]) => clusterId);
      clusterIdsInOrder.forEach((clusterId, idx) => {
        const members = bucketMap.get(clusterId);
        if (members.length < 3) {
          return;
        }
        const memberIds = new Set(members.map((node) => node.id));
        const personCount = members.filter((node) => node.type === "person").length;
        const dominantType = personCount >= (members.length - personCount) ? "person" : "community";
        const clusterNodeId = getClusterNodeId(clusterId);
        const clusterInfo = clusterInfoFor(clusterId, modePayload);
        const definition = clusterModeDefinitions[clusterMode] || clusterModeDefinitions.off;
        const clusterColor = clusterColors[idx % clusterColors.length];
        const topMembers = members
          .slice(0, 4)
          .map((node) => node.name)
          .join(", ");
        const titleText = [
          clusterInfo.title || `${clusterInfo.label || definition.label} (${members.length})`,
          topMembers + (members.length > 4 ? ` ほか ${members.length - 4} 件` : "")
        ].join("\\n");
        network.cluster({
          joinCondition(nodeOptions) {
            return memberIds.has(nodeOptions.id);
          },
          clusterNodeProperties: {
            id: clusterNodeId,
            label: members.length >= 6 ? `${clusterInfo.label || definition.label}\\n${members.length}件` : `${members.length}`,
            group: dominantType,
            value: 18 + members.length,
            shape: "hexagon",
            color: {
              background: clusterColor,
              border: "#ffffff",
              highlight: { background: clusterColor, border: "#111827" }
            },
            title: titleText,
            font: { size: 15, bold: true }
          }
        });
        activeClusterIds.add(clusterNodeId);
      });
    }

    function hashText(value) {
      let hash = 0;
      for (let index = 0; index < value.length; index += 1) {
        hash = ((hash << 5) - hash + value.charCodeAt(index)) | 0;
      }
      return Math.abs(hash);
    }

    function buildAnchor(index, spacing = 1) {
      const baseAnchors = [
        [0, 0],
        [-220, -95],
        [245, 85],
        [-70, 185],
        [330, -140],
        [-340, 120],
        [95, -220],
        [-430, -190],
        [445, 205],
        [-190, 295],
        [240, -320],
        [515, -60],
        [-530, 35]
      ];
      const anchors = baseAnchors.map(([x, y]) => [x * spacing, y * spacing]);
      if (index < anchors.length) {
        return { x: anchors[index][0], y: anchors[index][1] };
      }
      const column = (index - anchors.length) % 7;
      const row = Math.floor((index - anchors.length) / 7);
      return {
        x: ((column - 3) * 170 + (row % 2 ? 55 : 0)) * spacing,
        y: (375 + row * 120 + ((column % 2) * 35)) * spacing
      };
    }

    function computeLayoutPositions(visibleNodes, visibleEdges, clusterMode, shouldCluster) {
      const positions = new Map();
      const modePayload = rawClusters.modes?.[clusterMode] || rawClusters.modes?.connectivity;
      const clusterSpacing = shouldCluster ? (clusterMode === "region_group" ? 1.45 : 1.25) : 1;
      const buckets = new Map();

      visibleNodes.forEach((node) => {
        const assignedClusterId = clusterBucketIdForNode(node, clusterMode, modePayload);
        const bucketId = assignedClusterId || `unassigned:${hashText(node.id) % 12}`;
        if (!buckets.has(bucketId)) {
          buckets.set(bucketId, []);
        }
        buckets.get(bucketId).push(node);
      });

      const orderedBuckets = Array.from(buckets.entries())
        .sort((left, right) => {
          if (right[1].length !== left[1].length) {
            return right[1].length - left[1].length;
          }
          return left[0].localeCompare(right[0]);
        });

      const connectedCounts = new Map();
      visibleEdges.forEach((edge) => {
        connectedCounts.set(edge.source, (connectedCounts.get(edge.source) || 0) + 1);
        connectedCounts.set(edge.target, (connectedCounts.get(edge.target) || 0) + 1);
      });

      orderedBuckets.forEach(([bucketId, members], bucketIndex) => {
        const anchor = buildAnchor(bucketIndex, clusterSpacing);
        const sortedMembers = [...members].sort((left, right) => {
          const rightDegree = connectedCounts.get(right.id) || 0;
          const leftDegree = connectedCounts.get(left.id) || 0;
          if (rightDegree !== leftDegree) {
            return rightDegree - leftDegree;
          }
          return left.name.localeCompare(right.name);
        });
        const spreadX = Math.max(90, Math.min(shouldCluster ? 340 : 300, 50 + sortedMembers.length * (shouldCluster ? 9 : 8)));
        const spreadY = Math.max(70, Math.min(shouldCluster ? 270 : 240, 40 + sortedMembers.length * (shouldCluster ? 7 : 6)));

        sortedMembers.forEach((node, memberIndex) => {
          if (sortedMembers.length === 1) {
            positions.set(node.id, anchor);
            return;
          }
          const hash = hashText(`${bucketId}:${node.id}`);
          const columns = Math.max(5, Math.ceil(Math.sqrt(sortedMembers.length * 1.35)));
          const lane = memberIndex % columns;
          const row = Math.floor(memberIndex / columns);
          const rowCount = Math.ceil(sortedMembers.length / columns);
          const offsetX = (lane - (columns - 1) / 2) * (spreadX / columns) + ((hash % 23) - 11);
          const offsetY = (row - (rowCount - 1) / 2) * (spreadY / rowCount) + (((hash >> 3) % 25) - 12);
          positions.set(node.id, {
            x: anchor.x + offsetX,
            y: anchor.y + offsetY
          });
        });
      });

      return positions;
    }

    function getNodeVisualValue(node, visibleNodes) {
      const degree = nodeDegreeById.get(node.id) || 0;
      const followDegree = followDegreeById.get(node.id) || 0;
      const followerCount = node.follower_count || 0;
      const hasProfileIcon = hasRealProfileIcon(node);
      const followerVisualSize = () => {
        if (followerCount >= 1000000) return 42;
        if (followerCount >= 300000) return 38;
        if (followerCount >= 100000) return 34;
        if (followerCount >= 50000) return 31;
        if (followerCount >= 20000) return 28;
        if (followerCount >= 10000) return 25;
        if (followerCount >= 5000) return 22;
        if (followerCount >= 1000) return 19;
        if (followerCount >= 300) return 16;
        if (followerCount > 0) return 13;
        return 0;
      };
      const followerSize = followerVisualSize();
      if (node.type === "person" && followerSize > 0) {
        if (isNetworkRelevantPerson(node)) {
          return visibleNodes.length > 500 ? Math.max(9, followerSize - 6) : followerSize;
        }
        return visibleNodes.length > 500
          ? 2 + Math.min(5, Math.sqrt(Math.max(degree, followDegree)))
          : 7 + Math.min(10, Math.sqrt(Math.max(degree, followDegree) + 1) * 2);
      }
      if (visibleNodes.length > 500) {
        if (hasProfileIcon) {
          return 3 + Math.min(6, Math.sqrt(Math.max(degree, followDegree * 1.35)) * 1.1);
        }
        return 1 + Math.min(7, Math.sqrt(Math.max(degree, followDegree * 1.35)) * 1.4);
      }
      return 10 + Math.min(18, Math.sqrt(degree + 1) * 3);
    }

    function shouldShowNodeIcon(node, visibleNodes) {
      if (!node.icon_url) {
        return false;
      }
      return hasRealProfileIcon(node);
    }

    function getNodeLabel(node, visibleNodes, term) {
      if (term) {
        return visibleNodes.length <= 180 ? node.name : "";
      }
      if (visibleNodes.length > 500) {
        return defaultLabelNodeIds.has(node.id) ? node.name : "";
      }
      return visibleNodes.length <= 140 ? node.name : "";
    }

    function getEdgeColor(visibleEdges) {
      if (visibleEdges.length > 1200) {
        return { color: "rgba(148, 163, 184, 0.13)", highlight: "#1d4ed8" };
      }
      if (visibleEdges.length > 500) {
        return { color: "rgba(148, 163, 184, 0.24)", highlight: "#1d4ed8" };
      }
      return { color: "rgba(148, 163, 184, 0.42)", highlight: "#2f6feb" };
    }

    function updateNetworkEmphasis(selectedId = null) {
      if (!currentVisibleNodes.length) {
        return;
      }
      const baseEdgeColor = getEdgeColor(currentVisibleEdges);
      const neighborIds = new Set();
      if (selectedId) {
        neighborIds.add(selectedId);
        currentVisibleEdges.forEach((edge) => {
          if (edge.source === selectedId) {
            neighborIds.add(edge.target);
          }
          if (edge.target === selectedId) {
            neighborIds.add(edge.source);
          }
        });
      }

      edgesDataSet.update(
        currentVisibleEdges.map((edge, index) => {
          const related = selectedId && (edge.source === selectedId || edge.target === selectedId);
          return {
            id: `${edge.source}-${edge.target}-${edge.type}-${index}`,
            color: related
              ? { color: "#1d4ed8", highlight: "#1d4ed8" }
              : (isAssistiveEdge(edge)
                ? assistiveEdgeColor(edge, currentVisibleEdges)
                : baseEdgeColor),
            width: related ? 2.2 : (isWeakAssistiveEdge(edge) ? 0.25 : (currentVisibleEdges.length > 1200 ? 0.35 : 1))
          };
        })
      );

      nodesDataSet.update(
        currentVisibleNodes.map((node) => ({
          id: node.id,
          label: selectedId && neighborIds.has(node.id) ? node.name : getNodeLabel(node, currentVisibleNodes, currentSearchTerm)
        }))
      );
    }

    function applyFilters() {
      const allowedNodeTypes = selectedValues("[data-node-type]", "data-node-type");
      const allowedEdgeTypes = selectedValues("[data-edge-type]", "data-edge-type");
      const allowedBridgeCategories = selectedValues("[data-bridge-category]", "data-bridge-category");
      const includeProfileBridgeEdges = document.getElementById("profile-bridge-toggle")?.checked === true;
      const onlyRelevantAccounts = document.getElementById("relevance-filter-toggle")?.checked !== false;
      const term = document.getElementById("search").value.trim().toLowerCase();
      const clusterMode = getClusterMode();
      const selectedKeywordClusterId = getSelectedKeywordClusterId();
      const keywordAssignments = rawClusters.modes?.keyword_group?.assignments || {};
      const shouldCluster = false;
      const tableFilterKey = JSON.stringify({
        nodeTypes: [...allowedNodeTypes].sort(),
        edgeTypes: [...allowedEdgeTypes].sort(),
        bridgeCategories: [...allowedBridgeCategories].sort(),
        profileBridge: includeProfileBridgeEdges,
        onlyRelevantAccounts,
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
        if (onlyRelevantAccounts && node.type === "person" && !isNetworkRelevantPerson(node)) {
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

      let visibleEdges = rawGraph.edges.filter((edge) => {
        if (!allowedEdgeTypes.has(edge.type)) {
          return false;
        }
        if (isAssistiveEdge(edge)) {
          if (!includeProfileBridgeEdges) {
            return false;
          }
          const categories = bridgeCategoryIds(edge);
          if (!categories.some((categoryId) => allowedBridgeCategories.has(categoryId))) {
            return false;
          }
        }
        if (!eligibleIds.has(edge.source) || !eligibleIds.has(edge.target)) {
          return false;
        }
        if (!term) {
          return true;
        }
        return matchedIds.has(edge.source) || matchedIds.has(edge.target);
      });

      // Ego expansion: when a node is selected/searched, always include its solid
      // person-person edges even if the global periphery filter would drop them.
      let focusedEdgeExtras = [];
      const focusIds = new Set(matchedIds);
      if (selectedNodeId) {
        focusIds.add(selectedNodeId);
      }
      if (focusIds.size) {
        focusedEdgeExtras = rawGraph.edges.filter((edge) => {
          if (!(focusIds.has(edge.source) || focusIds.has(edge.target))) {
            return false;
          }
          const assistive = isAssistiveEdge(edge);
          if (assistive) {
            if (!includeProfileBridgeEdges) {
              return false;
            }
            if (allowedEdgeTypes.size && !allowedEdgeTypes.has(edge.type)) {
              return false;
            }
          } else {
            // Always pull solid follow/mention/activity edges for the focus node.
            const solidTypes = new Set([
              "follow",
              "profile_mention",
              "influence",
              "collaboration",
              "activity",
              "affiliation",
              "criticism",
              "monetization"
            ]);
            if (!solidTypes.has(edge.type) && allowedEdgeTypes.size && !allowedEdgeTypes.has(edge.type)) {
              return false;
            }
          }
          const otherId = focusIds.has(edge.source) ? edge.target : edge.source;
          const other = rawNodeById.get(otherId);
          if (!other || !accountNodeTypes.has(other.type)) {
            // Keep location/context endpoints of solid activity edges (e.g. Matching Apps).
            if (!assistive && other && (other.type === "location" || other.type === "community")) {
              return true;
            }
            return false;
          }
          // Solid neighbors of a focused hub always show; assistive still respects relevance.
          if (
            onlyRelevantAccounts &&
            assistive &&
            other.type === "person" &&
            !isNetworkRelevantPerson(other)
          ) {
            return false;
          }
          return true;
        });
      }
      const edgeKey = (edge) => `${edge.source}|${edge.target}|${edge.type}|${edge.review_notes || ""}`;
      const seenEdgeKeys = new Set(visibleEdges.map(edgeKey));
      focusedEdgeExtras.forEach((edge) => {
        const key = edgeKey(edge);
        if (!seenEdgeKeys.has(key)) {
          seenEdgeKeys.add(key);
          visibleEdges.push(edge);
        }
      });

      const visibleNodeIds = new Set();
      if (term) {
        matchedIds.forEach((nodeId) => visibleNodeIds.add(nodeId));
        // 検索ヒットは孤立でも残し、つながる相手も足す。
        visibleEdges.forEach((edge) => {
          visibleNodeIds.add(edge.source);
          visibleNodeIds.add(edge.target);
        });
      } else {
        // 外周ノイズ抑制: 表示中の関係線につながるアカウントだけ描画する。
        visibleEdges.forEach((edge) => {
          visibleNodeIds.add(edge.source);
          visibleNodeIds.add(edge.target);
        });
        if (selectedNodeId) {
          visibleNodeIds.add(selectedNodeId);
        }
      }

      // Ego neighbors may sit outside the current keyword cluster / eligible set;
      // still draw them while focused so high-follower hubs don't look isolated.
      const extraNodes = [];
      visibleNodeIds.forEach((nodeId) => {
        if (eligibleIds.has(nodeId)) {
          return;
        }
        const node = rawNodeById.get(nodeId);
        if (!node || !accountNodeTypes.has(node.type)) {
          return;
        }
        if (!allowedNodeTypes.has(node.type)) {
          return;
        }
        if (onlyRelevantAccounts && node.type === "person" && !isNetworkRelevantPerson(node)) {
          return;
        }
        extraNodes.push(node);
      });
      const visibleNodes = [
        ...eligibleNodes.filter((node) => visibleNodeIds.has(node.id)),
        ...extraNodes
      ];
      const nodeNameById = new Map(rawGraph.nodes.map((node) => [node.id, node.name]));
      const visibleReviewCandidates = (rawReviewCandidates.candidates || []).filter((candidate) =>
        visibleNodeIds.has(candidate.source) && visibleNodeIds.has(candidate.target)
      );
      const visibleReviewCandidateDecisions = Object.entries(rawReviewCandidateDecisions.decisions || {})
        .map(([candidateId, decision]) => normalizeDecisionEntry(candidateId, decision || {}))
        .filter((entry) => visibleNodeIds.has(entry.source) && visibleNodeIds.has(entry.target));
      const thinCandidateEntries = buildThinCandidateEntries(term);
      const thinDecisionEntries = buildThinDecisionEntries().filter((entry) => matchesSearch(rawNodeById.get(entry.node_id) || { id: entry.node_id, name: entry.name, description: "", aliases: [] }, term));
      currentVisibleNodes = visibleNodes;
      currentVisibleEdges = visibleEdges;
      currentVisibleReviewNodes = visibleNodes.filter((node) => node.needs_review);
      currentVisibleReviewEdges = visibleEdges.filter((edge) => edge.needs_review);
      currentThinCandidateEntries = thinCandidateEntries;
      currentThinDecisionEntries = thinDecisionEntries;
      currentVisibleReviewCandidates = visibleReviewCandidates;
      currentVisibleReviewCandidateDecisions = visibleReviewCandidateDecisions;
      currentNodeNameById = nodeNameById;
      currentSearchTerm = term;

      resetClusters();
      nodesDataSet.clear();
      edgesDataSet.clear();
      const layoutPositions = computeLayoutPositions(visibleNodes, visibleEdges, clusterMode, shouldCluster);

      nodesDataSet.add(
        visibleNodes.map((node) => ({
          id: node.id,
          x: layoutPositions.get(node.id)?.x,
          y: layoutPositions.get(node.id)?.y,
          label: getNodeLabel(node, visibleNodes, term),
          group: node.type,
          value: getNodeVisualValue(node, visibleNodes),
          shape: shouldShowNodeIcon(node, visibleNodes) ? "circularImage" : "dot",
          image: shouldShowNodeIcon(node, visibleNodes) ? node.icon_url : undefined,
          brokenImage: "icon.svg",
          borderWidth: (node.follower_count || 0) > 0 ? 2 : 1,
          color: {
            background: nodeColors[node.type] || "#64748b",
            border: (node.follower_count || 0) > 0 ? "#ffffff" : "#d8e2f0",
            highlight: { background: nodeColors[node.type] || "#64748b", border: "#111827" }
          },
          title: [
            node.name + (node.type === "person" ? "" : ` (${formatNodeType(node.type)})`),
            node.follower_count ? `X followers: ${formatNumber(node.follower_count)}` : "",
            node.follower_count ? `フォロワー順位: #${followerRankById.get(node.id) || "-"}` : "",
            node.follower_count ? "アイコンサイズ: Xフォロワー数ベース" : "",
            node.description || ""
          ]
            .filter((value) => value)
            .join("\\n")
        }))
      );

      edgesDataSet.add(
        visibleEdges.map((edge, index) => ({
          id: `${edge.source}-${edge.target}-${edge.type}-${index}`,
          from: edge.source,
          to: edge.target,
          arrows: visibleEdges.length > 1200 ? "" : "to",
          label: visibleEdges.length <= 260 ? formatEdgeType(edge.type) : undefined,
          color: isAssistiveEdge(edge)
            ? assistiveEdgeColor(edge, visibleEdges)
            : getEdgeColor(visibleEdges),
          dashes: isAssistiveEdge(edge) && visibleEdges.length <= 1200 ? (isWeakAssistiveEdge(edge) ? [2, 8] : [3, 5]) : false,
          width: isWeakAssistiveEdge(edge) ? 0.25 : (visibleEdges.length > 1200 ? 0.35 : 1),
          title: `${formatEdgeType(edge.type)}: ${edge.description || ""}${isWeakAssistiveEdge(edge) ? "\\n弱補助線: 広い特徴語だけの薄い接続" : ""}${isStrongAssistiveEdge(edge) ? "\\n強補助線: 複数特徴語または明示的な共有文脈" : ""}`
        }))
      );

      if (shouldCluster) {
        applyRelationClusters(visibleNodes, clusterMode);
      }
      updateNetworkEmphasis(selectedNodeId);
      // Avoid auto-fit when focusing a node so the ego neighborhood stays readable.
      if (!selectedNodeId && !term) {
        fitVisibleGraph();
      }

      document.getElementById("visible-nodes").textContent = visibleNodes.length;
      document.getElementById("visible-edges").textContent = visibleEdges.length;
      document.getElementById("review-nodes").textContent = currentVisibleReviewNodes.length;
      document.getElementById("review-edges").textContent = currentVisibleReviewEdges.length;
      document.getElementById("review-candidates").textContent = visibleReviewCandidates.length;
      renderSearchResults(term, visibleNodes);
      renderViewSummary(visibleNodes, visibleEdges);
      renderVisibleTables();

      if (selectedNodeId) {
        renderDetailPanel(selectedNodeId);
      } else {
        renderDetailPanel(null);
      }
    }

    function fitVisibleGraph() {
      window.setTimeout(() => {
        if (currentVisibleNodes.length) {
          network.fit({ animation: { duration: 250, easingFunction: "easeInOutQuad" } });
        }
      }, 50);
    }

    function setBridgePreset(presetId) {
      const categorySets = {
        all: bridgeCategoryDefinitions.map((category) => category.id),
        solid: [],
        online: ["online"],
        street: ["street"],
        club: ["club"],
        field: ["online", "street", "club", "close"],
        community: ["miso", "mbh", "lesson", "community"]
      };
      const enabledCategories = new Set(categorySets[presetId] || categorySets.all);
      const profileBridgeToggle = document.getElementById("profile-bridge-toggle");
      if (profileBridgeToggle) {
        profileBridgeToggle.checked = presetId !== "solid";
      }
      document.querySelectorAll("[data-bridge-category]").forEach((input) => {
        input.checked = enabledCategories.has(input.getAttribute("data-bridge-category"));
      });
      applyFilters();
      fitVisibleGraph();
    }

    function resetView() {
      document.getElementById("search").value = "";
      document.querySelectorAll("[data-node-type], [data-edge-type]").forEach((input) => {
        input.checked = true;
      });
      const relevanceFilterToggle = document.getElementById("relevance-filter-toggle");
      if (relevanceFilterToggle) {
        relevanceFilterToggle.checked = true;
      }
      if (clusterModeInput) {
        clusterModeInput.value = rawClusters.modes?.connectivity ? "connectivity" : "off";
      }
      if (keywordClusterInput) {
        keywordClusterInput.value = "";
      }
      selectedNodeId = null;
      updateClusterModeHelp();
      updateKeywordClusterControl();
      // 全体に戻すときも solid-first（自動補助線オフ）を維持する。
      setBridgePreset("solid");
    }

    const debouncedApplyFilters = debounce(applyFilters, 120);
    document.getElementById("search").addEventListener("input", debouncedApplyFilters);
    document.getElementById("search-results").addEventListener("click", (event) => {
      const button = event.target.closest("[data-node-id]");
      if (!button) {
        return;
      }
      focusNode(button.getAttribute("data-node-id"));
    });
    document.getElementById("reset-view").addEventListener("click", resetView);
    document.getElementById("fit-graph").addEventListener("click", fitVisibleGraph);
    document.querySelectorAll("[data-bridge-preset]").forEach((button) => {
      button.addEventListener("click", () => {
        setBridgePreset(button.getAttribute("data-bridge-preset"));
      });
    });
    document.getElementById("quick-connectivity").addEventListener("click", () => {
      if (clusterModeInput) {
        clusterModeInput.value = "connectivity";
        updateClusterModeHelp();
        updateKeywordClusterControl();
      }
      document.getElementById("search").value = "";
      applyFilters();
      fitVisibleGraph();
    });
    document.getElementById("quick-keyword").addEventListener("click", () => {
      if (clusterModeInput) {
        clusterModeInput.value = "keyword_group";
        updateClusterModeHelp();
        updateKeywordClusterControl();
      }
      document.getElementById("search").value = "";
      applyFilters();
      fitVisibleGraph();
    });
    document.querySelectorAll("[data-node-type], [data-edge-type], [data-bridge-category], #profile-bridge-toggle, #relevance-filter-toggle").forEach((input) => {
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
      ["thinCandidates", "thin-candidates-table-more"],
      ["reviewCandidates", "review-candidates-table-more"],
      ["reviewCandidateDecisions", "review-candidate-decisions-table-more"],
      ["thinCandidateDecisions", "thin-candidate-decisions-table-more"],
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
    document.getElementById("thin-candidates-table").addEventListener("click", async (event) => {
      const copyButton = event.target.closest("[data-copy-command]");
      if (copyButton) {
        const originalText = copyButton.textContent;
        try {
          await copyTextToClipboard(copyButton.getAttribute("data-copy-command") || "");
          copyButton.textContent = "コピー済";
          window.setTimeout(() => {
            copyButton.textContent = originalText;
          }, 1200);
        } catch (error) {
          copyButton.textContent = "失敗";
          window.setTimeout(() => {
            copyButton.textContent = originalText;
          }, 1200);
        }
        return;
      }
      const button = event.target.closest("[data-thin-node-id]");
      if (!button) {
        return;
      }
      revealAndFocusNode(button.getAttribute("data-thin-node-id"));
    });
    document.getElementById("thin-candidate-decisions-table").addEventListener("click", (event) => {
      const button = event.target.closest("[data-thin-node-id]");
      if (!button) {
        return;
      }
      revealAndFocusNode(button.getAttribute("data-thin-node-id"));
    });
    document.getElementById("detail-panel").addEventListener("click", (event) => {
      const starterButton = event.target.closest("[data-starter-action]");
      if (starterButton) {
        const action = starterButton.getAttribute("data-starter-action");
        if (action === "connectivity") {
          document.getElementById("quick-connectivity").click();
        } else if (action === "keyword") {
          document.getElementById("quick-keyword").click();
        } else if (action === "solid" || action === "online") {
          setBridgePreset(action);
          applyFilters();
        } else if (action === "all-accounts") {
          const relevanceFilterToggle = document.getElementById("relevance-filter-toggle");
          if (relevanceFilterToggle) {
            relevanceFilterToggle.checked = false;
          }
          applyFilters();
        }
        return;
      }
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
          updateNetworkEmphasis(selectedId);
          renderDetailPanel(selectedId);
        }
      }
    });
    network.on("deselectNode", () => {
      updateNetworkEmphasis(null);
      renderDetailPanel(null);
    });

    updateClusterModeHelp();
    updateKeywordClusterControl();
    // solid-first: 自動補助線オフ + 関係線につながるノードのみ表示。
    setBridgePreset("solid");
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
    thin_candidate_decisions_payload: dict[str, Any] | None = None,
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
        "thin_candidate_decisions": thin_candidate_decisions_payload
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
            thin_candidate_decisions_payload=thin_candidate_decisions_payload,
            growth_targets_payload=growth_targets_payload,
            site_data_path=site_data_file.name,
        ),
        encoding="utf-8",
        newline="",
    )
