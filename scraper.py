from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from urllib.parse import urlparse

from graph_model import (
    EDGE_TYPES,
    EVIDENCE_KINDS,
    GraphData,
    NODE_TYPES,
    add_edge,
    add_node,
    export_csv,
    export_html,
    export_networkx_metrics,
    export_sqlite,
    load_graph,
    query_relations,
    save_graph,
)

SEED_FILE = Path("seed_entities.txt")
SNAPSHOT_FILE = Path("data/source_snapshots.json")
GENERATED_SNAPSHOT_FILE = Path("data/source_snapshots.generated.json")
GENERATED_HINT_SNAPSHOT_FILE = Path("data/source_snapshots.generated.hints.json")
REVIEW_CANDIDATES_JSON = Path("data/review_candidates.json")
REVIEW_CANDIDATE_DECISIONS_JSON = Path("data/review_candidate_decisions.json")
THIN_CANDIDATE_DECISIONS_JSON = Path("data/thin_candidate_decisions.json")
NODES_JSON = Path("data/nodes.json")
EDGES_JSON = Path("data/edges.json")
NODES_CSV = Path("data/nodes.csv")
EDGES_CSV = Path("data/edges.csv")
NETWORKX_METRICS = Path("data/networkx_metrics.json")
SQLITE_DB = Path("data/graph.db")
ALLOWED_URL_SCHEMES = {"http", "https", "manual"}
SEED_SCOPES = ("real", "fictional", "unspecified")
REVIEW_CANDIDATE_TEXT_FIELDS = ("summary", "profile_text", "pinned_post_text")
REVIEW_CANDIDATE_BASE_CONFIDENCE = {
    "summary": 0.34,
    "profile_text": 0.4,
    "pinned_post_text": 0.46,
}
NETWORK_RELEVANCE_KEYWORDS = (
    "ナンパ",
    "pua",
    "即",
    "ストナン",
    "ネトナン",
    "クラナン",
    "ストリート",
    "路上",
    "nanpa",
    "nannpa",
    "nampa",
    "stonan",
    "rojou",
    "suto_nan",
    "suto-nan",
    "netonan",
    "kuranan",
    "street",
    "tinder",
    "tapple",
    "pairs",
    "omiai",
    "タップル",
    "ペアーズ",
    "東カレ",
    "mote",
    "マッチングアプリ",
    "講習",
    "コンサル",
    "モテ",
    "攻略",
    "美女攻略",
    "恋愛",
    "界隈",
    "一門",
    "味噌",
    "mbh",
    "こりら",
    "アツスト",
    "女遊び",
    "経験人数",
    "箱",
    "クラブ",
)
THIN_CANDIDATE_STATUSES = ("keep", "exclude", "review")
REAL_GROWTH_TARGETS = {
    "person": {"min": 1000, "max": 1000},
    "community": {"min": 8, "max": 12},
    "content": {"min": 12, "max": 18},
    "location": {"min": 8, "max": 14},
    "platform": {"min": 6, "max": 8},
}
REAL_GROWTH_PHASES = (
    {"label": "Phase 1", "real_person_target": 20},
    {"label": "Phase 2", "real_person_target": 50},
    {"label": "Phase 3", "real_person_target": 100},
    {"label": "Phase 4", "real_person_target": 200},
    {"label": "Phase 5", "real_person_target": 500},
    {"label": "Phase 6", "real_person_target": 1000},
)
CJK_TOKEN_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]")
X_STYLE_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{3,15}$")
AMBIGUOUS_PERSON_ALIAS_RE = re.compile(r"^[A-Za-zＡ-Ｚａ-ｚ][氏さん様くんちゃん]$")
DEFAULT_MATERIALIZED_REVIEW_EDGE_TYPES = frozenset(
    {"profile_mention", "activity", "collaboration", "influence", "affiliation"}
)
LEGACY_EDGE_TYPES = frozenset({"reference"})
FOLLOW_REFERENCE_PREFIX = "authenticated x following list shows this account follows @"
PROFILE_BRIDGE_PATTERNS = (
    ("即", ("即",)),
    ("即報", ("即報",)),
    ("合流", ("合流",)),
    ("講習", ("講習", "コンサル")),
    ("恋愛", ("恋愛", "婚活", "恋活", "出会い", "出逢い")),
    ("モテ", ("モテ", "彼女")),
    ("美女", ("美女",)),
    ("男磨き", ("男磨き",)),
    ("ナンパ", ("ナンパ", "nampa", "nanpa", "界隈")),
    ("PUA", ("pua", "PUA")),
    ("プレイヤー", ("プレイヤー",)),
    ("女遊び", ("女遊び", "女の子")),
    ("ヒモ", ("ヒモ", "himo", "貢がせ", "奢られ")),
    ("港区", ("港区女子", "ギャラ飲み", "パパ活", "港区おじ")),
    ("セフレ", ("セフレ", "セックスフレンド", "都合のいい")),
    ("デート", ("デート", "王道彼氏", "彼氏感")),
    ("攻略", ("攻略", "人斬り", "斬り")),
    ("ストリート", ("ストナン", "stonan", "sutonan", "sutonanpa", "street", "スト値", "スト高", "スト師", "路上", "街", "ストリート")),
    ("アプリ/オンライン", ("アプリ", "Tinder", "tinder", "ネトナン", "netonan", "netonanpa", "ネト", "オンライン", "チャットアプリ")),
    ("クラブ/箱", ("クラブ", "クラナン", "kuranan", "kurananpa", "箱", "相席", "バー", "ハプバー", "夜遊び")),
    ("関係構築", ("関係構築",)),
    ("美容/整形", ("美容", "整形", "外見", "メンズメイク", "メイク", "垢抜け", "ブサイク", "イケメン")),
    ("ファッション", ("ファッション", "服", "垢抜け")),
    ("筋トレ", ("筋トレ", "マッチョ", "ダイエット")),
    ("SNSマーケ", ("SNS", "マーケティング", "発信")),
    ("ビジネス", ("事業", "起業", "会社", "代表", "稼ぐ")),
    ("夜職", ("夜職", "ホスト", "港区")),
    ("旅ナンパ", ("旅ナンパ", "海外ナンパ", "旅行", "国内旅行", "海外", "地方")),
    ("裏垢", ("裏垢",)),
    ("ソロ", ("ソロ", "完ソロ")),
    ("コンビ", ("コンビ",)),
    ("MVP", ("mvp", "MVP")),
    ("新人賞", ("新人賞",)),
    ("ベストソロ", ("ベストソロ",)),
    ("ベストコンビ", ("ベストコンビ",)),
    ("経験人数", ("経験人数",)),
    ("△▽", ("△▽", "男優", "監督")),
    ("月間実績", ("月間", "月")),
    ("社会人", ("社会人",)),
    ("同棲", ("同棲",)),
    ("審査制", ("審査制",)),
    ("地方", ("地方",)),
    ("帝都", ("帝都",)),
    ("関西", ("関西", "大阪", "梅田")),
    ("名古屋", ("名古屋",)),
    ("福岡", ("福岡",)),
    ("仙台", ("仙台",)),
    ("札幌", ("札幌",)),
    ("マッチングアプリ", ("マッチングアプリ", "tinder", "Tinder", "東カレ", "アプリ")),
)
KEYWORD_CLUSTER_PREFERRED_ANCHORS = {
    "mbh": ("gureran-m", "gureran-m3"),
}

DEFAULT_PLATFORM_NODES = {
    "x": {
        "id": "x",
        "type": "platform",
        "name": "X",
        "aliases": ["Twitter"],
        "description": "Public short-post platform node.",
        "source_urls": ["https://x.com"],
        "confidence": 1.0,
    },
    "note": {
        "id": "note",
        "type": "platform",
        "name": "note",
        "aliases": [],
        "description": "Long-form publishing platform node.",
        "source_urls": ["https://note.com"],
        "confidence": 1.0,
    },
    "youtube": {
        "id": "youtube",
        "type": "platform",
        "name": "YouTube",
        "aliases": [],
        "description": "Video publishing platform node.",
        "source_urls": ["https://www.youtube.com"],
        "confidence": 1.0,
    },
    "instagram": {
        "id": "instagram",
        "type": "platform",
        "name": "Instagram",
        "aliases": [],
        "description": "Photo and short-video platform node.",
        "source_urls": ["https://www.instagram.com"],
        "confidence": 1.0,
    },
    "brain": {
        "id": "brain",
        "type": "platform",
        "name": "Brain",
        "aliases": [],
        "description": "Knowledge product marketplace node.",
        "source_urls": ["https://brain-market.com"],
        "confidence": 1.0,
    },
    "tips": {
        "id": "tips",
        "type": "platform",
        "name": "Tips",
        "aliases": [],
        "description": "Tips marketplace node.",
        "source_urls": ["https://tips.jp"],
        "confidence": 1.0,
    },
    "line": {
        "id": "line",
        "type": "platform",
        "name": "LINE",
        "aliases": [],
        "description": "Messaging and link-in-bio platform node.",
        "source_urls": ["https://line.me"],
        "confidence": 1.0,
    },
}

DOMAIN_PLATFORM_MAP = {
    "x.com": "x",
    "twitter.com": "x",
    "note.com": "note",
    "note.mu": "note",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "instagram.com": "instagram",
    "brain-market.com": "brain",
    "tips.jp": "tips",
    "line.me": "line",
    "lin.ee": "line",
}


SAMPLE_NODE_DETAILS = {
    "aoi-street": {
        "description": "Fictional field operator used to demonstrate person-to-person and person-to-location edges.",
        "source_urls": ["https://example.com/sample/aoi-profile"],
        "confidence": 0.74,
    },
    "ren-app": {
        "description": "Fictional operator centered on app-based outreach and monetized content.",
        "source_urls": ["https://example.com/sample/ren-profile"],
        "confidence": 0.76,
    },
    "noa-reviewer": {
        "description": "Fictional commentator who references and criticizes other actors in the graph.",
        "source_urls": ["https://example.com/sample/noa-profile"],
        "confidence": 0.68,
    },
    "midnight-lab": {
        "description": "Fictional community node representing a paid or semi-closed group.",
        "source_urls": ["https://example.com/sample/midnight-lab"],
        "confidence": 0.7,
    },
    "x": {
        "description": "Public short-post platform node.",
        "source_urls": ["https://x.com"],
        "confidence": 1.0,
    },
    "note": {
        "description": "Long-form publishing platform node.",
        "source_urls": ["https://note.com"],
        "confidence": 1.0,
    },
    "youtube": {
        "description": "Public video publishing platform node.",
        "source_urls": ["https://www.youtube.com"],
        "confidence": 1.0,
    },
    "instagram": {
        "description": "Public photo and short-video platform node.",
        "source_urls": ["https://www.instagram.com"],
        "confidence": 1.0,
    },
    "brain": {
        "description": "Knowledge product marketplace node.",
        "source_urls": ["https://brain-market.com"],
        "confidence": 1.0,
    },
    "tips": {
        "description": "Digital product marketplace node.",
        "source_urls": ["https://tips.jp"],
        "confidence": 1.0,
    },
    "line": {
        "description": "Messaging and link-in-bio platform node.",
        "source_urls": ["https://line.me"],
        "confidence": 1.0,
    },
    "tokyo": {
        "description": "Real public location node used for wider metro-area activity references.",
        "source_urls": ["https://www.metro.tokyo.lg.jp/"],
        "confidence": 0.98,
    },
    "nagoya": {
        "description": "Real public location node used for Chubu-area activity references.",
        "source_urls": ["https://www.city.nagoya.jp/"],
        "confidence": 0.98,
    },
    "miso": {
        "description": "Curated field/location label for public profiles that identify activity around 味噌.",
        "source_urls": ["manual://seed/miso"],
        "confidence": 0.76,
        "evidence_kind": "mixed",
        "review_notes": "Use profile-backed activity edges for account-level evidence.",
    },
    "shibuya": {
        "description": "Real public location node used as a common field example.",
        "source_urls": ["https://www.city.shibuya.tokyo.jp/"],
        "confidence": 0.98,
    },
    "shinjuku": {
        "description": "Real public location node used as another common field example.",
        "source_urls": ["https://www.city.shinjuku.lg.jp/"],
        "confidence": 0.98,
    },
    "late-night-walks": {
        "description": "Conceptual late-night field node used to test a second activity cluster.",
        "source_urls": ["manual://curated/late-night-walks"],
        "confidence": 0.69,
    },
    "matching-apps": {
        "description": "Real app-centered field node used for dating-app activity references.",
        "source_urls": ["https://www.caa.go.jp/policies/policy/consumer_policy/caution/internet/matching_app/"],
        "confidence": 0.9,
    },
    "field-guide-01": {
        "description": "Fictional content node representing a guide, note, or product.",
        "source_urls": ["https://example.com/sample/field-guide-01"],
        "confidence": 0.78,
    },
    "mei-curator": {
        "description": "Fictional curator/operator connecting meetup recaps, notes, and commentary.",
        "source_urls": ["https://example.com/sample/mei-profile"],
        "confidence": 0.72,
    },
    "sora-clips": {
        "description": "Fictional clip-focused operator used to demonstrate media distribution edges.",
        "source_urls": ["https://example.com/sample/sora-profile"],
        "confidence": 0.73,
    },
    "loop-circle": {
        "description": "Fictional community node centered on meetup recaps, clips, and cross-platform references.",
        "source_urls": ["https://example.com/sample/loop-circle"],
        "confidence": 0.71,
    },
    "clip-series-01": {
        "description": "Fictional recurring video series node used to connect communities, people, and YouTube output.",
        "source_urls": ["https://example.com/sample/clip-series-01"],
        "confidence": 0.75,
    },
    "audit-note-01": {
        "description": "Fictional analysis note node used for commentary and reference edges.",
        "source_urls": ["https://example.com/sample/audit-note-01"],
        "confidence": 0.74,
    },
}


