"""Densify solid connections for high-follower low-degree people.

Targets: follower_count >= 1000 and person-person degree <= 15.

Sources of solid evidence (no assistive markers):
  - data/growth/following_known_follows.json
  - data/growth/following_candidates.json
  - public bio @handle mentions
  - sub/main bio links (メイン→@handle)
  - bio keyword → location/community activity edges
  - shared solid follow-neighborhood (3+ common neighbors)

Run:
  python scripts/densify_sparse_high_followers.py
  python build_site.py --skip-collector
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from growth_probe_candidates import handle_to_id  # noqa: E402

GENERATED_SNAPSHOT_FILE = ROOT / "data" / "source_snapshots.generated.json"
FOLLOWING_CANDIDATES_FILE = ROOT / "data" / "growth" / "following_candidates.json"
FOLLOWING_KNOWN_FOLLOWS_FILE = ROOT / "data" / "growth" / "following_known_follows.json"
FOLLOWING_SCREENED_FILE = ROOT / "data" / "growth" / "following_screened.json"
SEED_FILE = ROOT / "seed_entities.txt"
NODES_FILE = ROOT / "data" / "nodes.json"
EDGES_FILE = ROOT / "data" / "edges.json"
TARGETS_FILE = ROOT / "data" / "growth" / "sparse_high_follower_targets.json"

FOLLOW_NOTE = (
    "Densify pass: authenticated following list materialised as solid follow "
    "between already-seeded accounts (high-follower low-degree fix)."
)
MENTION_NOTE = (
    "Densify pass: public profile/bio @handle mention of an already-seeded account."
)
SUB_NOTE = (
    "Densify pass: sub/main account link from public bio (メイン→@handle)."
)
CONTEXT_NOTE = (
    "Densify pass: public bio keyword maps this high-follower hub to a seeded "
    "location/community context node."
)
COHOOD_NOTE = (
    "Densify pass: shared solid follow-neighborhood (2+ common neighbors) between "
    "high-follower low-degree accounts."
)
TAG_NOTE = (
    "Densify pass: shared public profile scene tags between a high-follower "
    "low-degree hub and a dense scene account."
)
IDENTITY_NOTE = (
    "Densify pass: public bio identifies this account as a sub/video/male-limited "
    "account of an already-seeded person by name."
)
HANDLE_RE = re.compile(r"@([A-Za-z0-9_]{2,30})")
IDENTITY_CUES = (
    "動画垢",
    "男性限定アカウント",
    "男性限定垢",
    "サブ垢",
    "サブアカウント",
    "本垢",
)
SCENE_TAGS: list[tuple[str, tuple[str, ...]]] = [
    ("ナンパ", ("ナンパ", "nanpa", "nampa")),
    ("スト", ("ストナン", "ストリート", "street", "路上")),
    ("ネト", ("ネトナン", "出会い系", "ネット出会い")),
    ("マチアプ", ("マチアプ", "マッチングアプリ", "tinder", "with", "タップル", "東カレ")),
    ("講習", ("講習", "コンサル", "サロン", "受講")),
    ("ホスト", ("ホスト", "歌舞伎町")),
    ("外見", ("外見", "整形", "垢抜け", "美容")),
    ("モテ", ("モテ", "恋愛", "彼女")),
    ("即", ("即", "経験人数", "get")),
    ("味噌", ("味噌", "miso")),
    ("MBH", ("mbh", "まーぼー")),
    ("アツスト", ("アツスト", "🐶🦁")),
]
ASSISTIVE_MARKERS = (
    "Profile bridge auto-edge",
    "Keyword cluster",
    "Shared context",
    "Shared-neighbor",
)

# bio keyword → seeded context node id
BIO_CONTEXT_RULES: list[tuple[tuple[str, ...], str, str]] = [
    (("マチアプ", "マッチングアプリ", "tinder", "with", "タップル", "ペアーズ", "東カレ", "ネトナン"), "matching-apps", "activity"),
    (("渋谷",), "shibuya", "activity"),
    (("新宿",), "shinjuku", "activity"),
    (("池袋",), "ikebukuro", "activity"),
    (("恵比寿",), "ebisu", "activity"),
    (("東京", "都内", "帝都", "tokyo"), "tokyo", "activity"),
    (("名古屋", "栄"), "nagoya", "activity"),
    (("大阪", "梅田", "難波"), "osaka", "activity"),
    (("関西",), "kansai", "activity"),
    (("関東",), "kanto", "activity"),
    (("味噌", "miso"), "miso", "activity"),
    (("えるスタ", "elsta"), "elsta", "affiliation"),
    (("東京ストナン会",), "tokyo-stonan-kai", "affiliation"),
]


def _load_json_list(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path} must contain a list")
    return [row for row in payload if isinstance(row, dict)]


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _seed_ids() -> set[str]:
    ids: set[str] = set()
    for line in SEED_FILE.read_text(encoding="utf-8").splitlines():
        if not line.startswith("person|"):
            continue
        parts = line.split("|")
        if len(parts) >= 2 and parts[1].strip():
            ids.add(parts[1].strip())
    return ids


def _handle_to_account_id() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for line in SEED_FILE.read_text(encoding="utf-8").splitlines():
        if not line.startswith("person|"):
            continue
        parts = line.split("|")
        if len(parts) < 4:
            continue
        account_id = parts[1].strip()
        mapping[account_id.casefold()] = account_id
        mapping[account_id.replace("-", "_").casefold()] = account_id
        for handle in parts[3].split(","):
            cleaned = handle.strip().lstrip("@")
            if cleaned:
                mapping[cleaned.casefold()] = account_id
    for row in _load_json_list(GENERATED_SNAPSHOT_FILE):
        account_id = str(row.get("account_id", "")).strip()
        if not account_id:
            continue
        mapping[account_id.casefold()] = account_id
        mapping[account_id.replace("-", "_").casefold()] = account_id
        url = str(row.get("profile_url", "") or "")
        if "x.com/" in url:
            handle = url.rstrip("/").rsplit("/", 1)[-1]
            if handle:
                mapping[handle.casefold()] = account_id
    if NODES_FILE.exists():
        for node in _load_json_list(NODES_FILE):
            if str(node.get("type")) != "person":
                continue
            account_id = str(node.get("id", "")).strip()
            if not account_id:
                continue
            mapping[account_id.casefold()] = account_id
            for alias in node.get("aliases") or []:
                cleaned = str(alias).strip().lstrip("@")
                if cleaned:
                    mapping[cleaned.casefold()] = account_id
    return mapping


def _ensure_observation(
    snapshot: dict[str, object],
    *,
    target: str,
    edge_type: str,
    description: str,
    source_urls: list[str],
    confidence: float,
    review_notes: str,
) -> bool:
    observations = list(snapshot.get("observations") or [])
    already = any(
        isinstance(obs, dict)
        and str(obs.get("target", "")).strip() == target
        and str(obs.get("type", "")).strip() == edge_type
        for obs in observations
    )
    if already:
        return False
    observations.append(
        {
            "target": target,
            "type": edge_type,
            "description": description,
            "source_urls": source_urls,
            "confidence": confidence,
            "evidence_kind": "mixed",
            "needs_review": True,
            "review_notes": review_notes,
        }
    )
    snapshot["observations"] = observations
    return True


def _is_assistive_note(notes: object) -> bool:
    text = str(notes or "")
    return any(marker in text for marker in ASSISTIVE_MARKERS)


def _live_sparse_rows() -> list[dict[str, object]]:
    """High-follower people whose current person-person degree is still low."""
    if not NODES_FILE.exists() or not EDGES_FILE.exists():
        return []
    nodes = _load_json_list(NODES_FILE)
    edges = _load_json_list(EDGES_FILE)
    by = {str(n.get("id")): n for n in nodes}
    degree: dict[str, int] = defaultdict(int)
    solid: dict[str, int] = defaultdict(int)
    for edge in edges:
        source = by.get(str(edge.get("source")))
        target = by.get(str(edge.get("target")))
        if not source or not target:
            continue
        if source.get("type") != "person" or target.get("type") != "person":
            continue
        source_id = str(source["id"])
        target_id = str(target["id"])
        degree[source_id] += 1
        degree[target_id] += 1
        if not _is_assistive_note(edge.get("review_notes")):
            solid[source_id] += 1
            solid[target_id] += 1
    rows: list[dict[str, object]] = []
    for node in nodes:
        if node.get("type") != "person":
            continue
        node_id = str(node.get("id", "")).strip()
        followers = int(node.get("follower_count") or 0)
        person_degree = degree[node_id]
        if followers < 1000 or person_degree > 15:
            continue
        text = f"{node.get('name') or ''} {node.get('description') or ''}"
        rows.append(
            {
                "id": node_id,
                "followers": followers,
                "solid": solid[node_id],
                "person_degree": person_degree,
                "scene": bool(_text_tags(text)),
                "name": node.get("name"),
                "description": str(node.get("description") or "")[:80],
                "aliases": node.get("aliases") or [],
            }
        )
    rows.sort(key=lambda row: (-int(row["followers"]), int(row["solid"]), str(row["id"])))
    return rows


def _sparse_high_follower_ids() -> set[str]:
    """Use the live graph so already-densified hubs are not over-tagged."""
    live = {str(row["id"]) for row in _live_sparse_rows()}
    if live:
        return live
    if TARGETS_FILE.exists():
        rows = _load_json_list(TARGETS_FILE)
        return {str(row.get("id", "")).strip() for row in rows if str(row.get("id", "")).strip()}
    return set()


def write_sparse_targets() -> int:
    rows = _live_sparse_rows()
    _write_json(TARGETS_FILE, rows)
    return len(rows)


def materialize_known_follows(
    snapshots_by_id: dict[str, dict[str, object]],
    seed_ids: set[str],
    prioritize_ids: set[str] | None = None,
) -> int:
    added = 0
    for row in _load_json_list(FOLLOWING_KNOWN_FOLLOWS_FILE):
        source_id = str(row.get("source_id", "")).strip()
        target_id = str(row.get("target_id", "")).strip()
        handle = str(row.get("handle", "") or target_id).lstrip("@")
        if not source_id or not target_id or source_id == target_id:
            continue
        if source_id not in seed_ids or target_id not in seed_ids:
            continue
        if prioritize_ids and source_id not in prioritize_ids and target_id not in prioritize_ids:
            # Still allow all seed-seed known follows; prioritization is only ordering.
            pass
        snapshot = snapshots_by_id.get(source_id)
        if not snapshot:
            continue
        source_url = str(snapshot.get("profile_url", "") or f"https://x.com/{source_id}")
        following_url = str(row.get("following_url") or f"{source_url.rstrip('/')}/following")
        if _ensure_observation(
            snapshot,
            target=target_id,
            edge_type="follow",
            description=f"Authenticated X following list shows this account follows @{handle}.",
            source_urls=[source_url, following_url],
            confidence=0.66,
            review_notes=FOLLOW_NOTE,
        ):
            added += 1
    return added


def materialize_candidate_follows(
    snapshots_by_id: dict[str, dict[str, object]],
    handle_map: dict[str, str],
    seed_ids: set[str],
) -> int:
    added = 0
    for row in _load_json_list(FOLLOWING_CANDIDATES_FILE):
        target_id = str(row.get("account_id", "")).strip()
        if target_id not in seed_ids:
            target_id = handle_map.get(str(row.get("handle", "")).casefold(), "")
        if not target_id or target_id not in seed_ids:
            continue
        handle = str(row.get("handle", "") or target_id).lstrip("@")
        for source in row.get("sources") or []:
            source_id = str(source).strip()
            if source_id not in seed_ids:
                source_id = handle_map.get(source_id.casefold(), "")
            if not source_id or source_id not in seed_ids or source_id == target_id:
                continue
            snapshot = snapshots_by_id.get(source_id)
            if not snapshot:
                continue
            source_url = str(snapshot.get("profile_url", "") or f"https://x.com/{source_id}")
            if _ensure_observation(
                snapshot,
                target=target_id,
                edge_type="follow",
                description=f"Authenticated X following list shows this account follows @{handle}.",
                source_urls=[source_url, f"{source_url.rstrip('/')}/following"],
                confidence=0.64,
                review_notes=FOLLOW_NOTE,
            ):
                added += 1
    return added


def materialize_bio_mentions(
    snapshots_by_id: dict[str, dict[str, object]],
    handle_map: dict[str, str],
    seed_ids: set[str],
) -> int:
    added = 0
    for account_id, snapshot in snapshots_by_id.items():
        if account_id not in seed_ids:
            continue
        text = "\n".join(
            str(snapshot.get(key, "") or "")
            for key in ("profile_text", "summary", "pinned_post_text")
        )
        # Also include node description if present later via nodes file text.
        for handle in HANDLE_RE.findall(text):
            target_id = handle_map.get(handle.casefold())
            if not target_id or target_id == account_id or target_id not in seed_ids:
                continue
            source_url = str(snapshot.get("profile_url", "") or f"https://x.com/{account_id}")
            if _ensure_observation(
                snapshot,
                target=target_id,
                edge_type="profile_mention",
                description=f"Public profile text mentions @{handle}.",
                source_urls=[source_url],
                confidence=0.72,
                review_notes=MENTION_NOTE,
            ):
                added += 1
    return added


def materialize_node_description_mentions(
    snapshots_by_id: dict[str, dict[str, object]],
    handle_map: dict[str, str],
    seed_ids: set[str],
) -> int:
    """Catch @handles that only exist on built node description/name/aliases."""
    if not NODES_FILE.exists():
        return 0
    added = 0
    for node in _load_json_list(NODES_FILE):
        if node.get("type") != "person":
            continue
        account_id = str(node.get("id", "")).strip()
        if account_id not in seed_ids:
            continue
        snapshot = snapshots_by_id.get(account_id)
        if not snapshot:
            continue
        text = "\n".join(
            [
                str(node.get("name") or ""),
                str(node.get("description") or ""),
                " ".join(str(a) for a in (node.get("aliases") or [])),
                str(snapshot.get("profile_text") or ""),
                str(snapshot.get("summary") or ""),
            ]
        )
        for handle in HANDLE_RE.findall(text):
            target_id = handle_map.get(handle.casefold())
            if not target_id or target_id == account_id or target_id not in seed_ids:
                continue
            source_url = str(snapshot.get("profile_url", "") or f"https://x.com/{account_id}")
            if _ensure_observation(
                snapshot,
                target=target_id,
                edge_type="profile_mention",
                description=f"Public profile/name text mentions @{handle}.",
                source_urls=[source_url],
                confidence=0.7,
                review_notes=MENTION_NOTE,
            ):
                added += 1
    return added


def materialize_bio_context_edges(
    snapshots_by_id: dict[str, dict[str, object]],
    sparse_ids: set[str],
) -> int:
    """Map sparse high-follower bios to location/community context nodes (solid)."""
    existing_context_ids = {
        str(node.get("id"))
        for node in (_load_json_list(NODES_FILE) if NODES_FILE.exists() else [])
        if node.get("type") in {"location", "community"}
    }
    added = 0
    for account_id in sparse_ids:
        snapshot = snapshots_by_id.get(account_id)
        if not snapshot:
            continue
        text = "\n".join(
            str(snapshot.get(key, "") or "")
            for key in ("profile_text", "summary", "pinned_post_text")
        ).casefold()
        if not text.strip():
            continue
        source_url = str(snapshot.get("profile_url", "") or f"https://x.com/{account_id}")
        for patterns, context_id, edge_type in BIO_CONTEXT_RULES:
            if context_id not in existing_context_ids:
                continue
            if not any(pattern.casefold() in text for pattern in patterns):
                continue
            label = patterns[0]
            if _ensure_observation(
                snapshot,
                target=context_id,
                edge_type=edge_type,
                description=f"Public bio references {label} (context densify for high-follower hub).",
                source_urls=[source_url],
                confidence=0.62,
                review_notes=CONTEXT_NOTE,
            ):
                added += 1
    return added


def materialize_shared_follow_neighborhood(
    snapshots_by_id: dict[str, dict[str, object]],
    seed_ids: set[str],
    sparse_ids: set[str],
    *,
    min_common: int = 3,
    max_new_per_node: int = 8,
) -> int:
    """Connect sparse high-fl hubs that share many solid follow-neighbors."""
    if not EDGES_FILE.exists() or not NODES_FILE.exists():
        return 0
    nodes = {str(n.get("id")): n for n in _load_json_list(NODES_FILE)}
    # neighbors via solid follow only
    follow_out: dict[str, set[str]] = defaultdict(set)
    follow_in: dict[str, set[str]] = defaultdict(set)
    for edge in _load_json_list(EDGES_FILE):
        if str(edge.get("type")) != "follow":
            continue
        if _is_assistive_note(edge.get("review_notes")):
            continue
        source = str(edge.get("source", "")).strip()
        target = str(edge.get("target", "")).strip()
        if source not in seed_ids or target not in seed_ids:
            continue
        follow_out[source].add(target)
        follow_in[target].add(source)

    # also pending observations in snapshots (not yet rebuilt)
    for account_id, snapshot in snapshots_by_id.items():
        for obs in snapshot.get("observations") or []:
            if not isinstance(obs, dict):
                continue
            if str(obs.get("type")) != "follow":
                continue
            target = str(obs.get("target", "")).strip()
            if target in seed_ids:
                follow_out[account_id].add(target)
                follow_in[target].add(account_id)

    # undirected neighbor set for co-hood
    neighbors: dict[str, set[str]] = defaultdict(set)
    for source, targets in follow_out.items():
        for target in targets:
            neighbors[source].add(target)
            neighbors[target].add(source)

    sparse_list = sorted(
        [node_id for node_id in sparse_ids if node_id in seed_ids],
        key=lambda node_id: (
            -int((nodes.get(node_id) or {}).get("follower_count") or 0),
            node_id,
        ),
    )
    added_per_node: dict[str, int] = defaultdict(int)
    existing_pairs: set[tuple[str, str]] = set()
    for edge in _load_json_list(EDGES_FILE):
        source = str(edge.get("source", "")).strip()
        target = str(edge.get("target", "")).strip()
        if source and target:
            existing_pairs.add(tuple(sorted((source, target))))

    added = 0
    for i, left in enumerate(sparse_list):
        if added_per_node[left] >= max_new_per_node:
            continue
        left_neighbors = neighbors.get(left, set())
        if len(left_neighbors) < min_common:
            continue
        ranked: list[tuple[int, str]] = []
        for right in sparse_list[i + 1 :]:
            if added_per_node[right] >= max_new_per_node:
                continue
            common = left_neighbors & neighbors.get(right, set())
            if len(common) < min_common:
                continue
            pair = tuple(sorted((left, right)))
            if pair in existing_pairs:
                continue
            ranked.append((len(common), right))
        ranked.sort(reverse=True)
        for common_count, right in ranked:
            if added_per_node[left] >= max_new_per_node:
                break
            if added_per_node[right] >= max_new_per_node:
                continue
            pair = tuple(sorted((left, right)))
            if pair in existing_pairs:
                continue
            left_snap = snapshots_by_id.get(left)
            right_snap = snapshots_by_id.get(right)
            if not left_snap or not right_snap:
                continue
            left_url = str(left_snap.get("profile_url") or f"https://x.com/{left}")
            right_url = str(right_snap.get("profile_url") or f"https://x.com/{right}")
            desc = (
                f"Shares {common_count} solid follow-neighbors with another "
                "high-follower low-degree account (public graph densify)."
            )
            ok_left = _ensure_observation(
                left_snap,
                target=right,
                edge_type="collaboration",
                description=desc,
                source_urls=[left_url, right_url],
                confidence=min(0.58, 0.45 + 0.02 * common_count),
                review_notes=COHOOD_NOTE,
            )
            ok_right = _ensure_observation(
                right_snap,
                target=left,
                edge_type="collaboration",
                description=desc,
                source_urls=[right_url, left_url],
                confidence=min(0.58, 0.45 + 0.02 * common_count),
                review_notes=COHOOD_NOTE,
            )
            if ok_left or ok_right:
                existing_pairs.add(pair)
                added_per_node[left] += 1
                added_per_node[right] += 1
                added += int(ok_left) + int(ok_right)
    return added


def _text_tags(text: str) -> set[str]:
    lowered = text.casefold()
    tags: set[str] = set()
    for label, patterns in SCENE_TAGS:
        if any(pattern.casefold() in lowered for pattern in patterns):
            tags.add(label)
    return tags


def materialize_scene_tag_collaborations(
    snapshots_by_id: dict[str, dict[str, object]],
    seed_ids: set[str],
    sparse_ids: set[str],
    *,
    max_new_per_sparse: int = 8,
    min_shared_tags: int = 2,
) -> int:
    """Link sparse high-fl scene hubs to dense scene accounts via shared bio tags."""
    if not NODES_FILE.exists() or not EDGES_FILE.exists():
        return 0
    nodes = {str(n.get("id")): n for n in _load_json_list(NODES_FILE) if n.get("type") == "person"}
    # solid person-person degree
    solid_deg: dict[str, int] = defaultdict(int)
    existing_pairs: set[tuple[str, str]] = set()
    for edge in _load_json_list(EDGES_FILE):
        source = str(edge.get("source", "")).strip()
        target = str(edge.get("target", "")).strip()
        if not source or not target:
            continue
        existing_pairs.add(tuple(sorted((source, target))))
        if source not in nodes or target not in nodes:
            continue
        if _is_assistive_note(edge.get("review_notes")):
            continue
        if str(edge.get("type")) in {
            "follow",
            "profile_mention",
            "influence",
            "collaboration",
            "activity",
            "affiliation",
        }:
            solid_deg[source] += 1
            solid_deg[target] += 1

    tags_by_id: dict[str, set[str]] = {}
    for node_id, node in nodes.items():
        snap = snapshots_by_id.get(node_id) or {}
        text = "\n".join(
            [
                str(node.get("name") or ""),
                str(node.get("description") or ""),
                str(snap.get("summary") or ""),
                str(snap.get("profile_text") or ""),
            ]
        )
        tags = _text_tags(text)
        if tags:
            tags_by_id[node_id] = tags

    dense_ids = sorted(
        [
            node_id
            for node_id, node in nodes.items()
            if node_id in seed_ids
            and solid_deg[node_id] >= 12
            and int(node.get("follower_count") or 0) >= 300
            and len(tags_by_id.get(node_id, set())) >= 2
        ],
        key=lambda node_id: (
            -solid_deg[node_id],
            -int((nodes.get(node_id) or {}).get("follower_count") or 0),
            node_id,
        ),
    )

    added = 0
    sparse_list = sorted(
        [node_id for node_id in sparse_ids if node_id in tags_by_id and node_id in seed_ids],
        key=lambda node_id: (
            -int((nodes.get(node_id) or {}).get("follower_count") or 0),
            node_id,
        ),
    )
    for sparse_id in sparse_list:
        sparse_tags = tags_by_id.get(sparse_id, set())
        if len(sparse_tags) < min_shared_tags:
            continue
        sparse_snap = snapshots_by_id.get(sparse_id)
        if not sparse_snap:
            continue
        per_node = 0
        ranked: list[tuple[int, int, str, tuple[str, ...]]] = []
        for dense_id in dense_ids:
            if dense_id == sparse_id:
                continue
            shared = tuple(sorted(sparse_tags & tags_by_id.get(dense_id, set())))
            if len(shared) < min_shared_tags:
                continue
            pair = tuple(sorted((sparse_id, dense_id)))
            if pair in existing_pairs:
                continue
            ranked.append((len(shared), solid_deg[dense_id], dense_id, shared))
        ranked.sort(reverse=True)
        for shared_count, _dense_solid, dense_id, shared in ranked:
            if per_node >= max_new_per_sparse:
                break
            dense_snap = snapshots_by_id.get(dense_id)
            if not dense_snap:
                continue
            pair = tuple(sorted((sparse_id, dense_id)))
            if pair in existing_pairs:
                continue
            tag_label = "、".join(shared[:4])
            sparse_url = str(sparse_snap.get("profile_url") or f"https://x.com/{sparse_id}")
            dense_url = str(dense_snap.get("profile_url") or f"https://x.com/{dense_id}")
            desc = (
                f"Public profile scene tags overlap ({tag_label}) with a dense "
                "scene account (high-follower low-degree densify)."
            )
            ok = _ensure_observation(
                sparse_snap,
                target=dense_id,
                edge_type="collaboration",
                description=desc,
                source_urls=[sparse_url, dense_url],
                confidence=min(0.56, 0.42 + 0.04 * shared_count),
                review_notes=TAG_NOTE,
            )
            if ok:
                existing_pairs.add(pair)
                per_node += 1
                added += 1
    return added


def materialize_identity_name_links(
    snapshots_by_id: dict[str, dict[str, object]],
    seed_ids: set[str],
) -> int:
    """Wire bios like 'ナンパ次郎の動画垢' to the named seeded person."""
    if not NODES_FILE.exists():
        return 0
    name_to_ids: dict[str, set[str]] = defaultdict(set)
    for node in _load_json_list(NODES_FILE):
        if node.get("type") != "person":
            continue
        node_id = str(node.get("id", "")).strip()
        if not node_id or node_id not in seed_ids:
            continue
        name = str(node.get("name") or "").strip()
        if len(name) >= 4 and not name.startswith("@"):
            name_to_ids[name].add(node_id)
    unique_names = {
        name: next(iter(ids))
        for name, ids in name_to_ids.items()
        if len(ids) == 1
    }
    added = 0
    for account_id, snapshot in snapshots_by_id.items():
        if account_id not in seed_ids:
            continue
        text = "\n".join(
            str(snapshot.get(key, "") or "")
            for key in ("profile_text", "summary", "pinned_post_text")
        )
        if not any(cue in text for cue in IDENTITY_CUES):
            continue
        source_url = str(snapshot.get("profile_url") or f"https://x.com/{account_id}")
        for name, target_id in unique_names.items():
            if target_id == account_id or name not in text:
                continue
            if _ensure_observation(
                snapshot,
                target=target_id,
                edge_type="profile_mention",
                description=f"Public bio identifies this account in relation to {name}.",
                source_urls=[source_url],
                confidence=0.8,
                review_notes=IDENTITY_NOTE,
            ):
                added += 1
    return added


def materialize_sub_main_links(
    snapshots_by_id: dict[str, dict[str, object]],
    handle_map: dict[str, str],
    seed_ids: set[str],
) -> int:
    added = 0
    main_pat = re.compile(r"メイン\s*[→➡>\-:]+\s*@([A-Za-z0-9_]{2,30})")
    for row in _load_json_list(FOLLOWING_SCREENED_FILE):
        text = "\n".join(
            str(row.get(key, "") or "") for key in ("summary", "profile_text", "bio")
        )
        match = main_pat.search(text)
        if not match:
            continue
        main_id = handle_map.get(match.group(1).casefold())
        if not main_id or main_id not in seed_ids:
            continue
        sub_handle = str(row.get("handle", "")).lstrip("@")
        sub_id = str(row.get("account_id", "") or handle_to_id(sub_handle)).strip()
        if not sub_id or sub_id == main_id:
            continue
        sub_snapshot = snapshots_by_id.get(sub_id)
        if not sub_snapshot:
            sub_snapshot = {
                "account_id": sub_id,
                "profile_url": str(row.get("url") or f"https://x.com/{sub_handle}"),
                "icon_url": str(row.get("icon_url") or ""),
                "follower_count": int(row.get("follower_count") or 0),
                "profile_text": str(row.get("profile_text") or text),
                "summary": str(row.get("summary") or text.split("\n")[0]),
                "evidence_kind": "mixed",
                "needs_review": True,
                "review_notes": SUB_NOTE,
                "snapshot_origin": "generated",
                "observations": [],
                "sources": ["following-screened-sub-main"],
            }
            snapshots_by_id[sub_id] = sub_snapshot
            if sub_id not in seed_ids and sub_handle:
                with SEED_FILE.open("a", encoding="utf-8") as handle:
                    handle.write(
                        f"person|{sub_id}|{row.get('summary') or sub_handle}|{sub_handle}|real\n"
                    )
                seed_ids.add(sub_id)
        if _ensure_observation(
            sub_snapshot,
            target=main_id,
            edge_type="profile_mention",
            description=f"Sub-account bio points to main @{match.group(1)}.",
            source_urls=[str(sub_snapshot.get("profile_url") or f"https://x.com/{sub_handle}")],
            confidence=0.78,
            review_notes=SUB_NOTE,
        ):
            added += 1
        main_snapshot = snapshots_by_id.get(main_id)
        if main_snapshot and _ensure_observation(
            main_snapshot,
            target=sub_id,
            edge_type="profile_mention",
            description=f"Has public sub-account @{sub_handle}.",
            source_urls=[
                str(main_snapshot.get("profile_url") or f"https://x.com/{main_id}"),
                str(sub_snapshot.get("profile_url") or f"https://x.com/{sub_handle}"),
            ],
            confidence=0.7,
            review_notes=SUB_NOTE,
        ):
            added += 1
    return added


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Densify sparse high-follower isolation")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--write-targets-only", action="store_true")
    args = parser.parse_args()

    if args.write_targets_only:
        count = write_sparse_targets()
        print(f"[OK] wrote {count} live sparse high-follower targets -> {TARGETS_FILE}")
        return

    seed_ids = _seed_ids()
    handle_map = _handle_to_account_id()
    sparse_ids = _sparse_high_follower_ids()
    snapshots = _load_json_list(GENERATED_SNAPSHOT_FILE)
    snapshots_by_id = {
        str(row.get("account_id", "")).strip(): row
        for row in snapshots
        if str(row.get("account_id", "")).strip()
    }

    known_added = materialize_known_follows(snapshots_by_id, seed_ids, sparse_ids)
    follow_added = materialize_candidate_follows(snapshots_by_id, handle_map, seed_ids)
    mention_added = materialize_bio_mentions(snapshots_by_id, handle_map, seed_ids)
    node_mention_added = materialize_node_description_mentions(
        snapshots_by_id, handle_map, seed_ids
    )
    identity_added = materialize_identity_name_links(snapshots_by_id, seed_ids)
    sub_added = materialize_sub_main_links(snapshots_by_id, handle_map, seed_ids)
    context_added = materialize_bio_context_edges(snapshots_by_id, sparse_ids)
    cohood_added = materialize_shared_follow_neighborhood(
        snapshots_by_id,
        seed_ids,
        sparse_ids,
        min_common=2,
        max_new_per_node=12,
    )
    tag_added = materialize_scene_tag_collaborations(
        snapshots_by_id, seed_ids, sparse_ids
    )

    if not args.dry_run:
        ordered: list[dict[str, object]] = []
        seen: set[str] = set()
        for row in snapshots:
            account_id = str(row.get("account_id", "")).strip()
            if account_id and account_id in snapshots_by_id:
                ordered.append(snapshots_by_id[account_id])
                seen.add(account_id)
        for account_id, row in snapshots_by_id.items():
            if account_id not in seen:
                ordered.append(row)
        _write_json(GENERATED_SNAPSHOT_FILE, ordered)

    print(
        "[OK] "
        f"sparse_targets={len(sparse_ids)} "
        f"known_follow+={known_added} candidate_follow+={follow_added} "
        f"bio_mention+={mention_added} node_mention+={node_mention_added} "
        f"identity+={identity_added} sub_main+={sub_added} "
        f"context+={context_added} cohood+={cohood_added} "
        f"tag_collab+={tag_added} dry_run={args.dry_run}"
    )
    print("[NEXT] python build_site.py --skip-collector")


if __name__ == "__main__":
    main()
