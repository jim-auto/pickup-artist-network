from __future__ import annotations

import argparse
import copy
import json
import re
from datetime import datetime, timezone
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
DEFAULT_MATERIALIZED_REVIEW_EDGE_TYPES = frozenset({"profile_mention"})
LEGACY_EDGE_TYPES = frozenset({"reference"})
FOLLOW_REFERENCE_PREFIX = "authenticated x following list shows this account follows @"

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


def build_growth_targets_payload(seed_entities: list[dict[str, object]]) -> dict[str, object]:
    current_real_counts = {node_type: 0 for node_type in NODE_TYPES}
    for entity in seed_entities:
        if str(entity.get("scope", "unspecified")).strip() != "real":
            continue
        entity_type = str(entity.get("type", "")).strip()
        if entity_type in current_real_counts:
            current_real_counts[entity_type] += 1

    return {
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
        if icon_url:
            source_node.icon_url = icon_url
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
    """生成スナップショットの文本から推定した profile_mention を既定でグラフに載せる。

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
        display_match = matched if matched.startswith("@") else (f"@{matched}" if matched else "?")
        description = (
            f"公開プロフィール・概要・固定ポストの文本から {display_match} への言及として推定（自動）。"
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
                    "review_notes": str(cand.get("review_notes", "")).strip(),
                },
            )
            added += 1
        except ValueError as exc:
            if str(exc).startswith("Duplicate edge"):
                continue
            raise
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
    return "\n".join(lines)


def main() -> None:
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
    list_review_candidates_mode = bool(args.list_review_candidates)
    list_candidate_decisions_mode = bool(args.list_candidate_decisions)
    growth_progress_mode = bool(args.growth_progress)
    query_mode = any(
        [
            bool(str(args.query).strip()),
            bool(str(args.query_node_id).strip()),
            args.query_node_type is not None,
            args.query_edge_type is not None,
        ]
    )

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
            growth_targets_payload=growth_targets_payload,
        )
        print(
            f"[OK] dismissed review candidate {candidate['id']} / "
            f"{len(refreshed_candidates.get('candidates', []))} review candidates remain"
        )
        if decision.get("note"):
            print(f"[OK] dismiss note: {decision['note']}")
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

    if growth_progress_mode:
        if args.review_json:
            print(json.dumps(growth_targets_payload, ensure_ascii=False, indent=2))
        else:
            print(format_growth_targets_output(growth_targets_payload))
        return

    if query_mode:
        graph = build_graph_from_sources(seed_entities, snapshots)
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