def load_seed_entities(path: Path = SEED_FILE) -> list[dict[str, object]]:
    if not path.exists():
        return []

    entities: list[dict[str, object]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 3:
            raise ValueError(f"Invalid seed entity line: {line}")
        entity_type, entity_id, name = parts[:3]
        aliases = []
        if len(parts) >= 4 and parts[3]:
            aliases = [alias.strip() for alias in parts[3].split(",") if alias.strip()]
        scope = "unspecified"
        if len(parts) >= 5 and parts[4]:
            scope = parts[4].strip().lower()
            if scope not in SEED_SCOPES:
                raise ValueError(f"Unsupported seed scope: {scope}")
        entities.append(
            {
                "type": entity_type,
                "id": entity_id,
                "name": name,
                "aliases": aliases,
                "scope": scope,
            }
        )
    return entities


def build_growth_targets_payload(
    seed_entities: list[dict[str, object]],
    graph: GraphData | None = None,
    thin_decisions_payload: dict[str, object] | None = None,
) -> dict[str, object]:
    current_real_counts = {node_type: 0 for node_type in NODE_TYPES}
    for entity in seed_entities:
        if str(entity.get("scope", "unspecified")).strip() != "real":
            continue
        entity_type = str(entity.get("type", "")).strip()
        if entity_type in current_real_counts:
            current_real_counts[entity_type] += 1

    payload: dict[str, object] = {
        "headline": {
            "label": "Real person target",
            "current": current_real_counts["person"],
            "target": REAL_GROWTH_TARGETS["person"]["max"],
        },
        "phases": list(REAL_GROWTH_PHASES),
        "types": [
            {
                "type": node_type,
                "current": current_real_counts[node_type],
                "target_min": REAL_GROWTH_TARGETS[node_type]["min"],
                "target_max": REAL_GROWTH_TARGETS[node_type]["max"],
            }
            for node_type in ("person", "community", "content", "location", "platform")
        ],
    }
    if graph is not None:
        payload["density"] = build_graph_density_payload(
            graph,
            thin_decisions_payload=thin_decisions_payload,
        )
        payload["clusters"] = build_cluster_density_payload(
            graph,
            thin_decisions_payload=thin_decisions_payload,
        )
    return payload


def build_cluster_density_payload(
    graph: GraphData,
    thin_decisions_payload: dict[str, object] | None = None,
    *,
    top_n: int = 12,
) -> dict[str, object]:
    """キーワードクラスタごとの solid 密度を集計する。"""

    from graph_model import KEYWORD_CLUSTER_RULES, build_relation_cluster_payload

    _, _, solid_degree_by_id, _ = graph_account_degree_stats(graph)
    relevant_ids = network_relevant_person_ids(graph, thin_decisions_payload)
    node_by_id = {node.id: node for node in graph.nodes}
    cluster_payload = build_relation_cluster_payload(graph)
    keyword_mode = (cluster_payload.get("modes") or {}).get("keyword_group") or {}
    assignments = keyword_mode.get("assignments") or {}
    clusters_meta = keyword_mode.get("clusters") or {}

    # solid person-person edges inside each cluster
    solid_edges_by_cluster: dict[str, int] = defaultdict(int)
    for edge in graph.edges:
        if is_assistive_edge(edge):
            continue
        source = node_by_id.get(edge.source)
        target = node_by_id.get(edge.target)
        if not source or not target:
            continue
        if source.type != "person" or target.type != "person":
            continue
        left = assignments.get(edge.source)
        right = assignments.get(edge.target)
        if left and left == right:
            solid_edges_by_cluster[str(left)] += 1

    rows: list[dict[str, object]] = []
    for cluster_id, meta in clusters_meta.items():
        members = [
            node_id
            for node_id, assigned in assignments.items()
            if assigned == cluster_id and node_id in node_by_id and node_by_id[node_id].type == "person"
        ]
        if not members:
            continue
        relevant_members = [node_id for node_id in members if node_id in relevant_ids]
        solid_degrees = [int(solid_degree_by_id[node_id]) for node_id in members]
        mean_solid = sum(solid_degrees) / len(solid_degrees) if solid_degrees else 0.0
        rows.append(
            {
                "id": cluster_id,
                "label": str((meta or {}).get("label", cluster_id)),
                "size": len(members),
                "relevant_size": len(relevant_members),
                "solid_internal_edges": int(solid_edges_by_cluster.get(str(cluster_id), 0)),
                "mean_solid_degree": round(mean_solid, 2),
                "solid0": sum(1 for value in solid_degrees if value == 0),
            }
        )
    rows.sort(
        key=lambda item: (
            -int(item.get("solid_internal_edges", 0)),
            -int(item.get("size", 0)),
            str(item.get("label", "")),
        )
    )
    assigned_persons = sum(1 for node_id, cluster_id in assignments.items() if node_by_id.get(node_id) and node_by_id[node_id].type == "person")
    return {
        "mode": "keyword_group",
        "cluster_count": len(rows),
        "assigned_persons": assigned_persons,
        "rule_count": len(KEYWORD_CLUSTER_RULES),
        "top": rows[: max(1, top_n)],
    }


def build_graph_density_payload(
    graph: GraphData,
    thin_decisions_payload: dict[str, object] | None = None,
) -> dict[str, object]:
    """solid 関係の密度と外周ノイズ指標を集計する。"""

    _degree_by_id, follow_degree_by_id, solid_degree_by_id, assistive_degree_by_id = (
        graph_account_degree_stats(graph)
    )
    node_by_id = {node.id: node for node in graph.nodes}
    persons = [node for node in graph.nodes if node.type == "person"]
    relevant_ids = network_relevant_person_ids(graph, thin_decisions_payload)

    solid_edge_count = 0
    assistive_edge_count = 0
    person_person_solid = 0
    person_person_assistive = 0
    for edge in graph.edges:
        assistive = is_assistive_edge(edge)
        if assistive:
            assistive_edge_count += 1
        else:
            solid_edge_count += 1
        source = node_by_id.get(edge.source)
        target = node_by_id.get(edge.target)
        if not source or not target:
            continue
        if source.type == "person" and target.type == "person":
            if assistive:
                person_person_assistive += 1
            else:
                person_person_solid += 1

    total_edges = solid_edge_count + assistive_edge_count
    relevant_persons = [node for node in persons if node.id in relevant_ids]
    relevant_solid_degrees = [int(solid_degree_by_id[node.id]) for node in relevant_persons]
    relevant_solid0 = sum(1 for value in relevant_solid_degrees if value == 0)
    relevant_solid_ge3 = sum(1 for value in relevant_solid_degrees if value >= 3)
    mean_relevant_solid = (
        sum(relevant_solid_degrees) / len(relevant_solid_degrees)
        if relevant_solid_degrees
        else 0.0
    )
    excluded_count = 0
    decisions = (thin_decisions_payload or {}).get("decisions", {})
    if isinstance(decisions, dict):
        excluded_count = sum(
            1
            for decision in decisions.values()
            if isinstance(decision, dict) and str(decision.get("status", "")).strip() == "exclude"
        )

    return {
        "solid_edge_count": solid_edge_count,
        "assistive_edge_count": assistive_edge_count,
        "solid_edge_ratio": round(solid_edge_count / total_edges, 4) if total_edges else 0.0,
        "person_person_solid_edges": person_person_solid,
        "person_person_assistive_edges": person_person_assistive,
        "person_count": len(persons),
        "network_relevant_persons": len(relevant_ids),
        "excluded_thin_persons": excluded_count,
        "relevant_solid_degree_0": relevant_solid0,
        "relevant_solid_degree_ge_3": relevant_solid_ge3,
        "mean_relevant_solid_degree": round(mean_relevant_solid, 3),
        "relevant_with_follow_degree_ge_2": sum(
            1 for node in relevant_persons if int(follow_degree_by_id[node.id]) >= 2
        ),
        "relevant_bridge_only": sum(
            1
            for node in relevant_persons
            if int(solid_degree_by_id[node.id]) == 0 and int(assistive_degree_by_id[node.id]) > 0
        ),
    }


def load_source_snapshots(path: Path = SNAPSHOT_FILE) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return list(json.loads(path.read_text(encoding="utf-8")))


def save_source_snapshots(snapshots: list[dict[str, object]], path: Path = SNAPSHOT_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(snapshots, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_all_source_snapshots(
    manual_path: Path = SNAPSHOT_FILE,
    generated_path: Path = GENERATED_SNAPSHOT_FILE,
) -> list[dict[str, object]]:
    manual_snapshots = load_source_snapshots(manual_path)
    generated_snapshots = load_generated_snapshots(generated_path, GENERATED_HINT_SNAPSHOT_FILE)
    return merge_snapshots_by_account(manual_snapshots, generated_snapshots)


def load_generated_snapshots(
    generated_path: Path = GENERATED_SNAPSHOT_FILE,
    generated_hint_path: Path = GENERATED_HINT_SNAPSHOT_FILE,
) -> list[dict[str, object]]:
    return [
        *load_source_snapshots(generated_path),
        *load_source_snapshots(generated_hint_path),
    ]


def validate_confidence(value: object, field_name: str) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a number between 0.0 and 1.0") from exc
    if not 0.0 <= normalized <= 1.0:
        raise ValueError(f"{field_name} must be between 0.0 and 1.0")
    return normalized


def validate_bool(value: object, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{field_name} must be true or false")


def validate_evidence_kind(value: object, field_name: str) -> str:
    normalized = str(value or "fact").strip().lower()
    if normalized not in EVIDENCE_KINDS:
        raise ValueError(f"{field_name} must be one of: {list(EVIDENCE_KINDS)}")
    return normalized


def validate_url(url: str, field_name: str) -> str:
    normalized = str(url).strip()
    if not normalized:
        return ""
    parsed = urlparse(normalized)
    if parsed.scheme not in ALLOWED_URL_SCHEMES:
        raise ValueError(f"{field_name} must use one of: {sorted(ALLOWED_URL_SCHEMES)}")
    if parsed.scheme in {"http", "https"} and not parsed.netloc:
        raise ValueError(f"{field_name} must be an absolute URL")
    return normalized


def validate_string_list(values: object, field_name: str) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError(f"{field_name} must be a list")
    return [str(value).strip() for value in values if str(value).strip()]


def build_entity_reference_lookup(seed_entities: list[dict[str, object]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for entity in seed_entities:
        entity_id = str(entity["id"]).strip()
        name = str(entity["name"]).strip()
        lookup[entity_id.casefold()] = entity_id
        lookup[name.casefold()] = entity_id
        for alias in entity.get("aliases", []):
            alias_text = str(alias).strip()
            if alias_text:
                lookup[alias_text.casefold()] = entity_id
    return lookup


def resolve_entity_reference(reference: object, lookup: dict[str, str], field_name: str) -> str:
    normalized = str(reference).strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    resolved = lookup.get(normalized.casefold())
    if resolved is None:
        raise ValueError(f"Unknown {field_name}: {normalized}")
    return resolved


def validate_seed_entities(seed_entities: list[dict[str, object]]) -> None:
    seen_ids: set[str] = set()
    for entity in seed_entities:
        entity_type = str(entity.get("type", "")).strip()
        entity_id = str(entity.get("id", "")).strip()
        entity_name = str(entity.get("name", "")).strip()
        aliases = entity.get("aliases", [])

        if entity_type not in NODE_TYPES:
            raise ValueError(f"Unsupported seed entity type: {entity_type}")
        if not entity_id:
            raise ValueError("seed entity id is required")
        if entity_id in seen_ids:
            raise ValueError(f"Duplicate seed entity id: {entity_id}")
        if not entity_name:
            raise ValueError(f"seed entity name is required for {entity_id}")
        if aliases is not None and not isinstance(aliases, list):
            raise ValueError(f"aliases must be a list for {entity_id}")
        seen_ids.add(entity_id)


def normalize_edge_type(edge_type: object, *, description: str = "") -> str:
    normalized = str(edge_type or "").strip()
    if normalized not in LEGACY_EDGE_TYPES:
        return normalized
    if description.strip().casefold().startswith(FOLLOW_REFERENCE_PREFIX):
        return "follow"
    return "profile_mention"


def normalize_observation(observation: dict[str, object]) -> dict[str, object]:
    normalized = dict(observation)
    normalized["type"] = normalize_edge_type(
        observation.get("type", ""),
        description=str(observation.get("description", "")).strip(),
    )
    return normalized


def normalize_review_candidate(candidate: dict[str, object]) -> dict[str, object]:
    normalized = dict(candidate)
    normalized["type"] = normalize_edge_type(
        candidate.get("type", ""),
        description=str(candidate.get("evidence_text", candidate.get("description", ""))).strip(),
    )
    return normalized


def normalize_review_candidate_decision(decision: dict[str, object]) -> dict[str, object]:
    normalized = dict(decision)
    normalized["type"] = normalize_edge_type(
        decision.get("type", ""),
        description=str(decision.get("evidence_text", decision.get("description", ""))).strip(),
    )
    return normalized


def normalize_thin_candidate_decision(decision: dict[str, object]) -> dict[str, object]:
    status = str(decision.get("status", "")).strip().lower()
    if status and status not in THIN_CANDIDATE_STATUSES:
        raise ValueError(f"Unsupported thin candidate decision status: {status}")
    return {
        "node_id": str(decision.get("node_id", "")).strip(),
        "status": status,
        "note": str(decision.get("note", "")).strip(),
        "name": str(decision.get("name", "")).strip(),
        "score": int(decision.get("score", 0) or 0),
        "degree": int(decision.get("degree", 0) or 0),
        "solid_degree": int(decision.get("solid_degree", decision.get("degree", 0)) or 0),
        "assistive_degree": int(decision.get("assistive_degree", 0) or 0),
        "reasons": [
            str(reason).strip()
            for reason in decision.get("reasons", [])
            if str(reason).strip()
        ],
        "updated_at": str(decision.get("updated_at", "")).strip(),
    }


def validate_source_snapshots(
    snapshots: list[dict[str, object]],
    seed_entities: list[dict[str, object]],
) -> None:
    seed_ids = {str(entity["id"]).strip() for entity in seed_entities}
    reference_lookup = build_entity_reference_lookup(seed_entities)

    for snapshot in snapshots:
        account_id = str(snapshot.get("account_id", "")).strip()
        if account_id not in seed_ids:
            raise ValueError(f"Snapshot references unknown account_id: {account_id}")

        validate_url(str(snapshot.get("profile_url", "")).strip(), f"{account_id}.profile_url")
        validate_url(
            str(snapshot.get("pinned_post_url", "")).strip(),
            f"{account_id}.pinned_post_url",
        )
        validate_url(str(snapshot.get("icon_url", "")).strip(), f"{account_id}.icon_url")
        links = validate_string_list(snapshot.get("links", []), f"{account_id}.links")
        observations = snapshot.get("observations", [])
        if not isinstance(observations, list):
            raise ValueError(f"{account_id}.observations must be a list")

        for link in links:
            validate_url(link, f"{account_id}.links[]")

        if "link_confidence" in snapshot:
            validate_confidence(snapshot["link_confidence"], f"{account_id}.link_confidence")
        if "link_needs_review" in snapshot:
            validate_bool(snapshot["link_needs_review"], f"{account_id}.link_needs_review")
        if "link_evidence_kind" in snapshot:
            validate_evidence_kind(snapshot["link_evidence_kind"], f"{account_id}.link_evidence_kind")
        if "observation_confidence" in snapshot:
            validate_confidence(
                snapshot["observation_confidence"],
                f"{account_id}.observation_confidence",
            )
        if "needs_review" in snapshot:
            validate_bool(snapshot["needs_review"], f"{account_id}.needs_review")
        if "evidence_kind" in snapshot:
            validate_evidence_kind(snapshot["evidence_kind"], f"{account_id}.evidence_kind")
        if "summary_evidence_kind" in snapshot:
            validate_evidence_kind(
                snapshot["summary_evidence_kind"],
                f"{account_id}.summary_evidence_kind",
            )

        if not any(
            [
                str(snapshot.get("profile_text", "")).strip(),
                str(snapshot.get("pinned_post_text", "")).strip(),
                str(snapshot.get("summary", "")).strip(),
                str(snapshot.get("profile_url", "")).strip(),
                str(snapshot.get("pinned_post_url", "")).strip(),
                links,
                observations,
            ]
        ):
            raise ValueError(f"{account_id} snapshot has no usable evidence")

        for index, observation in enumerate(observations):
            if not isinstance(observation, dict):
                raise ValueError(f"{account_id}.observations[{index}] must be an object")
            description = str(observation.get("description", "")).strip()
            observation_type = normalize_edge_type(
                observation.get("type", ""),
                description=description,
            )
            if observation_type not in EDGE_TYPES:
                raise ValueError(
                    f"{account_id}.observations[{index}] has unsupported edge type: {observation_type}"
                )
            if not description:
                raise ValueError(f"{account_id}.observations[{index}] description is required")

            resolve_entity_reference(
                observation.get("target", ""),
                reference_lookup,
                f"{account_id}.observations[{index}].target",
            )
            if "source" in observation:
                resolve_entity_reference(
                    observation["source"],
                    reference_lookup,
                    f"{account_id}.observations[{index}].source",
                )

            observation_urls = validate_string_list(
                observation.get("source_urls", []),
                f"{account_id}.observations[{index}].source_urls",
            )
            for url in observation_urls:
                validate_url(url, f"{account_id}.observations[{index}].source_urls[]")
            if "confidence" in observation:
                validate_confidence(
                    observation["confidence"],
                    f"{account_id}.observations[{index}].confidence",
                )
            if "needs_review" in observation:
                validate_bool(
                    observation["needs_review"],
                    f"{account_id}.observations[{index}].needs_review",
                )
            if "evidence_kind" in observation:
                validate_evidence_kind(
                    observation["evidence_kind"],
                    f"{account_id}.observations[{index}].evidence_kind",
                )


def merge_evidence_kind(current: str, incoming: str) -> str:
    if current == incoming:
        return current
    if current == "fact":
        return incoming
    if incoming == "fact":
        return current
    return "mixed"


def snapshot_priority(snapshot: dict[str, object]) -> int:
    if snapshot.get("snapshot_origin") == "manual" or "collector" not in snapshot:
        return 100
    collector_meta = snapshot.get("collector", {})
    collector_type = collector_meta.get("type") if isinstance(collector_meta, dict) else ""
    if not collector_type and isinstance(collector_meta, dict):
        for source in collector_meta.get("sources", []):
            if isinstance(source, dict) and source.get("type") == "x_web_profile":
                return 35
    if collector_type == "x_web_profile":
        return 35
    if collector_type == "x_profile":
        return 30
    if collector_type == "public_page":
        return 20
    return 10


def merge_notes(*notes: str) -> str:
    merged: list[str] = []
    for note in notes:
        normalized = str(note).strip()
        if normalized and normalized not in merged:
            merged.append(normalized)
    return " ".join(merged)


def dedupe_observations(observations: list[dict[str, object]]) -> list[dict[str, object]]:
    unique: list[dict[str, object]] = []
    seen: set[str] = set()
    for observation in observations:
        normalized_observation = (
            normalize_observation(observation) if isinstance(observation, dict) else observation
        )
        key = json.dumps(normalized_observation, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        unique.append(normalized_observation)
    return unique


def merge_snapshots_by_account(
    manual_snapshots: list[dict[str, object]],
    generated_snapshots: list[dict[str, object]],
) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = {}
    for snapshot in manual_snapshots:
        grouped.setdefault(str(snapshot["account_id"]).strip(), []).append(
            {**copy.deepcopy(snapshot), "snapshot_origin": "manual"}
        )
    for snapshot in generated_snapshots:
        grouped.setdefault(str(snapshot["account_id"]).strip(), []).append(
            copy.deepcopy(snapshot)
        )

    merged_snapshots: list[dict[str, object]] = []
    text_fields = (
        "profile_url",
        "pinned_post_url",
        "icon_url",
        "profile_text",
        "pinned_post_text",
        "summary",
    )

    for account_id in sorted(grouped):
        ordered = sorted(grouped[account_id], key=snapshot_priority, reverse=True)
        merged = copy.deepcopy(ordered[0])
        merged["account_id"] = account_id
        merged["links"] = list(merged.get("links", []))
        merged["observations"] = list(merged.get("observations", []))
        conflicts: list[str] = []

        for snapshot in ordered[1:]:
            current_follower_count = int(merged.get("follower_count", 0) or 0)
            incoming_follower_count = int(snapshot.get("follower_count", 0) or 0)
            if current_follower_count <= 0 < incoming_follower_count:
                merged["follower_count"] = incoming_follower_count

            for field in text_fields:
                current_value = str(merged.get(field, "")).strip()
                incoming_value = str(snapshot.get(field, "")).strip()
                if not incoming_value:
                    continue
                if not current_value:
                    merged[field] = incoming_value
                    continue
                if current_value != incoming_value and snapshot_priority(merged) >= snapshot_priority(snapshot):
                    conflicts.append(field)

            merged["links"] = list(
                dict.fromkeys([*merged.get("links", []), *snapshot.get("links", [])])
            )
            merged["observations"] = dedupe_observations(
                [*merged.get("observations", []), *snapshot.get("observations", [])]
            )
            merged["needs_review"] = bool(
                merged.get("needs_review", False) or snapshot.get("needs_review", False)
            )
            merged["evidence_kind"] = merge_evidence_kind(
                str(merged.get("evidence_kind", "fact")),
                str(snapshot.get("evidence_kind", "fact")),
            )
            merged["summary_evidence_kind"] = merge_evidence_kind(
                str(merged.get("summary_evidence_kind", merged.get("evidence_kind", "fact"))),
                str(snapshot.get("summary_evidence_kind", snapshot.get("evidence_kind", "fact"))),
            )
            merged["review_notes"] = merge_notes(
                str(merged.get("review_notes", "")),
                str(snapshot.get("review_notes", "")),
            )

        if conflicts:
            merged["needs_review"] = True
            merged["review_notes"] = merge_notes(
                str(merged.get("review_notes", "")),
                "Generated snapshot differs from manual fields: "
                + ", ".join(sorted(set(conflicts)))
                + ".",
            )

        merged_snapshots.append(merged)

    return merged_snapshots


def merge_unique(items: list[str], *extra_items: str) -> list[str]:
    merged = list(items)
    for item in extra_items:
        if item and item not in merged:
            merged.append(item)
    return merged


def build_node_lookup(graph: GraphData) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for node in graph.nodes:
        lookup[node.id.casefold()] = node.id
        lookup[node.name.casefold()] = node.id
        for alias in node.aliases:
            lookup[alias.casefold()] = node.id
    return lookup


def detect_platform_id_from_url(url: str) -> str | None:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    for domain, platform_id in DOMAIN_PLATFORM_MAP.items():
        if host == domain or host.endswith(f".{domain}"):
            return platform_id
    return None


def ensure_platform_node(graph: GraphData, platform_id: str) -> None:
    if any(node.id == platform_id for node in graph.nodes):
        return
    payload = DEFAULT_PLATFORM_NODES.get(platform_id)
    if payload is None:
        raise ValueError(f"Unsupported platform id: {platform_id}")
    add_node(graph, payload)


def apply_source_snapshots(
    graph: GraphData,
    snapshots: list[dict[str, object]],
) -> GraphData:
    lookup = build_node_lookup(graph)
    nodes_by_id = {node.id: node for node in graph.nodes}

    for snapshot in snapshots:
        account_id = str(snapshot["account_id"]).strip()
        if account_id not in nodes_by_id:
            raise ValueError(f"Snapshot references unknown account_id: {account_id}")

        source_node = nodes_by_id[account_id]
        profile_url = str(snapshot.get("profile_url", "")).strip()
        pinned_post_url = str(snapshot.get("pinned_post_url", "")).strip()
        for link in snapshot.get("links", []):
            source_node.source_urls = merge_unique(source_node.source_urls, str(link).strip())
        source_node.source_urls = merge_unique(
            source_node.source_urls,
            profile_url,
            pinned_post_url,
        )
        source_node.needs_review = source_node.needs_review or bool(
            snapshot.get("needs_review", False)
        )
        icon_url = str(snapshot.get("icon_url", "")).strip()
        if icon_url and not (
            "/default_profile_" in icon_url.casefold()
            or "abs.twimg.com/sticky/default_profile_images" in icon_url.casefold()
        ):
            source_node.icon_url = icon_url
        elif (
            "/default_profile_" in str(source_node.icon_url).casefold()
            or "abs.twimg.com/sticky/default_profile_images" in str(source_node.icon_url).casefold()
        ):
            # Drop placeholder avatars so the UI can fall back cleanly.
            source_node.icon_url = ""
        follower_count = int(snapshot.get("follower_count", 0) or 0)
        if follower_count > 0:
            source_node.follower_count = follower_count
        source_node.evidence_kind = merge_evidence_kind(
            source_node.evidence_kind,
            str(snapshot.get("summary_evidence_kind", snapshot.get("evidence_kind", "fact"))),
        )
        if snapshot.get("review_notes"):
            source_node.review_notes = str(snapshot["review_notes"]).strip()

        summary = str(snapshot.get("summary", "")).strip()
        if summary and (
            not source_node.description
            or source_node.description.startswith("Seed entity placeholder")
        ):
            source_node.description = summary

        platform_links: dict[str, str] = {}
        for link in snapshot.get("links", []):
            normalized_link = str(link).strip()
            platform_id = detect_platform_id_from_url(normalized_link)
            if not platform_id:
                continue
            platform_links.setdefault(platform_id, normalized_link)

        for platform_id, normalized_link in platform_links.items():
            ensure_platform_node(graph, platform_id)
            if platform_id not in nodes_by_id:
                nodes_by_id = {node.id: node for node in graph.nodes}
                lookup = build_node_lookup(graph)
            if account_id == platform_id:
                continue
            add_edge(
                graph,
                {
                    "source": account_id,
                    "target": platform_id,
                    "type": "affiliation",
                    "description": f"Profile links point to {nodes_by_id[platform_id].name}.",
                    "source_urls": [url for url in [profile_url, normalized_link] if url],
                    "confidence": float(snapshot.get("link_confidence", 0.86)),
                    "evidence_kind": str(snapshot.get("link_evidence_kind", "fact")),
                    "needs_review": bool(snapshot.get("link_needs_review", snapshot.get("needs_review", False))),
                    "review_notes": str(
                        snapshot.get(
                            "link_review_notes",
                            snapshot.get("review_notes", ""),
                        )
                    ).strip(),
                },
            )

        for observation in snapshot.get("observations", []):
            normalized_observation = normalize_observation(observation)
            target_id = resolve_entity_reference(
                normalized_observation["target"],
                lookup,
                "observation target",
            )
            source_id = (
                resolve_entity_reference(
                    normalized_observation["source"], lookup, "observation source"
                )
                if "source" in normalized_observation
                else account_id
            )
            add_edge(
                graph,
                {
                    "source": source_id,
                    "target": target_id,
                    "type": normalized_observation["type"],
                    "description": str(normalized_observation.get("description", "")).strip(),
                    "source_urls": [
                        str(url).strip()
                        for url in normalized_observation.get("source_urls", [])
                        if str(url).strip()
                    ]
                    or [url for url in [profile_url, pinned_post_url] if url],
                    "confidence": float(
                        normalized_observation.get(
                            "confidence",
                            snapshot.get("observation_confidence", source_node.confidence),
                        )
                    ),
                    "evidence_kind": str(
                        normalized_observation.get(
                            "evidence_kind",
                            snapshot.get("evidence_kind", "fact"),
                        )
                    ),
                    "needs_review": bool(
                        normalized_observation.get(
                            "needs_review",
                            snapshot.get("needs_review", False),
                        )
                    ),
                    "review_notes": str(
                        normalized_observation.get(
                            "review_notes",
                            snapshot.get("review_notes", ""),
                        )
                    ).strip(),
                },
            )

    return graph


def build_graph_from_sources(
    seed_entities: list[dict[str, object]],
    snapshots: list[dict[str, object]] | None = None,
) -> GraphData:
    validate_seed_entities(seed_entities)
    validate_source_snapshots(snapshots or [], seed_entities)

    graph = GraphData()
    for entity in seed_entities:
        entity_id = str(entity["id"])
        detail = SAMPLE_NODE_DETAILS.get(entity_id, {})
        add_node(
            graph,
            {
                "id": entity_id,
                "type": entity["type"],
                "name": entity["name"],
                "aliases": entity.get("aliases", []),
                "description": detail.get(
                    "description",
                    "Seed entity placeholder. Replace with public-source-backed details.",
                ),
                "source_urls": detail.get("source_urls", ["manual://seed"]),
                "confidence": detail.get("confidence", 0.5),
                "evidence_kind": detail.get("evidence_kind", "fact"),
                "needs_review": detail.get("needs_review", False),
                "review_notes": detail.get("review_notes", ""),
            },
        )

    if snapshots:
        apply_source_snapshots(graph, snapshots)
    return graph


def build_sample_graph(seed_entities: list[dict[str, object]]) -> GraphData:
    return build_graph_from_sources(seed_entities, load_all_source_snapshots())


def refresh_outputs(graph: GraphData) -> None:
    save_graph(graph, NODES_JSON, EDGES_JSON)
    export_csv(graph, NODES_CSV, EDGES_CSV)
    export_networkx_metrics(graph, NETWORKX_METRICS)
    export_sqlite(graph, SQLITE_DB)


def _seed_handle_match_strings(entity: dict[str, object]) -> list[str]:
    """X プロフィール内の @handle 言及向け（entity id のハイフンはアンダースコア扱い）。"""
    variants: list[str] = []
    entity_id = str(entity.get("id", "")).strip()
    if entity_id:
        slug = entity_id.replace("-", "_")
        if X_STYLE_HANDLE_RE.fullmatch(slug):
            variants.extend([slug, f"@{slug}"])
    for raw in entity.get("aliases") or []:
        alias = str(raw).strip()
        if X_STYLE_HANDLE_RE.fullmatch(alias):
            variants.extend([alias, f"@{alias}"])
    seen: set[str] = set()
    ordered: list[str] = []
    for item in variants:
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(item)
    return ordered


def is_ambiguous_review_match_token(token: str) -> bool:
    normalized = str(token).strip()
    if not normalized:
        return True
    return bool(AMBIGUOUS_PERSON_ALIAS_RE.fullmatch(normalized))


def build_review_candidate_matchers(seed_entities: list[dict[str, object]]) -> list[dict[str, str]]:
    matchers: list[dict[str, str]] = []
    for entity in seed_entities:
        entity_id = str(entity["id"]).strip()
        entity_type = str(entity["type"]).strip()
        if entity_type == "platform":
            continue
        for raw_token in [entity.get("name", ""), *entity.get("aliases", [])]:
            token = str(raw_token).strip()
            if len(token) < 3 and not (len(token) >= 2 and CJK_TOKEN_RE.search(token)):
                continue
            if is_ambiguous_review_match_token(token):
                continue
            matchers.append(
                {
                    "target": entity_id,
                    "target_type": entity_type,
                    "matched_text": token,
                    "matched_text_lower": token.casefold(),
                }
            )
        for display in _seed_handle_match_strings(entity):
            matchers.append(
                {
                    "target": entity_id,
                    "target_type": entity_type,
                    "matched_text": display,
                    "matched_text_lower": display.casefold(),
                }
            )
    return sorted(matchers, key=lambda item: len(item["matched_text"]), reverse=True)


def review_candidate_type(source_type: str, target_type: str, text: str, basis: str) -> str:
    lowered = text.casefold()
    if source_type == "content" and target_type == "location":
        return "profile_mention"
    if target_type == "location":
        return "activity"
    if any(
        keyword in lowered
        for keyword in (
            "influence",
            "inspired by",
            "inspired",
            "learned from",
            "credit",
            "credits",
            "参考",
            "影響",
            "師匠",
            "弟子",
            "の元",
        )
    ):
        return "influence"
    if any(keyword in lowered for keyword in ("critic", "critique", "against", "oppose", "批判")):
        return "criticism"
    if any(
        keyword in lowered
        for keyword in (
            "collab",
            "collaboration",
            "joint",
            "stream with",
            "session with",
            "with ",
            "hosted with",
            "共演",
            "対談",
        )
    ):
        return "collaboration"
    if target_type == "community" and basis == "profile_text":
        if any(
            keyword in lowered
            for keyword in (
                "organizer",
                "organiser",
                "mentor",
                "member",
                "inside",
                "host",
                "join",
                "joined",
                "run by",
            )
        ):
            return "affiliation"
    if target_type == "content":
        if any(
            keyword in lowered
            for keyword in (
                "product",
                "guide",
                "series",
                "funnel",
                "sale",
                "buy",
                "purchase",
                "link readers to",
                "links to",
                "archive",
                "教材",
                "販売",
            )
        ):
            return "monetization"
    return "profile_mention"


def review_candidate_group_id(source_id: str, target_id: str, candidate_type: str) -> str:
    return f"{source_id}__{target_id}__{candidate_type}"


def review_candidate_basis_id(source_id: str, target_id: str, candidate_type: str, basis: str) -> str:
    return f"{review_candidate_group_id(source_id, target_id, candidate_type)}__{basis}"


def reviewed_candidate_groups(
    decisions_payload: dict[str, object] | None,
) -> set[tuple[str, str, str]]:
    groups: set[tuple[str, str, str]] = set()
    decisions = (decisions_payload or {}).get("decisions", {})
    if not isinstance(decisions, dict):
        return groups
    for candidate_id, decision in decisions.items():
        if not isinstance(decision, dict):
            continue
        if str(decision.get("status", "")).strip() not in {"dismissed", "approved"}:
            continue
        source_id = str(decision.get("source", "")).strip()
        target_id = str(decision.get("target", "")).strip()
        candidate_type = normalize_edge_type(
            decision.get("type", ""),
            description=str(decision.get("evidence_text", decision.get("description", ""))).strip(),
        )
        if source_id and target_id and candidate_type:
            groups.add((source_id, target_id, candidate_type))
            continue
        parts = str(candidate_id).split("__")
        if len(parts) >= 3:
            groups.add(
                (
                    parts[0].strip(),
                    parts[1].strip(),
                    normalize_edge_type(parts[2].strip()),
                )
            )
    return groups


def generate_review_candidates(
    seed_entities: list[dict[str, object]],
    generated_snapshots: list[dict[str, object]],
    graph: GraphData,
    decisions_payload: dict[str, object] | None = None,
) -> dict[str, object]:
    entity_lookup = {str(entity["id"]).strip(): entity for entity in seed_entities}
    existing_relations = {(edge.source, edge.target, edge.type) for edge in graph.edges}
    reviewed_ids = {
        candidate_id
        for candidate_id, decision in (decisions_payload or {}).get("decisions", {}).items()
        if isinstance(decision, dict) and str(decision.get("status", "")).strip() in {"dismissed", "approved"}
    }
    reviewed_groups = reviewed_candidate_groups(decisions_payload)
    matchers = build_review_candidate_matchers(seed_entities)
    aggregated_candidates: dict[tuple[str, str, str], dict[str, object]] = {}

    for snapshot in generated_snapshots:
        source_id = str(snapshot.get("account_id", "")).strip()
        if not source_id or source_id not in entity_lookup:
            continue
        source_urls = [
            str(url).strip()
            for url in [
                snapshot.get("profile_url", ""),
                snapshot.get("pinned_post_url", ""),
                *snapshot.get("links", []),
            ]
            if str(url).strip()
        ]

        for basis in REVIEW_CANDIDATE_TEXT_FIELDS:
            raw_text = str(snapshot.get(basis, "")).strip()
            lowered_text = raw_text.casefold()
            if not lowered_text:
                continue

            for matcher in matchers:
                target_id = matcher["target"]
                if target_id == source_id:
                    continue
                if matcher["matched_text_lower"] not in lowered_text:
                    continue

                source_type = str(entity_lookup[source_id].get("type", "")).strip()
                candidate_type = review_candidate_type(
                    source_type,
                    matcher["target_type"],
                    raw_text,
                    basis,
                )
                candidate_group = (source_id, target_id, candidate_type)
                candidate_id = review_candidate_group_id(source_id, target_id, candidate_type)
                basis_candidate_id = review_candidate_basis_id(source_id, target_id, candidate_type, basis)
                if (source_id, target_id, candidate_type) in existing_relations:
                    continue
                if candidate_group in reviewed_groups:
                    continue
                if candidate_id in reviewed_ids or basis_candidate_id in reviewed_ids:
                    continue
                candidate = aggregated_candidates.get(candidate_group)
                if candidate is None:
                    aggregated_candidates[candidate_group] = {
                        "id": candidate_id,
                        "source": source_id,
                        "target": target_id,
                        "type": candidate_type,
                        "basis": basis,
                        "bases": [basis],
                        "matched_text": matcher["matched_text"],
                        "matched_texts": [matcher["matched_text"]],
                        "evidence_text": raw_text,
                        "source_urls": list(dict.fromkeys(source_urls)),
                        "confidence": REVIEW_CANDIDATE_BASE_CONFIDENCE[basis],
                        "needs_review": True,
                    }
                    continue

                if basis not in candidate["bases"]:
                    candidate["bases"].append(basis)
                if matcher["matched_text"] not in candidate["matched_texts"]:
                    candidate["matched_texts"].append(matcher["matched_text"])
                candidate["source_urls"] = list(
                    dict.fromkeys([*candidate.get("source_urls", []), *source_urls])
                )
                basis_confidence = REVIEW_CANDIDATE_BASE_CONFIDENCE[basis]
                if basis_confidence > float(candidate.get("confidence", 0.0)):
                    candidate["confidence"] = basis_confidence
                    candidate["basis"] = basis
                    candidate["matched_text"] = matcher["matched_text"]
                    candidate["evidence_text"] = raw_text

    candidates: list[dict[str, object]] = []
    for candidate in aggregated_candidates.values():
        bases = [str(item).strip() for item in candidate.get("bases", []) if str(item).strip()]
        matched_texts = [
            str(item).strip() for item in candidate.get("matched_texts", []) if str(item).strip()
        ]
        basis_label = ", ".join(bases) or str(candidate.get("basis", "")).strip()
        match_label = ", ".join(matched_texts) or str(candidate.get("matched_text", "")).strip()
        candidate["basis"] = basis_label
        candidate["review_notes"] = (
            f"Generated review candidate consolidated from {basis_label} via mention match "
            f"'{match_label}'. This is review-only and not part of the canonical graph."
        )
        candidates.append(candidate)

    candidates.sort(key=lambda item: (item["source"], item["type"], item["target"], item["basis"]))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "candidates": candidates,
    }


def materialize_inferred_social_edges(
    graph: GraphData,
    seed_entities: list[dict[str, object]],
    generated_snapshots: list[dict[str, object]],
    decisions_payload: dict[str, object] | None = None,
    *,
    edge_types: frozenset[str] | None = None,
) -> int:
    """生成スナップショットの文本から推定したソーシャル候補を既定でグラフに載せる。

    follow エッジはコレクタのフォロー一覧観測（既定で収集）由来。
    """
    chosen = edge_types or DEFAULT_MATERIALIZED_REVIEW_EDGE_TYPES
    payload = generate_review_candidates(
        seed_entities,
        generated_snapshots,
        graph,
        decisions_payload=decisions_payload,
    )
    added = 0
    for cand in payload.get("candidates", []):
        if cand.get("type") not in chosen:
            continue
        matched = str(cand.get("matched_text", "")).strip()
        ctype = str(cand.get("type", "")).strip()
        display_match = matched if matched.startswith("@") else (f"@{matched}" if matched else "?")
        if ctype == "profile_mention":
            description = (
                f"公開プロフィール・概要・固定ポストの文本から {display_match} への言及として推定（自動）。"
            )
        elif ctype == "activity":
            description = (
                f"公開プロフィール等の文本に活動地域・フィールドとして「{matched}」の言及があるとして推定（自動）。"
            )
        elif ctype == "collaboration":
            description = (
                f"公開文本から共演・協業・合同表現（一致語: {matched}）に基づく交流として推定（自動）。"
            )
        elif ctype == "influence":
            description = (
                f"公開文本から師匠・参考・影響表明（一致語: {matched}）に基づく関係として推定（自動）。"
            )
        else:
            description = (
                f"公開文本から {ctype} 関係（一致: {matched}）として推定（自動）。"
            )
        basis = str(cand.get("basis", "")).strip() or "generated_text"
        match_label = str(cand.get("matched_text", "")).strip() or matched or "?"
        review_notes = (
            f"Materialized generated candidate from {basis} via mention match "
            f"'{match_label}'. Needs manual confirmation before treating as fact."
        )
        try:
            add_edge(
                graph,
                {
                    "source": str(cand["source"]).strip(),
                    "target": str(cand["target"]).strip(),
                    "type": str(cand["type"]).strip(),
                    "description": description,
                    "source_urls": [
                        str(url).strip()
                        for url in (cand.get("source_urls") or [])
                        if str(url).strip()
                    ],
                    "confidence": float(cand.get("confidence", REVIEW_CANDIDATE_BASE_CONFIDENCE["profile_text"])),
                    "evidence_kind": "interpretation",
                    "needs_review": True,
                    "review_notes": review_notes,
                },
            )
            added += 1
        except ValueError as exc:
            if str(exc).startswith("Duplicate edge"):
                continue
            raise
    return added


def infer_keyword_cluster_edges(graph: GraphData) -> int:
    """キーワードクラスタ内の弱い補助エッジを追加する。

    まず共通隣接ノードを持つ人物ペアを接続し、さらに同じ明示キーワードを持つ
    人物をクラスタ内ハブへ少数だけ接続する。全結合にはしない。
    """
    from graph_model import KEYWORD_CLUSTER_RULES

    person_nodes = [node for node in graph.nodes if node.type == "person"]
    node_text_map: dict[str, str] = {}
    for node in person_nodes:
        node_text_map[node.id] = " ".join(
            [node.id, node.name, node.description, *node.aliases]
        ).casefold()

    existing_pairs: set[tuple[str, str]] = set()
    for edge in graph.edges:
        if edge.source in node_text_map and edge.target in node_text_map:
            existing_pairs.add((edge.source, edge.target))
            existing_pairs.add((edge.target, edge.source))

    adjacency: dict[str, set[str]] = {}
    for node_id in node_text_map:
        adjacency[node_id] = set()
    for pair in existing_pairs:
        adjacency[pair[0]].add(pair[1])
    node_by_id = {node.id: node for node in person_nodes}

    cluster_members: dict[str, list[str]] = {}
    for rule in KEYWORD_CLUSTER_RULES:
        rule_id = str(rule["id"])
        member_ids: list[str] = []
        for node_id, text in node_text_map.items():
            for pattern in rule["patterns"]:
                if str(pattern).casefold() in text:
                    member_ids.append(node_id)
                    break
        if len(member_ids) >= 3:
            cluster_members[rule_id] = member_ids

    added = 0
    for rule_id, member_ids in cluster_members.items():
        rule_label = rule_id
        for rule in KEYWORD_CLUSTER_RULES:
            if str(rule["id"]) == rule_id:
                rule_label = str(rule.get("label", rule_id))
                break
        for i in range(len(member_ids)):
            for j in range(i + 1, len(member_ids)):
                source_id = member_ids[i]
                target_id = member_ids[j]
                pair = (source_id, target_id)
                if pair in existing_pairs:
                    continue
                shared = adjacency[source_id] & adjacency[target_id]
                if not shared:
                    continue
                try:
                    add_edge(
                        graph,
                        {
                            "source": source_id,
                            "target": target_id,
                            "type": "affiliation",
                            "description": (
                                f"キーワードクラスタ「{rule_label}」に属し、共通のつながりが"
                                f" {len(shared)} 件あるため同クラスタ関係として推定（自動）。"
                            ),
                            "confidence": 0.32,
                            "evidence_kind": "interpretation",
                            "needs_review": True,
                            "review_notes": (
                                f"Keyword cluster '{rule_label}' auto-edge. "
                                f"Shared neighbors: {len(shared)}."
                            ),
                        },
                    )
                    added += 1
                    existing_pairs.add((source_id, target_id))
                    existing_pairs.add((target_id, source_id))
                    adjacency[source_id].add(target_id)
                    adjacency[target_id].add(source_id)
                except ValueError:
                    continue
        sorted_members = sorted(
            member_ids,
            key=lambda node_id: (
                -len(adjacency[node_id]),
                node_by_id[node_id].name,
                node_id,
            ),
        )
        preferred_anchor_ids = [
            node_id
            for node_id in KEYWORD_CLUSTER_PREFERRED_ANCHORS.get(rule_id, ())
            if node_id in member_ids
        ]
        anchor_limit = min(5, max(2, len(sorted_members) // 8))
        anchor_ids = [
            *preferred_anchor_ids,
            *[node_id for node_id in sorted_members if node_id not in preferred_anchor_ids],
        ][:anchor_limit]
        bridge_count_by_node: defaultdict[str, int] = defaultdict(int)
        cluster_bridge_limit = min(36, max(6, len(sorted_members)))
        cluster_bridge_added = 0
        for source_id in sorted_members:
            if cluster_bridge_added >= cluster_bridge_limit:
                break
            def anchor_priority(node_id: str) -> int:
                if node_id in preferred_anchor_ids:
                    return preferred_anchor_ids.index(node_id)
                return len(preferred_anchor_ids)

            candidates = sorted(
                (
                    target_id
                    for target_id in anchor_ids
                    if target_id != source_id and (source_id, target_id) not in existing_pairs
                ),
                key=lambda node_id: (
                    anchor_priority(node_id),
                    bridge_count_by_node[node_id],
                    -len(adjacency[node_id]),
                    node_by_id[node_id].name,
                    node_id,
                ),
            )
            for target_id in candidates:
                target_limit = 18 if target_id in preferred_anchor_ids else 6
                if bridge_count_by_node[source_id] >= 1 or bridge_count_by_node[target_id] >= target_limit:
                    continue
                try:
                    add_edge(
                        graph,
                        {
                            "source": source_id,
                            "target": target_id,
                            "type": "affiliation",
                            "description": (
                                f"キーワードクラスタ「{rule_label}」に同時所属するため、"
                                "クラスタ内の近い関係として補助接続（自動）。"
                            ),
                            "confidence": 0.27,
                            "evidence_kind": "interpretation",
                            "needs_review": True,
                            "review_notes": (
                                f"Keyword cluster '{rule_label}' bridge auto-edge. "
                                "Added with per-node caps to avoid a full clique."
                            ),
                        },
                    )
                    added += 1
                    cluster_bridge_added += 1
                    bridge_count_by_node[source_id] += 1
                    bridge_count_by_node[target_id] += 1
                    existing_pairs.add((source_id, target_id))
                    existing_pairs.add((target_id, source_id))
                    adjacency[source_id].add(target_id)
                    adjacency[target_id].add(source_id)
                    if bridge_count_by_node[source_id] >= 1 or cluster_bridge_added >= cluster_bridge_limit:
                        break
                except ValueError:
                    continue
    return added


def infer_shared_context_edges(graph: GraphData) -> int:
    """同じ中規模コンテキストを共有する人物同士に補助 affiliation edge を追加する。

    solid 関係の可読性を優先し、広い文脈・過多な補助線は抑える。
    """

    node_by_id = {node.id: node for node in graph.nodes}
    context_members: defaultdict[str, set[str]] = defaultdict(set)
    existing_pairs: set[tuple[str, str]] = set()
    adjacency: defaultdict[str, set[str]] = defaultdict(set)
    broad_context_ids = {
        "tokyo",
        "matching-apps",
        "kanto",
        "kansai",
        "x",
        "line",
        "note",
        "youtube",
        "instagram",
        "brain",
        "tips",
    }

    for edge in graph.edges:
        source = node_by_id.get(edge.source)
        target = node_by_id.get(edge.target)
        if not source or not target:
            continue
        if source.type == "person" and target.type == "person":
            existing_pairs.add((source.id, target.id))
            existing_pairs.add((target.id, source.id))
            adjacency[source.id].add(target.id)
            adjacency[target.id].add(source.id)
        if source.type == "person" and target.type in {"community", "location", "content"}:
            context_members[target.id].add(source.id)
        if target.type == "person" and source.type in {"community", "location", "content"}:
            context_members[source.id].add(target.id)

    added = 0
    for context_id, member_set in sorted(
        context_members.items(),
        key=lambda item: (node_by_id[item[0]].type, node_by_id[item[0]].name),
    ):
        context_node = node_by_id.get(context_id)
        if not context_node or context_id in broad_context_ids:
            continue
        members = sorted(
            member_set,
            key=lambda node_id: (-len(adjacency[node_id]), node_by_id[node_id].name, node_id),
        )
        if len(members) < 3 or len(members) > 36:
            continue
        anchor_ids = members[: min(5, max(2, len(members) // 5))]
        bridge_count_by_node: defaultdict[str, int] = defaultdict(int)
        context_limit = min(36, max(3, len(members) * 2))
        context_added = 0
        for source_id in members:
            if context_added >= context_limit:
                break
            candidates = sorted(
                (
                    target_id
                    for target_id in anchor_ids
                    if target_id != source_id and (source_id, target_id) not in existing_pairs
                ),
                key=lambda node_id: (
                    bridge_count_by_node[node_id],
                    -len(adjacency[node_id]),
                    node_by_id[node_id].name,
                    node_id,
                ),
            )
            for target_id in candidates:
                if bridge_count_by_node[source_id] >= 2 or bridge_count_by_node[target_id] >= 6:
                    continue
                try:
                    add_edge(
                        graph,
                        {
                            "source": source_id,
                            "target": target_id,
                            "type": "affiliation",
                            "description": (
                                f"共通コンテキスト「{context_node.name}」につながるため、"
                                "近い関係として補助接続（自動）。"
                            ),
                            "confidence": 0.25,
                            "evidence_kind": "interpretation",
                            "needs_review": True,
                            "review_notes": (
                                f"Shared context '{context_id}' bridge auto-edge. "
                                "Broad generic contexts are excluded and per-node caps are applied."
                            ),
                        },
                    )
                    added += 1
                    context_added += 1
                    bridge_count_by_node[source_id] += 1
                    bridge_count_by_node[target_id] += 1
                    existing_pairs.add((source_id, target_id))
                    existing_pairs.add((target_id, source_id))
                    adjacency[source_id].add(target_id)
                    adjacency[target_id].add(source_id)
                    if bridge_count_by_node[source_id] >= 2 or context_added >= context_limit:
                        break
                except ValueError:
                    continue
    return added


def infer_shared_neighbor_edges(graph: GraphData) -> int:
    """複数の意味ある隣接ノードを共有する人物ペアを補助接続する。"""

    node_by_id = {node.id: node for node in graph.nodes}
    broad_context_ids = {
        "tokyo",
        "matching-apps",
        "kanto",
        "kansai",
        "x",
        "line",
        "note",
        "youtube",
        "instagram",
        "brain",
        "tips",
    }
    existing_pairs: set[tuple[str, str]] = set()
    context_to_people: defaultdict[str, set[str]] = defaultdict(set)

    for edge in graph.edges:
        source = node_by_id.get(edge.source)
        target = node_by_id.get(edge.target)
        if not source or not target:
            continue
        if source.type == "person" and target.type == "person":
            existing_pairs.add(tuple(sorted((source.id, target.id))))
        if source.type == "person" and target.id not in broad_context_ids and target.type in {
            "person",
            "community",
            "location",
            "content",
        }:
            context_to_people[target.id].add(source.id)
        if target.type == "person" and source.id not in broad_context_ids and source.type in {
            "person",
            "community",
            "location",
            "content",
        }:
            context_to_people[source.id].add(target.id)

    pair_contexts: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
    for context_id, members in context_to_people.items():
        if len(members) < 2 or len(members) > 60:
            continue
        for left_id, right_id in combinations(sorted(members), 2):
            pair = tuple(sorted((left_id, right_id)))
            if pair in existing_pairs:
                continue
            pair_contexts[pair].add(context_id)

    added = 0
    added_by_node: defaultdict[str, int] = defaultdict(int)
    ranked_pairs = sorted(
        (
            (pair, contexts)
            for pair, contexts in pair_contexts.items()
            if len(contexts) >= 3
        ),
        key=lambda item: (
            -len(item[1]),
            node_by_id[item[0][0]].name,
            node_by_id[item[0][1]].name,
        ),
    )
    for (source_id, target_id), contexts in ranked_pairs:
        if added >= 400:
            break
        if added_by_node[source_id] >= 6 or added_by_node[target_id] >= 6:
            continue
        context_names = [
            node_by_id[context_id].name
            for context_id in sorted(contexts)
            if context_id in node_by_id
        ]
        try:
            add_edge(
                graph,
                {
                    "source": source_id,
                    "target": target_id,
                    "type": "affiliation",
                    "description": (
                        "複数の共通隣接ノードを持つため、近い関係として補助接続（自動）。"
                    ),
                    "confidence": 0.24,
                    "evidence_kind": "interpretation",
                    "needs_review": True,
                    "review_notes": (
                        "Shared-neighbor bridge auto-edge. "
                        f"Shared contexts: {', '.join(context_names[:8])}."
                    ),
                },
            )
            added += 1
            added_by_node[source_id] += 1
            added_by_node[target_id] += 1
            existing_pairs.add(tuple(sorted((source_id, target_id))))
        except ValueError:
            continue
    return added


def infer_profile_bridge_edges(graph: GraphData) -> int:
    """プロフィール特徴語が近い低 solid 次数の人物へ、最低限の補助エッジを補う。

    solid 関係の密度を優先するため、スコア閾値・本数上限を抑え、
    既に solid 接続が十分なノードには補助線を増やさない。
    """

    from graph_model import KEYWORD_CLUSTER_RULES

    node_by_id = {node.id: node for node in graph.nodes}
    person_nodes = [node for node in graph.nodes if node.type == "person"]
    existing_pairs: set[tuple[str, str]] = set()
    person_degree: defaultdict[str, int] = defaultdict(int)
    solid_person_degree: defaultdict[str, int] = defaultdict(int)
    for edge in graph.edges:
        source = node_by_id.get(edge.source)
        target = node_by_id.get(edge.target)
        if not source or not target:
            continue
        if source.type == "person" and target.type == "person":
            pair = tuple(sorted((source.id, target.id)))
            existing_pairs.add(pair)
            person_degree[source.id] += 1
            person_degree[target.id] += 1
            if not is_assistive_edge(edge):
                solid_person_degree[source.id] += 1
                solid_person_degree[target.id] += 1

    node_tags: dict[str, set[str]] = {}
    tag_members: defaultdict[str, set[str]] = defaultdict(set)
    for node in person_nodes:
        text = " ".join([node.id, node.name, node.description, *node.aliases])
        lowered_text = text.casefold()
        # 区切り文字を除いたハンドル正規化テキスト。ローマ字ハンドルの
        # "suto_nan" / "suto-nan" などを "sutonan" 系パターンで拾えるようにする。
        compact_text = lowered_text.replace("_", "").replace("-", "").replace(" ", "")

        def _text_has(pattern: str) -> bool:
            folded = str(pattern).casefold()
            if folded in lowered_text:
                return True
            compact_pattern = folded.replace("_", "").replace("-", "").replace(" ", "")
            return len(compact_pattern) >= 5 and compact_pattern in compact_text

        tags: set[str] = set()
        for rule in KEYWORD_CLUSTER_RULES:
            label = str(rule.get("label", rule.get("id", ""))).strip()
            if not label:
                continue
            for pattern in rule.get("patterns", ()):
                if _text_has(pattern):
                    tags.add(label)
                    break
        for label, patterns in PROFILE_BRIDGE_PATTERNS:
            for pattern in patterns:
                if _text_has(pattern):
                    tags.add(label)
                    break
        if tags:
            node_tags[node.id] = tags
            for tag in tags:
                tag_members[tag].add(node.id)

    tag_weight: dict[str, float] = {}
    for tag, members in tag_members.items():
        count = len(members)
        if count < 2:
            continue
        if count <= 8:
            tag_weight[tag] = 3.0
        elif count <= 24:
            tag_weight[tag] = 2.4
        elif count <= 60:
            tag_weight[tag] = 1.6
        elif count <= 110:
            tag_weight[tag] = 1.0
        elif count <= 180:
            tag_weight[tag] = 0.55
        else:
            tag_weight[tag] = 0.35

    candidates_by_node: defaultdict[str, list[tuple[float, str, tuple[str, ...]]]] = defaultdict(list)
    for left, right in combinations(sorted(node_tags), 2):
        pair = tuple(sorted((left, right)))
        if pair in existing_pairs:
            continue
        shared_tags = tuple(sorted(tag for tag in (node_tags[left] & node_tags[right]) if tag in tag_weight))
        if not shared_tags:
            continue
        score = sum(tag_weight[tag] for tag in shared_tags)
        if len(shared_tags) >= 2:
            score += 0.7
        left_followers = int(node_by_id[left].follower_count or 0)
        right_followers = int(node_by_id[right].follower_count or 0)
        high_follower_pair = max(left_followers, right_followers) >= 1000
        sparse_high_follower = (
            (left_followers >= 1000 and solid_person_degree[left] <= 1)
            or (right_followers >= 1000 and solid_person_degree[right] <= 1)
        )
        # 単一の弱い汎用タグだけで大量の bridge が生えないよう閾値を上げる。
        # 高フォロワー孤立側は少しだけ緩め、最低限の可視接続を確保する。
        min_score = 1.35 if sparse_high_follower else (1.6 if high_follower_pair else 2.8)
        single_tag_floor = 2.8 if sparse_high_follower else (3.2 if high_follower_pair else 3.6)
        if len(shared_tags) < 2 and score < single_tag_floor:
            continue
        if score < min_score:
            continue
        candidates_by_node[left].append((score, right, shared_tags))
        candidates_by_node[right].append((score, left, shared_tags))

    added = 0
    for source in sorted(
        person_nodes,
        key=lambda node: (
            solid_person_degree[node.id],
            person_degree[node.id],
            -(node.follower_count or 0),
            node.name,
            node.id,
        ),
    ):
        source_id = source.id
        follower_count = int(source.follower_count or 0)
        # solid 次数を基準に「最低限の可視性」だけ補う。既に solid で繋がる人は増やさない。
        # 高フォロワー孤立は solid が薄くても補助線を多めに引き、地図上の孤立を防ぐ。
        if follower_count >= 10000:
            target_solid_degree = 5
        elif follower_count >= 5000:
            target_solid_degree = 4
        elif follower_count >= 1000:
            target_solid_degree = 4
        else:
            target_solid_degree = 2 if node_tags.get(source_id) else 1
        remaining = max(0, target_solid_degree - solid_person_degree[source_id])
        if follower_count >= 1000 and solid_person_degree[source_id] <= 1:
            remaining = max(remaining, 3)
        if remaining <= 0:
            continue
        ranked_candidates = sorted(
            candidates_by_node.get(source_id, []),
            key=lambda item: (
                -item[0],
                solid_person_degree[item[1]],
                person_degree[item[1]],
                -int(node_by_id[item[1]].follower_count or 0),
                node_by_id[item[1]].name,
            ),
        )
        per_node_added = 0
        max_per_node = 3 if follower_count >= 1000 and solid_person_degree[source_id] <= 1 else 2
        for score, target_id, shared_tags in ranked_candidates:
            if added >= 1100 or per_node_added >= min(remaining, max_per_node):
                break
            pair = tuple(sorted((source_id, target_id)))
            if pair in existing_pairs:
                continue
            if person_degree[target_id] >= 16 and score < 4.5:
                continue
            if solid_person_degree[target_id] >= 8 and score < 4.0:
                continue
            tag_label = "、".join(shared_tags[:5])
            try:
                add_edge(
                    graph,
                    {
                        "source": source_id,
                        "target": target_id,
                        "type": "affiliation",
                        "description": (
                            f"プロフィール特徴語（{tag_label}）が重なるため、"
                            "近い人物候補として補助接続（自動）。"
                        ),
                        "confidence": 0.23,
                        "evidence_kind": "interpretation",
                        "needs_review": True,
                        "review_notes": (
                            "Profile bridge auto-edge for low-degree node coverage. "
                            f"Shared profile tags: {', '.join(shared_tags)}. Score: {score:.2f}."
                        ),
                    },
                )
                added += 1
                per_node_added += 1
                person_degree[source_id] += 1
                person_degree[target_id] += 1
                existing_pairs.add(pair)
            except ValueError:
                continue
    return added


def save_review_candidates(payload: dict[str, object], output_path: Path = REVIEW_CANDIDATES_JSON) -> None:
    normalized_payload = {
        "generated_at": payload.get("generated_at"),
        "candidates": payload.get("candidates", []),
    }
    normalized_payload["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(normalized_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_review_candidate_decisions(
    path: Path = REVIEW_CANDIDATE_DECISIONS_JSON,
) -> dict[str, object]:
    if not path.exists():
        return {"updated_at": "", "decisions": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("review_candidate_decisions.json must contain an object")
    decisions = payload.get("decisions", {})
    if not isinstance(decisions, dict):
        raise ValueError("review_candidate_decisions.json decisions must be an object")
    return {
        "updated_at": str(payload.get("updated_at", "")).strip(),
        "decisions": {
            candidate_id: normalize_review_candidate_decision(decision)
            for candidate_id, decision in decisions.items()
            if isinstance(decision, dict)
        },
    }


def save_review_candidate_decisions(
    payload: dict[str, object],
    path: Path = REVIEW_CANDIDATE_DECISIONS_JSON,
) -> None:
    normalized_payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "decisions": payload.get("decisions", {}),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(normalized_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def refresh_review_candidates(
    seed_entities: list[dict[str, object]],
    generated_snapshots: list[dict[str, object]],
    graph: GraphData,
    decisions_payload: dict[str, object] | None = None,
) -> dict[str, object]:
    payload = generate_review_candidates(
        seed_entities,
        generated_snapshots,
        graph,
        decisions_payload=decisions_payload,
    )
    save_review_candidates(payload, REVIEW_CANDIDATES_JSON)
    return payload


def load_review_candidates(path: Path = REVIEW_CANDIDATES_JSON) -> dict[str, object]:
    if not path.exists():
        return {"generated_at": "", "candidates": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("review_candidates.json must contain an object")
    candidates = payload.get("candidates", [])
    if not isinstance(candidates, list):
        raise ValueError("review_candidates.json candidates must be a list")
    return {
        "generated_at": str(payload.get("generated_at", "")).strip(),
        "candidates": [
            normalize_review_candidate(candidate)
            for candidate in candidates
            if isinstance(candidate, dict)
        ],
    }


def get_review_candidate(payload: dict[str, object], candidate_id: str) -> dict[str, object]:
    normalized_id = candidate_id.strip()
    for candidate in payload.get("candidates", []):
        if isinstance(candidate, dict) and str(candidate.get("id", "")).strip() == normalized_id:
            return candidate
    raise ValueError(f"Unknown review candidate id: {candidate_id}")


def candidate_to_observation(candidate: dict[str, object], approval_note: str = "") -> dict[str, object]:
    normalized_candidate = normalize_review_candidate(candidate)
    basis = str(candidate.get("basis", "")).strip() or "generated_text"
    matched_text = str(candidate.get("matched_text", "")).strip()
    description = (
        f"Approved review candidate from {basis} mentioning {matched_text}."
        if matched_text
        else f"Approved review candidate from {basis}."
    )
    review_notes = (
        f"Approved from review candidate {candidate.get('id', '')}. "
        f"Original evidence: {str(candidate.get('evidence_text', '')).strip()}"
    ).strip()
    approval_note = approval_note.strip()
    if approval_note:
        review_notes += f" Approval note: {approval_note}"
    return {
        "target": str(normalized_candidate["target"]).strip(),
        "type": str(normalized_candidate["type"]).strip(),
        "description": description,
        "source_urls": [
            str(url).strip() for url in normalized_candidate.get("source_urls", []) if str(url).strip()
        ],
        "confidence": float(candidate.get("confidence", 0.4)),
        "evidence_kind": "interpretation",
        "needs_review": False,
        "review_notes": review_notes,
    }


def ensure_manual_snapshot(
    manual_snapshots: list[dict[str, object]],
    account_id: str,
    reference_snapshot: dict[str, object] | None = None,
) -> dict[str, object]:
    for snapshot in manual_snapshots:
        if str(snapshot.get("account_id", "")).strip() == account_id:
            snapshot.setdefault("observations", [])
            return snapshot

    snapshot = {
        "account_id": account_id,
        "profile_url": "",
        "pinned_post_url": "",
        "icon_url": "",
        "profile_text": "",
        "pinned_post_text": "",
        "links": [],
        "summary": "",
        "observations": [],
        "snapshot_origin": "manual",
    }
    if reference_snapshot:
        for field in ("profile_url", "pinned_post_url", "icon_url", "profile_text", "pinned_post_text", "summary"):
            value = str(reference_snapshot.get(field, "")).strip()
            if value:
                snapshot[field] = value
        snapshot["links"] = [
            str(url).strip() for url in reference_snapshot.get("links", []) if str(url).strip()
        ]
    manual_snapshots.append(snapshot)
    return snapshot


def approve_review_candidate(
    manual_snapshots: list[dict[str, object]],
    candidate: dict[str, object],
    *,
    approval_note: str = "",
    reference_snapshot: dict[str, object] | None = None,
) -> dict[str, object]:
    source_id = str(candidate.get("source", "")).strip()
    if not source_id:
        raise ValueError("review candidate source is required")
    snapshot = ensure_manual_snapshot(manual_snapshots, source_id, reference_snapshot=reference_snapshot)
    observation = candidate_to_observation(candidate, approval_note=approval_note)
    before_count = len(snapshot["observations"])
    snapshot["observations"] = dedupe_observations([*snapshot["observations"], observation])
    if len(snapshot["observations"]) == before_count:
        raise ValueError(f"Review candidate already approved for {source_id}: {candidate.get('id', '')}")
    return observation


def set_review_candidate_decision(
    decisions_payload: dict[str, object],
    candidate: dict[str, object],
    *,
    status: str,
    note: str = "",
) -> dict[str, object]:
    normalized_candidate = normalize_review_candidate(candidate)
    normalized_status = status.strip().lower()
    if normalized_status not in {"approved", "dismissed"}:
        raise ValueError(f"Unsupported review candidate decision status: {status}")
    decisions = decisions_payload.setdefault("decisions", {})
    if not isinstance(decisions, dict):
        raise ValueError("review candidate decisions must be an object")
    candidate_id = str(normalized_candidate.get("id", "")).strip()
    existing = decisions.get(candidate_id)
    if isinstance(existing, dict) and str(existing.get("status", "")).strip() == normalized_status:
        raise ValueError(f"Review candidate already marked as {normalized_status}: {candidate_id}")
    decisions[candidate_id] = {
        "candidate_id": candidate_id,
        "status": normalized_status,
        "note": note.strip(),
        "source": str(normalized_candidate.get("source", "")).strip(),
        "target": str(normalized_candidate.get("target", "")).strip(),
        "type": str(normalized_candidate.get("type", "")).strip(),
        "basis": str(normalized_candidate.get("basis", "")).strip(),
        "matched_text": str(normalized_candidate.get("matched_text", "")).strip(),
        "evidence_text": str(normalized_candidate.get("evidence_text", "")).strip(),
        "source_urls": [
            str(url).strip() for url in normalized_candidate.get("source_urls", []) if str(url).strip()
        ],
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return decisions[candidate_id]


def load_thin_candidate_decisions(
    path: Path = THIN_CANDIDATE_DECISIONS_JSON,
) -> dict[str, object]:
    if not path.exists():
        return {"updated_at": "", "decisions": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("thin_candidate_decisions.json must contain an object")
    decisions = payload.get("decisions", {})
    if not isinstance(decisions, dict):
        raise ValueError("thin_candidate_decisions.json decisions must be an object")
    return {
        "updated_at": str(payload.get("updated_at", "")).strip(),
        "decisions": {
            node_id: normalize_thin_candidate_decision(decision)
            for node_id, decision in decisions.items()
            if isinstance(decision, dict)
        },
    }


def save_thin_candidate_decisions(
    payload: dict[str, object],
    path: Path = THIN_CANDIDATE_DECISIONS_JSON,
) -> None:
    normalized_payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "decisions": payload.get("decisions", {}),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(normalized_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def is_assistive_edge(edge: object) -> bool:
    review_notes = str(getattr(edge, "review_notes", "") or "")
    return any(
        marker in review_notes
        for marker in (
            "Profile bridge auto-edge",
            "Keyword cluster",
            "Shared context",
            "Shared-neighbor",
        )
    )


def graph_account_degree_stats(
    graph: GraphData,
) -> tuple[defaultdict[str, int], defaultdict[str, int], defaultdict[str, int], defaultdict[str, int]]:
    """アカウント次数を集計する。

    person 同士に加え、person↔community / person↔location の solid 関係も
    地図上の実接続として solid degree に含める（味噌 activity などが solid0 に落ちないようにする）。
    platform への affiliation はノイズになりやすいので除外する。
    """
    node_by_id = {node.id: node for node in graph.nodes}
    context_types = frozenset({"person", "community", "location"})
    degree_by_id: defaultdict[str, int] = defaultdict(int)
    follow_degree_by_id: defaultdict[str, int] = defaultdict(int)
    solid_degree_by_id: defaultdict[str, int] = defaultdict(int)
    assistive_degree_by_id: defaultdict[str, int] = defaultdict(int)
    for edge in graph.edges:
        source = node_by_id.get(edge.source)
        target = node_by_id.get(edge.target)
        if not source or not target:
            continue
        person_ids: list[str] = []
        if source.type == "person" and target.type in context_types:
            person_ids.append(source.id)
        if target.type == "person" and source.type in context_types:
            person_ids.append(target.id)
        if not person_ids:
            continue
        # person-person は両端を数える。person-context は person 側のみ。
        counted_ids = {source.id, target.id} if source.type == "person" and target.type == "person" else set(person_ids)
        assistive = is_assistive_edge(edge)
        for node_id in counted_ids:
            degree_by_id[node_id] += 1
            if assistive:
                assistive_degree_by_id[node_id] += 1
            else:
                solid_degree_by_id[node_id] += 1
        if edge.type == "follow" and source.type == "person" and target.type == "person":
            follow_degree_by_id[edge.source] += 1
            follow_degree_by_id[edge.target] += 1
    return degree_by_id, follow_degree_by_id, solid_degree_by_id, assistive_degree_by_id


def graph_account_degrees(graph: GraphData) -> tuple[defaultdict[str, int], defaultdict[str, int]]:
    degree_by_id, follow_degree_by_id, _solid_degree_by_id, _assistive_degree_by_id = (
        graph_account_degree_stats(graph)
    )
    return degree_by_id, follow_degree_by_id


def node_search_text(node: object) -> str:
    return " ".join(
        str(value)
        for value in [
            getattr(node, "id", ""),
            getattr(node, "name", ""),
            getattr(node, "description", ""),
            *getattr(node, "aliases", []),
        ]
    ).casefold()


def node_has_network_relevance_keyword(node: object) -> bool:
    text = node_search_text(node)
    return any(keyword.casefold() in text for keyword in NETWORK_RELEVANCE_KEYWORDS)


def has_real_profile_icon(node: object) -> bool:
    icon_url = str(getattr(node, "icon_url", "") or "").strip()
    if not icon_url:
        return False
    lowered = icon_url.casefold()
    if "/default_profile_" in lowered:
        return False
    if "abs.twimg.com/sticky/default_profile_images" in lowered:
        return False
    return True


def thin_candidate_decision_status(
    decisions_payload: dict[str, object] | None,
    node_id: str,
) -> str:
    decisions = (decisions_payload or {}).get("decisions", {})
    if not isinstance(decisions, dict):
        return ""
    decision = decisions.get(node_id)
    if not isinstance(decision, dict):
        return ""
    return str(decision.get("status", "")).strip().lower()


def network_relevant_person_ids(
    graph: GraphData,
    decisions_payload: dict[str, object] | None = None,
) -> set[str]:
    _degree_by_id, follow_degree_by_id, _solid_degree_by_id, _assistive_degree_by_id = (
        graph_account_degree_stats(graph)
    )
    node_by_id = {node.id: node for node in graph.nodes}
    base_relevant_ids: set[str] = set()
    for node in graph.nodes:
        if node.type != "person":
            continue
        decision_status = thin_candidate_decision_status(decisions_payload, node.id)
        if decision_status == "exclude":
            continue
        if (
            decision_status == "keep"
            or node_has_network_relevance_keyword(node)
            or follow_degree_by_id[node.id] >= 2
        ):
            base_relevant_ids.add(node.id)

    relevant_ids = set(base_relevant_ids)
    for edge in graph.edges:
        if is_assistive_edge(edge):
            continue
        source_node = node_by_id.get(edge.source)
        target_node = node_by_id.get(edge.target)
        if not source_node or not target_node:
            continue
        if source_node.type != "person" or target_node.type != "person":
            continue
        target_decision_status = thin_candidate_decision_status(decisions_payload, edge.target)
        source_decision_status = thin_candidate_decision_status(decisions_payload, edge.source)
        if edge.source in base_relevant_ids and target_decision_status != "exclude":
            relevant_ids.add(edge.target)
        if edge.target in base_relevant_ids and source_decision_status != "exclude":
            relevant_ids.add(edge.source)
    return relevant_ids


def is_network_relevant_node(
    node: object,
    follow_degree_by_id: defaultdict[str, int],
    thin_decisions_payload: dict[str, object] | None = None,
    relevant_person_ids: set[str] | None = None,
) -> bool:
    node_id = str(getattr(node, "id", ""))
    if relevant_person_ids is not None:
        return node_id in relevant_person_ids
    if thin_candidate_decision_status(thin_decisions_payload, node_id) == "keep":
        return True
    return node_has_network_relevance_keyword(node) or follow_degree_by_id[node_id] >= 2


def thin_candidate_score(node: object, degree: int, solid_degree: int | None = None) -> int:
    effective_degree = degree if solid_degree is None else solid_degree
    followers = int(getattr(node, "follower_count", 0) or 0)
    if followers >= 100000:
        score = 72
    elif followers >= 10000:
        score = 56
    elif followers >= 1000:
        score = 38
    elif followers > 0:
        score = 18
    else:
        score = 8
    if effective_degree == 0:
        score += 26
    elif effective_degree < 3:
        score += 14
    elif effective_degree < 8:
        score += 6
    if not has_real_profile_icon(node):
        score += 10
    description = str(getattr(node, "description", "") or "")
    if not description.strip() or re.match(r"^X profile for\s", description, re.IGNORECASE):
        score += 8
    return score


def thin_priority_label(score: int) -> str:
    if score >= 80:
        return "high"
    if score >= 45:
        return "medium"
    return "low"


def thin_candidate_reasons(
    node: object,
    degree: int,
    solid_degree: int | None = None,
    assistive_degree: int = 0,
) -> list[str]:
    effective_solid_degree = degree if solid_degree is None else solid_degree
    reasons = ["missing relevance keyword"]
    followers = int(getattr(node, "follower_count", 0) or 0)
    if followers >= 10000:
        reasons.append("high-follower outlier")
    elif followers == 0:
        reasons.append("missing follower count")
    if degree == 0:
        reasons.append("no account edges")
    elif effective_solid_degree == 0:
        reasons.append("no solid account edges")
    elif effective_solid_degree < 3:
        reasons.append(f"only {effective_solid_degree} solid account edges")
    if assistive_degree:
        reasons.append(f"{assistive_degree} auto bridge edges")
    if not has_real_profile_icon(node):
        reasons.append("missing real icon")
    description = str(getattr(node, "description", "") or "")
    if not description.strip() or re.match(r"^X profile for\s", description, re.IGNORECASE):
        reasons.append("thin profile text")
    return reasons


def build_thin_candidates_payload(
    graph: GraphData,
    decisions_payload: dict[str, object] | None = None,
    *,
    min_score: int = 0,
    limit: int | None = None,
) -> dict[str, object]:
    degree_by_id, follow_degree_by_id, solid_degree_by_id, assistive_degree_by_id = (
        graph_account_degree_stats(graph)
    )
    decisions = (decisions_payload or {}).get("decisions", {})
    if not isinstance(decisions, dict):
        decisions = {}
    relevant_person_ids = network_relevant_person_ids(graph, decisions_payload)

    candidates: list[dict[str, object]] = []
    for node in graph.nodes:
        if node.type != "person":
            continue
        decision = decisions.get(node.id)
        decision_status = thin_candidate_decision_status(decisions_payload, node.id)
        if decision_status in {"exclude", "keep"}:
            continue
        if is_network_relevant_node(
            node,
            follow_degree_by_id,
            decisions_payload,
            relevant_person_ids,
        ):
            continue
        degree = int(degree_by_id[node.id])
        solid_degree = int(solid_degree_by_id[node.id])
        assistive_degree = int(assistive_degree_by_id[node.id])
        follow_degree = int(follow_degree_by_id[node.id])
        score = thin_candidate_score(node, degree, solid_degree)
        if score < max(0, int(min_score)):
            continue
        candidates.append(
            {
                "id": node.id,
                "name": node.name,
                "score": score,
                "priority": thin_priority_label(score),
                "reasons": thin_candidate_reasons(node, degree, solid_degree, assistive_degree),
                "follower_count": int(node.follower_count or 0),
                "degree": degree,
                "solid_degree": solid_degree,
                "assistive_degree": assistive_degree,
                "follow_degree": follow_degree,
                "source_urls": list(node.source_urls),
                "decision_status": decision_status,
                "decision_note": str(decision.get("note", "")).strip()
                if isinstance(decision, dict)
                else "",
            }
        )
    candidates.sort(
        key=lambda item: (
            -int(item.get("score", 0)),
            -int(item.get("follower_count", 0)),
            str(item.get("name", "")),
            str(item.get("id", "")),
        )
    )
    if limit is not None:
        candidates = candidates[: max(0, int(limit))]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "candidates": candidates,
    }


def get_graph_node(graph: GraphData, node_id: str):
    normalized_id = node_id.strip()
    for node in graph.nodes:
        if node.id == normalized_id:
            return node
    raise ValueError(f"Unknown graph node id: {node_id}")


def set_thin_candidate_decision(
    decisions_payload: dict[str, object],
    graph: GraphData,
    node_id: str,
    *,
    status: str,
    note: str = "",
) -> dict[str, object]:
    normalized_status = status.strip().lower()
    if normalized_status not in THIN_CANDIDATE_STATUSES:
        raise ValueError(f"Unsupported thin candidate decision status: {status}")
    node = get_graph_node(graph, node_id)
    if node.type != "person":
        raise ValueError(f"Thin candidate decisions are only supported for person nodes: {node_id}")
    degree_by_id, _follow_degree_by_id, solid_degree_by_id, assistive_degree_by_id = (
        graph_account_degree_stats(graph)
    )
    degree = int(degree_by_id[node.id])
    solid_degree = int(solid_degree_by_id[node.id])
    assistive_degree = int(assistive_degree_by_id[node.id])
    score = thin_candidate_score(node, degree, solid_degree)
    decisions = decisions_payload.setdefault("decisions", {})
    if not isinstance(decisions, dict):
        raise ValueError("thin candidate decisions must be an object")
    decisions[node.id] = {
        "node_id": node.id,
        "status": normalized_status,
        "note": note.strip(),
        "name": node.name,
        "score": score,
        "reasons": thin_candidate_reasons(node, degree, solid_degree, assistive_degree),
        "degree": degree,
        "solid_degree": solid_degree,
        "assistive_degree": assistive_degree,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return decisions[node.id]


def set_thin_candidate_decisions(
    decisions_payload: dict[str, object],
    graph: GraphData,
    node_ids: list[str],
    *,
    status: str,
    note: str = "",
) -> list[dict[str, object]]:
    decisions: list[dict[str, object]] = []
    seen: set[str] = set()
    for node_id in node_ids:
        normalized_id = str(node_id).strip()
        if not normalized_id or normalized_id in seen:
            continue
        seen.add(normalized_id)
        decisions.append(
            set_thin_candidate_decision(
                decisions_payload,
                graph,
                normalized_id,
                status=status,
                note=note,
            )
        )
    return decisions


def _connection_audit_edge_row(
    edge: object,
    node_id: str,
    node_by_id: dict[str, object],
) -> dict[str, object]:
    source_id = str(getattr(edge, "source", ""))
    target_id = str(getattr(edge, "target", ""))
    if source_id == node_id:
        direction = "outgoing"
        neighbor_id = target_id
    elif target_id == node_id:
        direction = "incoming"
        neighbor_id = source_id
    else:
        direction = "outside"
        neighbor_id = target_id
    neighbor = node_by_id.get(neighbor_id)
    kind = "assistive" if is_assistive_edge(edge) else "solid"
    return {
        "source": source_id,
        "target": target_id,
        "type": str(getattr(edge, "type", "")),
        "direction": direction,
        "neighbor_id": neighbor_id,
        "neighbor_name": str(getattr(neighbor, "name", neighbor_id)),
        "neighbor_type": str(getattr(neighbor, "type", "")),
        "kind": kind,
        "needs_review": bool(getattr(edge, "needs_review", False)),
        "evidence_kind": str(getattr(edge, "evidence_kind", "") or ""),
        "confidence": float(getattr(edge, "confidence", 0.0) or 0.0),
        "description": str(getattr(edge, "description", "") or ""),
        "review_notes": str(getattr(edge, "review_notes", "") or ""),
        "source_urls": list(getattr(edge, "source_urls", []) or []),
    }


def build_connection_audit_payload(
    graph: GraphData,
    node_id: str,
    *,
    direction: str = "both",
    limit: int | None = None,
) -> dict[str, object]:
    if direction not in {"both", "incoming", "outgoing"}:
        raise ValueError(f"Unsupported audit direction: {direction}")
    node = get_graph_node(graph, node_id)
    node_by_id = {node.id: node for node in graph.nodes}
    rows = [
        _connection_audit_edge_row(edge, node.id, node_by_id)
        for edge in graph.edges
        if (direction in {"both", "outgoing"} and edge.source == node.id)
        or (direction in {"both", "incoming"} and edge.target == node.id)
    ]
    kind_counts = Counter(str(row["kind"]) for row in rows)
    evidence_counts = Counter(str(row["evidence_kind"]) or "unknown" for row in rows)
    type_counts = Counter(str(row["type"]) for row in rows)
    needs_review_count = sum(1 for row in rows if row["needs_review"])
    rows.sort(
        key=lambda row: (
            row["kind"] != "assistive",
            not bool(row["needs_review"]),
            str(row["type"]),
            -float(row["confidence"]),
            str(row["neighbor_name"]),
            str(row["neighbor_id"]),
        )
    )
    visible_rows = rows
    if limit is not None:
        visible_rows = rows[: max(0, int(limit))]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "node": node.to_dict(),
        "direction": direction,
        "summary": {
            "total": len(rows),
            "solid": int(kind_counts.get("solid", 0)),
            "assistive": int(kind_counts.get("assistive", 0)),
            "needs_review": int(needs_review_count),
            "accepted": len(rows) - int(needs_review_count),
            "evidence_kind": dict(sorted(evidence_counts.items())),
            "edge_type": dict(sorted(type_counts.items())),
            "shown": len(visible_rows),
        },
        "edges": visible_rows,
    }


def format_connection_audit_output(payload: dict[str, object]) -> str:
    node = payload.get("node", {})
    node_id = str(node.get("id", "") if isinstance(node, dict) else "")
    node_name = str(node.get("name", node_id) if isinstance(node, dict) else node_id)
    node_type = str(node.get("type", "") if isinstance(node, dict) else "")
    direction = str(payload.get("direction", "both"))
    summary = payload.get("summary", {})
    if not isinstance(summary, dict):
        summary = {}
    evidence_counts = summary.get("evidence_kind", {})
    edge_type_counts = summary.get("edge_type", {})
    evidence_label = ", ".join(
        f"{key}={value}" for key, value in evidence_counts.items() if key
    )
    type_label = ", ".join(f"{key}={value}" for key, value in edge_type_counts.items() if key)
    lines = [
        f"[OK] connection audit: {node_name} ({node_id}) [{node_type}]",
        f"direction: {direction}",
        (
            "summary: "
            f"total={int(summary.get('total', 0) or 0)} "
            f"shown={int(summary.get('shown', 0) or 0)} "
            f"solid={int(summary.get('solid', 0) or 0)} "
            f"assistive={int(summary.get('assistive', 0) or 0)} "
            f"needs_review={int(summary.get('needs_review', 0) or 0)} "
            f"accepted={int(summary.get('accepted', 0) or 0)}"
        ),
    ]
    if evidence_label:
        lines.append(f"evidence: {evidence_label}")
    if type_label:
        lines.append(f"edge types: {type_label}")
    rows = [row for row in payload.get("edges", []) if isinstance(row, dict)]
    lines.append("edges:")
    if not rows:
        lines.append("- none")
        return "\n".join(lines)
    for row in rows:
        flags = [str(row.get("kind", ""))]
        if row.get("needs_review"):
            flags.append("needs_review")
        evidence_kind = str(row.get("evidence_kind", "")).strip()
        if evidence_kind:
            flags.append(evidence_kind)
        line = (
            f"- {'/'.join(flag for flag in flags if flag)} "
            f"{row.get('direction', '')} {row.get('type', '')}: "
            f"{row.get('neighbor_name', row.get('neighbor_id', ''))} "
            f"({row.get('neighbor_id', '')})"
        )
        confidence = float(row.get("confidence", 0.0) or 0.0)
        line += f" confidence={confidence:.2f}"
        description = str(row.get("description", "")).strip()
        if description:
            line += f" - {description}"
        lines.append(line)
        review_notes = str(row.get("review_notes", "")).strip()
        if review_notes:
            lines.append(f"  review: {review_notes}")
    return "\n".join(lines)


def format_query_output(result: dict[str, object]) -> str:
    nodes = list(result.get("nodes", []))
    edges = list(result.get("edges", []))
    matched_node_ids = list(result.get("matched_node_ids", []))
    direction = str(result.get("direction", "both"))
    node_lookup = {str(node["id"]): node for node in nodes if isinstance(node, dict)}

    lines = [
        f"[OK] query result: {len(nodes)} nodes / {len(edges)} edges",
        f"direction: {direction}",
    ]
    if matched_node_ids:
        lines.append("matched: " + ", ".join(matched_node_ids))

    lines.append("nodes:")
    for node in nodes:
        if not isinstance(node, dict):
            continue
        aliases = ", ".join(str(alias) for alias in node.get("aliases", []))
        label = f"- {node['name']} [{node['type']}] ({node['id']})"
        if aliases:
            label += f" aliases: {aliases}"
        lines.append(label)

    lines.append("edges:")
    if not edges:
        lines.append("- none")
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source_node = node_lookup.get(str(edge["source"]), {"name": edge["source"]})
        target_node = node_lookup.get(str(edge["target"]), {"name": edge["target"]})
        description = str(edge.get("description", "")).strip()
        line = (
            f"- {source_node['name']} ({edge['source']}) "
            f"-[{edge['type']}]-> {target_node['name']} ({edge['target']})"
        )
        if description:
            line += f": {description}"
        lines.append(line)

    return "\n".join(lines)


def format_review_candidates_output(
    payload: dict[str, object],
    seed_entities: list[dict[str, object]],
) -> str:
    entity_names = {
        str(entity["id"]).strip(): str(entity.get("name", entity["id"])).strip()
        for entity in seed_entities
    }
    candidates = [item for item in payload.get("candidates", []) if isinstance(item, dict)]
    lines = [f"[OK] review candidates: {len(candidates)}"]
    if not candidates:
        lines.append("- none")
        return "\n".join(lines)

    for candidate in candidates:
        source_id = str(candidate.get("source", "")).strip()
        target_id = str(candidate.get("target", "")).strip()
        source_name = entity_names.get(source_id, source_id)
        target_name = entity_names.get(target_id, target_id)
        basis = str(candidate.get("basis", "")).strip()
        matched_text = str(candidate.get("matched_text", "")).strip()
        line = (
            f"- {source_name} ({source_id}) -[{candidate.get('type', '')}]-> "
            f"{target_name} ({target_id})"
        )
        if basis:
            line += f" basis={basis}"
        if matched_text:
            line += f" match={matched_text}"
        lines.append(line)
        review_notes = str(candidate.get("review_notes", "")).strip()
        if review_notes:
            lines.append(f"  review: {review_notes}")
    return "\n".join(lines)


def format_review_candidate_decisions_output(
    payload: dict[str, object],
    seed_entities: list[dict[str, object]],
) -> str:
    entity_names = {
        str(entity["id"]).strip(): str(entity.get("name", entity["id"])).strip()
        for entity in seed_entities
    }
    raw_decisions = payload.get("decisions", {})
    if not isinstance(raw_decisions, dict):
        raise ValueError("review candidate decisions payload must contain an object")
    decisions = [
        (candidate_id, decision)
        for candidate_id, decision in raw_decisions.items()
        if isinstance(decision, dict)
    ]
    decisions.sort(key=lambda item: str(item[1].get("updated_at", "")).strip(), reverse=True)
    lines = [f"[OK] candidate decisions: {len(decisions)}"]
    if not decisions:
        lines.append("- none")
        return "\n".join(lines)

    for candidate_id, decision in decisions:
        source_id = str(decision.get("source", "")).strip() or candidate_id.split("__")[0]
        target_id = str(decision.get("target", "")).strip()
        source_name = entity_names.get(source_id, source_id)
        target_name = entity_names.get(target_id, target_id)
        status = str(decision.get("status", "")).strip() or "unknown"
        relation_type = str(decision.get("type", "")).strip()
        basis = str(decision.get("basis", "")).strip()
        line = (
            f"- {status}: {source_name} ({source_id}) -[{relation_type}]-> "
            f"{target_name} ({target_id})"
        )
        if basis:
            line += f" basis={basis}"
        lines.append(line)
        note = str(decision.get("note", "")).strip()
        if note:
            lines.append(f"  note: {note}")
    return "\n".join(lines)


def format_thin_candidates_output(payload: dict[str, object]) -> str:
    candidates = [item for item in payload.get("candidates", []) if isinstance(item, dict)]
    lines = [f"[OK] thin candidates: {len(candidates)}"]
    if not candidates:
        lines.append("- none")
        return "\n".join(lines)

    for candidate in candidates:
        node_id = str(candidate.get("id", "")).strip()
        name = str(candidate.get("name", node_id)).strip()
        priority = str(candidate.get("priority", "")).strip()
        score = int(candidate.get("score", 0) or 0)
        followers = int(candidate.get("follower_count", 0) or 0)
        degree = int(candidate.get("degree", 0) or 0)
        solid_degree = int(candidate.get("solid_degree", degree) or 0)
        assistive_degree = int(candidate.get("assistive_degree", 0) or 0)
        reasons = ", ".join(str(reason) for reason in candidate.get("reasons", []))
        line = (
            f"- {priority} score={score}: {name} ({node_id}) "
            f"followers={followers} degree={degree} solid={solid_degree} bridge={assistive_degree}"
        )
        if reasons:
            line += f" reasons={reasons}"
        lines.append(line)
        note = str(candidate.get("decision_note", "")).strip()
        if note:
            lines.append(f"  note: {note}")
    return "\n".join(lines)


def format_thin_candidate_decisions_output(payload: dict[str, object]) -> str:
    raw_decisions = payload.get("decisions", {})
    if not isinstance(raw_decisions, dict):
        raise ValueError("thin candidate decisions payload must contain an object")
    decisions = [
        (node_id, decision)
        for node_id, decision in raw_decisions.items()
        if isinstance(decision, dict)
    ]
    decisions.sort(key=lambda item: str(item[1].get("updated_at", "")).strip(), reverse=True)
    lines = [f"[OK] thin candidate decisions: {len(decisions)}"]
    if not decisions:
        lines.append("- none")
        return "\n".join(lines)

    for node_id, decision in decisions:
        status = str(decision.get("status", "")).strip() or "unknown"
        name = str(decision.get("name", node_id)).strip()
        score = int(decision.get("score", 0) or 0)
        degree = int(decision.get("degree", 0) or 0)
        solid_degree = int(decision.get("solid_degree", degree) or 0)
        assistive_degree = int(decision.get("assistive_degree", 0) or 0)
        lines.append(
            f"- {status}: {name} ({node_id}) score={score} "
            f"degree={degree} solid={solid_degree} bridge={assistive_degree}"
        )
        note = str(decision.get("note", "")).strip()
        if note:
            lines.append(f"  note: {note}")
    return "\n".join(lines)


def format_growth_targets_output(payload: dict[str, object]) -> str:
    headline = payload.get("headline", {})
    label = str(headline.get("label", "Growth target")).strip() or "Growth target"
    current = int(headline.get("current", 0))
    target = int(headline.get("target", 0))
    lines = [f"[OK] {label}: {current} / {target}"]

    phases = [phase for phase in payload.get("phases", []) if isinstance(phase, dict)]
    if phases:
        lines.append("phases:")
        for phase in phases:
            lines.append(
                f"- {phase.get('label', '-')}: real person target {phase.get('real_person_target', '-')}"
            )

    types = [item for item in payload.get("types", []) if isinstance(item, dict)]
    if types:
        lines.append("types:")
        for item in types:
            target_min = int(item.get("target_min", 0))
            target_max = int(item.get("target_max", 0))
            target_label = (
                str(target_min) if target_min == target_max else f"{target_min}-{target_max}"
            )
            lines.append(
                f"- {item.get('type', '-')}: {int(item.get('current', 0))} / {target_label}"
            )

    density = payload.get("density")
    if isinstance(density, dict) and density:
        solid_edges = int(density.get("solid_edge_count", 0))
        assistive_edges = int(density.get("assistive_edge_count", 0))
        solid_ratio = float(density.get("solid_edge_ratio", 0.0))
        lines.append("density:")
        lines.append(
            f"- edges: solid={solid_edges} assistive={assistive_edges} "
            f"solid_ratio={solid_ratio:.1%}"
        )
        lines.append(
            f"- person-person: solid={int(density.get('person_person_solid_edges', 0))} "
            f"assistive={int(density.get('person_person_assistive_edges', 0))}"
        )
        lines.append(
            f"- relevant persons: {int(density.get('network_relevant_persons', 0))} "
            f"(solid0={int(density.get('relevant_solid_degree_0', 0))}, "
            f"solid>=3={int(density.get('relevant_solid_degree_ge_3', 0))}, "
            f"bridge_only={int(density.get('relevant_bridge_only', 0))}, "
            f"mean_solid={float(density.get('mean_relevant_solid_degree', 0.0)):.2f})"
        )
        lines.append(
            f"- periphery excluded: {int(density.get('excluded_thin_persons', 0))}"
        )

    clusters = payload.get("clusters")
    if isinstance(clusters, dict) and clusters:
        lines.append("clusters:")
        lines.append(
            f"- keyword_group: {int(clusters.get('cluster_count', 0))} groups / "
            f"assigned_persons={int(clusters.get('assigned_persons', 0))}"
        )
        for item in clusters.get("top", [])[:8]:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"- {item.get('label', '-')}: size={int(item.get('size', 0))} "
                f"solid_in={int(item.get('solid_internal_edges', 0))} "
                f"mean_solid={float(item.get('mean_solid_degree', 0.0)):.2f} "
                f"solid0={int(item.get('solid0', 0))}"
            )
    return "\n".join(lines)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Manual-first graph bootstrapper inspired by sokusuu-ranking."
    )
    parser.add_argument(
        "--force-sample",
        action="store_true",
        help="Overwrite JSON with the sample graph from seed_entities.txt.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate seed and snapshot inputs without regenerating outputs.",
    )
    parser.add_argument(
        "--query",
        default="",
        help="Search term for query_relations against the current source graph.",
    )
    parser.add_argument(
        "--query-node-id",
        default="",
        help="Exact node id focus for query_relations.",
    )
    parser.add_argument(
        "--query-node-type",
        choices=NODE_TYPES,
        help="Optional node type filter for query_relations.",
    )
    parser.add_argument(
        "--query-edge-type",
        choices=EDGE_TYPES,
        help="Optional edge type filter for query_relations.",
    )
    parser.add_argument(
        "--query-direction",
        choices=("both", "incoming", "outgoing"),
        default="both",
        help="Relation direction relative to the matched node(s).",
    )
    parser.add_argument(
        "--query-json",
        action="store_true",
        help="Print query_relations output as JSON.",
    )
    parser.add_argument(
        "--audit-connections",
        default="",
        metavar="NODE_ID",
        help="Audit one node's immediate connections by solid/assistive/review status.",
    )
    parser.add_argument(
        "--audit-direction",
        choices=("both", "incoming", "outgoing"),
        default="both",
        help="Connection direction for --audit-connections.",
    )
    parser.add_argument(
        "--audit-limit",
        type=int,
        default=80,
        metavar="N",
        help="Limit connection audit rows after sorting.",
    )
    parser.add_argument(
        "--audit-json",
        action="store_true",
        help="Print connection audit output as JSON.",
    )
    parser.add_argument(
        "--approve-candidate",
        default="",
        help="Approve one review candidate id into manual source_snapshots observations.",
    )
    parser.add_argument(
        "--dismiss-candidate",
        default="",
        help="Dismiss one review candidate id so it no longer appears in the review queue.",
    )
    parser.add_argument(
        "--approval-note",
        default="",
        help="Optional note to attach when approving a review candidate.",
    )
    parser.add_argument(
        "--dismiss-note",
        default="",
        help="Optional note to attach when dismissing a review candidate.",
    )
    parser.add_argument(
        "--list-review-candidates",
        action="store_true",
        help="List the current review-only candidate queue in the terminal.",
    )
    parser.add_argument(
        "--list-candidate-decisions",
        action="store_true",
        help="List approved/dismissed candidate decisions in the terminal.",
    )
    parser.add_argument(
        "--list-thin-candidates",
        action="store_true",
        help="List low-signal person nodes hidden by the related-person default view.",
    )
    parser.add_argument(
        "--thin-limit",
        type=int,
        default=None,
        metavar="N",
        help="Limit --list-thin-candidates to the top N rows after sorting.",
    )
    parser.add_argument(
        "--thin-min-score",
        type=int,
        default=0,
        metavar="N",
        help="Only list thin candidates with score >= N.",
    )
    parser.add_argument(
        "--list-thin-decisions",
        action="store_true",
        help="List saved keep/exclude/review decisions for thin candidates.",
    )
    parser.add_argument(
        "--mark-thin-candidate",
        default="",
        help="Save a keep/exclude/review decision for one thin candidate node id.",
    )
    parser.add_argument(
        "--mark-thin-candidates",
        nargs="+",
        default=[],
        metavar="NODE_ID",
        help="Save the same keep/exclude/review decision for multiple thin candidate node ids.",
    )
    parser.add_argument(
        "--thin-status",
        choices=THIN_CANDIDATE_STATUSES,
        default="review",
        help="Decision status for --mark-thin-candidate or --mark-thin-candidates.",
    )
    parser.add_argument(
        "--thin-note",
        default="",
        help="Optional note to attach when marking a thin candidate.",
    )
    parser.add_argument(
        "--review-json",
        action="store_true",
        help="Print review candidate or decision listings as JSON.",
    )
    parser.add_argument(
        "--growth-progress",
        action="store_true",
        help="Print current real-growth progress against the agreed target.",
    )
    args = parser.parse_args()

    seed_entities = load_seed_entities(SEED_FILE)
    growth_targets_payload = build_growth_targets_payload(seed_entities)
    snapshots = load_all_source_snapshots()
    approval_mode = bool(str(args.approve_candidate).strip())
    dismiss_mode = bool(str(args.dismiss_candidate).strip())
    mark_thin_candidate_mode = bool(str(args.mark_thin_candidate).strip()) or bool(args.mark_thin_candidates)
    list_review_candidates_mode = bool(args.list_review_candidates)
    list_candidate_decisions_mode = bool(args.list_candidate_decisions)
    list_thin_candidates_mode = bool(args.list_thin_candidates)
    list_thin_decisions_mode = bool(args.list_thin_decisions)
    growth_progress_mode = bool(args.growth_progress)
    audit_connections_mode = bool(str(args.audit_connections).strip())
    query_mode = any(
        [
            bool(str(args.query).strip()),
            bool(str(args.query_node_id).strip()),
            args.query_node_type is not None,
            args.query_edge_type is not None,
        ]
    )

    def load_current_graph() -> GraphData:
        if NODES_JSON.exists() and EDGES_JSON.exists():
            return load_graph(NODES_JSON, EDGES_JSON)
        graph = build_graph_from_sources(seed_entities, snapshots)
        materialize_inferred_social_edges(
            graph,
            seed_entities,
            load_generated_snapshots(),
            load_review_candidate_decisions(REVIEW_CANDIDATE_DECISIONS_JSON),
        )
        return graph

    if args.validate_only:
        graph = build_graph_from_sources(seed_entities, snapshots)
        print(
            f"[OK] inputs valid: {len(seed_entities)} seed entities / "
            f"{len(snapshots)} source snapshots / "
            f"{len(graph.nodes)} graph nodes / {len(graph.edges)} graph edges"
        )
        return

    if approval_mode:
        manual_snapshots = load_source_snapshots(SNAPSHOT_FILE)
        generated_snapshots = load_generated_snapshots()
        review_candidates_payload = load_review_candidates(REVIEW_CANDIDATES_JSON)
        decisions_payload = load_review_candidate_decisions(REVIEW_CANDIDATE_DECISIONS_JSON)
        candidate = get_review_candidate(review_candidates_payload, str(args.approve_candidate))
        reference_snapshot = next(
            (
                snapshot
                for snapshot in generated_snapshots
                if str(snapshot.get("account_id", "")).strip() == str(candidate.get("source", "")).strip()
            ),
            None,
        )
        observation = approve_review_candidate(
            manual_snapshots,
            candidate,
            approval_note=str(args.approval_note),
            reference_snapshot=reference_snapshot,
        )
        set_review_candidate_decision(
            decisions_payload,
            candidate,
            status="approved",
            note=str(args.approval_note),
        )
        save_source_snapshots(manual_snapshots, SNAPSHOT_FILE)
        save_review_candidate_decisions(decisions_payload, REVIEW_CANDIDATE_DECISIONS_JSON)
        graph = build_graph_from_sources(
            seed_entities,
            merge_snapshots_by_account(manual_snapshots, generated_snapshots),
        )
        materialize_inferred_social_edges(
            graph,
            seed_entities,
            generated_snapshots,
            decisions_payload,
        )
        refresh_outputs(graph)
        refreshed_candidates = refresh_review_candidates(
            seed_entities,
            generated_snapshots,
            graph,
            decisions_payload=decisions_payload,
        )
        export_html(
            graph,
            "docs/index.html",
            title="Pickup Artist Network",
            review_candidates_payload=refreshed_candidates,
            review_candidate_decisions_payload=decisions_payload,
            thin_candidate_decisions_payload=load_thin_candidate_decisions(
                THIN_CANDIDATE_DECISIONS_JSON
            ),
            growth_targets_payload=growth_targets_payload,
        )
        print(
            f"[OK] approved review candidate {candidate['id']} -> "
            f"{candidate['source']} -[{candidate['type']}]-> {candidate['target']} / "
            f"manual snapshots updated / {len(refreshed_candidates.get('candidates', []))} review candidates remain"
        )
        print(f"[OK] approved observation: {observation['description']}")
        return

    if dismiss_mode:
        decisions_payload = load_review_candidate_decisions(REVIEW_CANDIDATE_DECISIONS_JSON)
        graph = build_graph_from_sources(seed_entities, snapshots)
        materialize_inferred_social_edges(
            graph,
            seed_entities,
            load_generated_snapshots(),
            decisions_payload,
        )
        review_candidates_payload = load_review_candidates(REVIEW_CANDIDATES_JSON)
        candidate = get_review_candidate(review_candidates_payload, str(args.dismiss_candidate))
        decision = set_review_candidate_decision(
            decisions_payload,
            candidate,
            status="dismissed",
            note=str(args.dismiss_note),
        )
        save_review_candidate_decisions(decisions_payload, REVIEW_CANDIDATE_DECISIONS_JSON)
        refreshed_candidates = refresh_review_candidates(
            seed_entities,
            load_generated_snapshots(),
            graph,
            decisions_payload=decisions_payload,
        )
        export_html(
            graph,
            "docs/index.html",
            title="Pickup Artist Network",
            review_candidates_payload=refreshed_candidates,
            review_candidate_decisions_payload=decisions_payload,
            thin_candidate_decisions_payload=load_thin_candidate_decisions(
                THIN_CANDIDATE_DECISIONS_JSON
            ),
            growth_targets_payload=growth_targets_payload,
        )
        print(
            f"[OK] dismissed review candidate {candidate['id']} / "
            f"{len(refreshed_candidates.get('candidates', []))} review candidates remain"
        )
        if decision.get("note"):
            print(f"[OK] dismiss note: {decision['note']}")
        return

    if mark_thin_candidate_mode:
        graph = load_current_graph()
        thin_decisions_payload = load_thin_candidate_decisions(THIN_CANDIDATE_DECISIONS_JSON)
        thin_node_ids = [
            str(args.mark_thin_candidate).strip(),
            *[str(node_id).strip() for node_id in args.mark_thin_candidates],
        ]
        decisions = set_thin_candidate_decisions(
            thin_decisions_payload,
            graph,
            thin_node_ids,
            status=str(args.thin_status),
            note=str(args.thin_note),
        )
        save_thin_candidate_decisions(thin_decisions_payload, THIN_CANDIDATE_DECISIONS_JSON)
        review_candidates_payload = load_review_candidates(REVIEW_CANDIDATES_JSON)
        review_candidate_decisions_payload = load_review_candidate_decisions(
            REVIEW_CANDIDATE_DECISIONS_JSON
        )
        export_html(
            graph,
            "docs/index.html",
            title="Pickup Artist Network",
            review_candidates_payload=review_candidates_payload,
            review_candidate_decisions_payload=review_candidate_decisions_payload,
            thin_candidate_decisions_payload=thin_decisions_payload,
            growth_targets_payload=growth_targets_payload,
        )
        status_label = str(args.thin_status)
        print(f"[OK] marked {len(decisions)} thin candidates as {status_label}")
        for decision in decisions:
            print(
                f"- {decision['node_id']}: {decision['name']} "
                f"score={decision['score']}"
            )
        if str(args.thin_note).strip():
            print(f"[OK] thin note: {str(args.thin_note).strip()}")
        return

    if list_review_candidates_mode:
        graph = build_graph_from_sources(seed_entities, snapshots)
        decisions_payload = load_review_candidate_decisions(REVIEW_CANDIDATE_DECISIONS_JSON)
        review_candidates_payload = generate_review_candidates(
            seed_entities,
            load_generated_snapshots(),
            graph,
            decisions_payload=decisions_payload,
        )
        if args.review_json:
            print(json.dumps(review_candidates_payload, ensure_ascii=False, indent=2))
        else:
            print(format_review_candidates_output(review_candidates_payload, seed_entities))
        return

    if list_candidate_decisions_mode:
        decisions_payload = load_review_candidate_decisions(REVIEW_CANDIDATE_DECISIONS_JSON)
        if args.review_json:
            print(json.dumps(decisions_payload, ensure_ascii=False, indent=2))
        else:
            print(format_review_candidate_decisions_output(decisions_payload, seed_entities))
        return

    if list_thin_candidates_mode:
        graph = load_current_graph()
        thin_decisions_payload = load_thin_candidate_decisions(THIN_CANDIDATE_DECISIONS_JSON)
        thin_candidates_payload = build_thin_candidates_payload(
            graph,
            decisions_payload=thin_decisions_payload,
            min_score=max(0, int(args.thin_min_score or 0)),
            limit=args.thin_limit,
        )
        if args.review_json:
            print(json.dumps(thin_candidates_payload, ensure_ascii=False, indent=2))
        else:
            print(format_thin_candidates_output(thin_candidates_payload))
        return

    if list_thin_decisions_mode:
        thin_decisions_payload = load_thin_candidate_decisions(THIN_CANDIDATE_DECISIONS_JSON)
        if args.review_json:
            print(json.dumps(thin_decisions_payload, ensure_ascii=False, indent=2))
        else:
            print(format_thin_candidate_decisions_output(thin_decisions_payload))
        return

    if growth_progress_mode:
        try:
            density_graph = load_graph(NODES_JSON, EDGES_JSON)
        except (OSError, ValueError, json.JSONDecodeError, KeyError, TypeError):
            density_graph = build_graph_from_sources(seed_entities, snapshots)
        growth_targets_payload = build_growth_targets_payload(
            seed_entities,
            graph=density_graph,
            thin_decisions_payload=load_thin_candidate_decisions(THIN_CANDIDATE_DECISIONS_JSON),
        )
        if args.review_json:
            print(json.dumps(growth_targets_payload, ensure_ascii=False, indent=2))
        else:
            print(format_growth_targets_output(growth_targets_payload))
        return

    if audit_connections_mode:
        graph = load_current_graph()
        payload = build_connection_audit_payload(
            graph,
            str(args.audit_connections).strip(),
            direction=str(args.audit_direction),
            limit=args.audit_limit,
        )
        if args.audit_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(format_connection_audit_output(payload))
        return

    if query_mode:
        graph = load_current_graph()
        result = query_relations(
            graph,
            search_term=str(args.query).strip(),
            node_type=args.query_node_type,
            edge_type=args.query_edge_type,
            node_id=str(args.query_node_id).strip() or None,
            direction=args.query_direction,
        )
        if args.query_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(format_query_output(result))
        return

    decisions_payload = load_review_candidate_decisions(REVIEW_CANDIDATE_DECISIONS_JSON)
    thin_decisions_payload = load_thin_candidate_decisions(THIN_CANDIDATE_DECISIONS_JSON)
    if args.force_sample or not NODES_JSON.exists() or not EDGES_JSON.exists():
        graph = build_graph_from_sources(seed_entities, snapshots)
        materialize_inferred_social_edges(
            graph,
            seed_entities,
            load_generated_snapshots(),
            decisions_payload,
        )
    else:
        graph = load_graph(NODES_JSON, EDGES_JSON)

    refresh_outputs(graph)
    save_review_candidate_decisions(decisions_payload, REVIEW_CANDIDATE_DECISIONS_JSON)
    refreshed_candidates = refresh_review_candidates(
        seed_entities,
        load_generated_snapshots(),
        graph,
        decisions_payload=decisions_payload,
    )
    export_html(
        graph,
        "docs/index.html",
        title="Pickup Artist Network",
        review_candidates_payload=refreshed_candidates,
        review_candidate_decisions_payload=decisions_payload,
        thin_candidate_decisions_payload=thin_decisions_payload,
        growth_targets_payload=growth_targets_payload,
    )
    headline = growth_targets_payload.get("headline", {})
    print(
        f"[OK] graph ready: {len(graph.nodes)} nodes / {len(graph.edges)} edges -> "
        f"{NODES_JSON}, {EDGES_JSON}, {NODES_CSV}, {EDGES_CSV}, {NETWORKX_METRICS}, {SQLITE_DB}, {REVIEW_CANDIDATES_JSON}"
    )
    print(
        f"[OK] {headline.get('label', 'Real person target')}: "
        f"{headline.get('current', 0)} / {headline.get('target', 0)}"
    )


if __name__ == "__main__":
    main()
