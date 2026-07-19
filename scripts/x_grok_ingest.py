"""Ingest Grok X-integration discoveries into seed / generated snapshots.

This is the pipeline counterpart to Grok Build tools:
  - x_user_search
  - x_keyword_search
  - x_semantic_search
  - x_thread_fetch

Write discoveries to data/growth/x_grok_discoveries.json then:

  python scripts/x_grok_ingest.py --dry-run
  python scripts/x_grok_ingest.py
  python build_site.py --skip-collector
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from apply_following_screened import (  # noqa: E402
    PROMOTION_BIO_SCENE,
    X_PROFILE_FILE,
    _append_seed_rows,
    _existing_seed_values,
    _load_json_list as _load_json_list_apply,
    _merge_generated_snapshots,
    _write_json as _write_json_apply,
)
from growth_probe_candidates import BIO_SCENE, HANDLE_SCENE, handle_to_id  # noqa: E402

DISCOVERIES_FILE = ROOT / "data" / "growth" / "x_grok_discoveries.json"
GENERATED_SNAPSHOT_FILE = ROOT / "data" / "source_snapshots.generated.json"
SEED_FILE = ROOT / "seed_entities.txt"

INGEST_NOTE = "Ingested via Grok X integration tools (x_user_search / x_keyword_search / x_semantic_search)."
SCENE_BIO = re.compile(
    r"(ナンパ|ストナン|ネトナン|クラナン|講習|コンサル|経験人数|即数|路上|マッチングアプリ|"
    r"マチアプ|スト低|スト高|ストのみ|完ソロ|女修行|恋愛工学|一門|ホスト|モテ|美女|恋愛コーチ|"
    r"強オス|口説き|PUA|pua|nampa|nanpa|netonan|tinder)",
    re.IGNORECASE,
)


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


def _normalize_handle(value: object) -> str:
    return str(value or "").strip().lstrip("@")


def _normalize_discovery(row: dict[str, object]) -> dict[str, object] | None:
    handle = _normalize_handle(row.get("handle") or row.get("username") or row.get("screen_name"))
    if not handle:
        return None
    account_id = str(row.get("account_id") or handle_to_id(handle)).strip()
    bio = str(row.get("bio") or row.get("summary") or row.get("description") or "").strip()
    name = str(row.get("name") or f"@{handle}").strip()
    followers = int(row.get("followers") or row.get("follower_count") or 0)
    icon = str(row.get("icon_url") or row.get("avatar") or row.get("profile_image_url") or "").strip()
    source_tool = str(row.get("source_tool") or "x_user_search").strip()
    post_url = str(row.get("post_url") or "").strip()
    mentioned_by = [
        _normalize_handle(item)
        for item in (row.get("mentioned_by") or [])
        if _normalize_handle(item)
    ]
    mentions = [
        _normalize_handle(item)
        for item in (row.get("mentions") or [])
        if _normalize_handle(item)
    ]
    text = f"{name}\n{bio}\n{handle}"
    ok_scene = bool(
        BIO_SCENE.search(text)
        or PROMOTION_BIO_SCENE.search(text)
        or SCENE_BIO.search(text)
        or HANDLE_SCENE.search(handle)
    )
    return {
        "handle": handle,
        "account_id": account_id,
        "name": name,
        "summary": bio or f"X profile for @{handle}.",
        "profile_text": bio,
        "icon_url": icon,
        "follower_count": followers,
        "ok_bio_scene": ok_scene,
        "source_tool": source_tool,
        "post_url": post_url,
        "mentioned_by": mentioned_by,
        "mentions": mentions,
        "sources": ["grok-x-integration"],
        "screened_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "review_notes": INGEST_NOTE,
    }


def _eligible_profiles(rows: list[dict[str, object]], limit: int | None) -> list[dict[str, object]]:
    existing = _existing_seed_values()
    out: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw in rows:
        row = _normalize_discovery(raw)
        if not row or not row["ok_bio_scene"]:
            continue
        account_id = str(row["account_id"])
        handle = str(row["handle"])
        if account_id.casefold() in existing or handle.casefold() in existing:
            continue
        if account_id.casefold() in seen:
            continue
        # Require scene evidence in name/bio/handle (not handle-only regex alone).
        text = (
            f"{row.get('name', '')}\n{row.get('summary', '')}\n"
            f"{row.get('profile_text', '')}\n{row.get('handle', '')}"
        )
        if not (BIO_SCENE.search(text) or PROMOTION_BIO_SCENE.search(text) or SCENE_BIO.search(text)):
            continue
        seen.add(account_id.casefold())
        out.append(row)
    out.sort(key=lambda item: (-int(item.get("follower_count", 0) or 0), str(item["account_id"])))
    if limit is not None:
        out = out[: max(0, limit)]
    return out


def _seed_handle_to_id() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for line in SEED_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 2 or parts[0] != "person":
            continue
        account_id = parts[1]
        mapping[account_id.casefold()] = account_id
        mapping[account_id.replace("-", "_").casefold()] = account_id
        if len(parts) >= 4 and parts[3]:
            for alias in parts[3].split(","):
                normalized = alias.strip().strip("@")
                if normalized:
                    mapping[normalized.casefold()] = account_id
    return mapping


def _is_missing_or_default_icon(icon_url: object) -> bool:
    value = str(icon_url or "").strip()
    if not value:
        return True
    lowered = value.casefold()
    return (
        "/default_profile_" in lowered
        or "abs.twimg.com/sticky/default_profile_images" in lowered
        or lowered.endswith("default_profile.png")
    )


def _backfill_icons_from_discoveries(rows: list[dict[str, object]]) -> int:
    """Update icon_url on existing generated snapshots from Grok X discoveries."""
    if not GENERATED_SNAPSHOT_FILE.exists():
        return 0
    snapshots = _load_json_list(GENERATED_SNAPSHOT_FILE)
    by_id = {
        str(snapshot.get("account_id", "")).strip(): snapshot
        for snapshot in snapshots
        if str(snapshot.get("account_id", "")).strip()
    }
    handle_map = _seed_handle_to_id()
    updated = 0
    for raw in rows:
        handle = _normalize_handle(raw.get("handle") or raw.get("username"))
        if not handle:
            continue
        icon = str(raw.get("icon_url") or raw.get("avatar") or raw.get("profile_image_url") or "").strip()
        if _is_missing_or_default_icon(icon):
            continue
        # Normalize _normal -> _400x400 if present.
        icon = re.sub(r"_normal(\.(?:jpg|jpeg|png|webp))$", r"_400x400\1", icon, flags=re.IGNORECASE)
        account_id = str(raw.get("account_id") or handle_to_id(handle)).strip()
        target_ids = {
            account_id,
            handle_map.get(handle.casefold(), ""),
            handle_map.get(account_id.casefold(), ""),
            handle_map.get(account_id.replace("-", "_").casefold(), ""),
        }
        for target_id in target_ids:
            if not target_id or target_id not in by_id:
                continue
            snapshot = by_id[target_id]
            if not _is_missing_or_default_icon(snapshot.get("icon_url")):
                continue
            snapshot["icon_url"] = icon
            notes = str(snapshot.get("review_notes", "")).strip()
            snapshot["review_notes"] = " ".join(
                dict.fromkeys(
                    [notes, "Icon backfilled from Grok X integration discovery."]
                )
            ).strip()
            updated += 1
    if updated:
        _write_json(GENERATED_SNAPSHOT_FILE, snapshots)
    return updated


def _add_one_relation(
    *,
    by_id: dict[str, dict[str, object]],
    source_id: str,
    target_id: str,
    source_handle: str,
    target_handle: str,
    post_url: str,
    edge_type: str,
    note: str,
) -> bool:
    if source_id == target_id:
        return False
    snapshot = by_id.get(source_id)
    if not snapshot:
        return False
    observations = list(snapshot.get("observations") or [])
    already = any(
        isinstance(obs, dict)
        and str(obs.get("target", "")) == target_id
        and str(obs.get("type", "")) in {"profile_mention", "influence", "follow", "collaboration"}
        for obs in observations
    )
    if already:
        return False
    observations.append(
        {
            "target": target_id,
            "type": edge_type,
            "description": (
                f"Grok X integration found a public post/mention connecting "
                f"@{source_handle} to @{target_handle}."
            ),
            "source_urls": [post_url, f"https://x.com/{target_handle}"],
            "confidence": 0.78 if edge_type == "influence" else 0.72,
            "evidence_kind": "fact",
            "needs_review": False,
            "review_notes": note,
        }
    )
    snapshot["observations"] = observations
    return True


def _add_relation_observations(rows: list[dict[str, object]]) -> int:
    """Add solid profile_mention / influence edges from X post evidence.

    Supports:
      - mentioned_by: [source handles] that mention this row's handle
      - mentions: [target handles] that this row's handle mentions
      - relation_type: profile_mention | influence | collaboration
    """
    if not GENERATED_SNAPSHOT_FILE.exists():
        return 0
    snapshots = _load_json_list(GENERATED_SNAPSHOT_FILE)
    by_id = {
        str(snapshot.get("account_id", "")).strip(): snapshot
        for snapshot in snapshots
        if str(snapshot.get("account_id", "")).strip()
    }
    handle_map = _seed_handle_to_id()
    for row in rows:
        handle = str(row.get("handle", "")).strip()
        account_id = str(row.get("account_id") or handle_to_id(handle)).strip()
        if handle:
            handle_map[handle.casefold()] = account_id
        if account_id:
            handle_map[account_id.casefold()] = account_id
            handle_map[account_id.replace("-", "_").casefold()] = account_id

    added = 0
    for raw in rows:
        row = _normalize_discovery(raw) or raw
        handle = _normalize_handle(row.get("handle"))
        account_id = str(row.get("account_id") or handle_to_id(handle)).strip()
        post_url = str(row.get("post_url") or f"https://x.com/{handle}")
        edge_type = str(row.get("relation_type") or "profile_mention").strip() or "profile_mention"
        if edge_type not in {"profile_mention", "influence", "collaboration", "follow"}:
            edge_type = "profile_mention"
        note = str(row.get("review_notes") or INGEST_NOTE)

        # A) others mention this account
        for source_handle in row.get("mentioned_by") or []:
            source_handle = _normalize_handle(source_handle)
            source_id = handle_map.get(source_handle.casefold())
            target_id = handle_map.get(handle.casefold()) or account_id
            if not source_id or not target_id:
                continue
            if _add_one_relation(
                by_id=by_id,
                source_id=source_id,
                target_id=target_id,
                source_handle=source_handle,
                target_handle=handle,
                post_url=post_url,
                edge_type=edge_type,
                note=note,
            ):
                added += 1

        # B) this account mentions others
        for target_handle in row.get("mentions") or []:
            target_handle = _normalize_handle(target_handle)
            source_id = handle_map.get(handle.casefold()) or account_id
            target_id = handle_map.get(target_handle.casefold())
            if not source_id or not target_id:
                continue
            if _add_one_relation(
                by_id=by_id,
                source_id=source_id,
                target_id=target_id,
                source_handle=handle,
                target_handle=target_handle,
                post_url=post_url,
                edge_type=edge_type,
                note=note,
            ):
                added += 1

    if added:
        _write_json(GENERATED_SNAPSHOT_FILE, snapshots)
    return added


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Ingest Grok X-tool discoveries")
    parser.add_argument("--input", type=Path, default=DISCOVERIES_FILE)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    raw_rows = _load_json_list(args.input)
    eligible = _eligible_profiles(raw_rows, args.limit)
    relation_rows = [row for row in (_normalize_discovery(r) for r in raw_rows) if row]
    print(f"discoveries={len(raw_rows)} eligible={len(eligible)}")
    for row in eligible:
        print(
            f"{row['handle']}\t{row['account_id']}\t{row.get('follower_count', 0)}\t"
            f"{row.get('source_tool', '')}"
        )
    if args.dry_run:
        print(f"relation_candidates={sum(1 for r in relation_rows if r.get('mentioned_by') or r.get('mentions'))}")
        return

    promo_rows: list[dict[str, object]] = []
    if eligible:
        for row in eligible:
            promo_rows.append(
                {
                    "handle": row["handle"],
                    "account_id": row["account_id"],
                    "summary": row["summary"],
                    "profile_text": row.get("profile_text", ""),
                    "icon_url": row.get("icon_url", ""),
                    "follower_count": row.get("follower_count", 0),
                    "ok_bio_scene": True,
                    "sources": row.get("sources", ["grok-x-integration"]),
                }
            )
        _append_seed_rows(promo_rows)
        # X profile source with Grok-specific label.
        x_profiles = _load_json_list_apply(X_PROFILE_FILE)
        seen_id = {str(row.get("account_id", "")).casefold() for row in x_profiles}
        seen_url = {str(row.get("url", "")).rstrip("/").casefold() for row in x_profiles}
        for row in promo_rows:
            account_id = str(row["account_id"])
            url = f"https://x.com/{row['handle']}"
            if account_id.casefold() in seen_id or url.casefold() in seen_url:
                continue
            x_profiles.append(
                {
                    "account_id": account_id,
                    "url": url,
                    "label": "Grok X連携候補",
                }
            )
            seen_id.add(account_id.casefold())
            seen_url.add(url.casefold())
        _write_json_apply(X_PROFILE_FILE, x_profiles)
        # Snapshot merge reuses following shape (profile + optional source follows).
        _merge_generated_snapshots(promo_rows)
        # Overwrite note on new snapshots to mark Grok X origin.
        snapshots = _load_json_list(GENERATED_SNAPSHOT_FILE)
        promo_ids = {str(row["account_id"]).casefold() for row in promo_rows}
        for snapshot in snapshots:
            if str(snapshot.get("account_id", "")).casefold() not in promo_ids:
                continue
            collector = snapshot.get("collector")
            if isinstance(collector, dict):
                collector["type"] = "grok_x_integration"
                collector["note"] = INGEST_NOTE
            snapshot["review_notes"] = INGEST_NOTE
            snapshot["snapshot_origin"] = "generated"
        _write_json(GENERATED_SNAPSHOT_FILE, snapshots)

    # Backfill real icons onto existing generated snapshots / later graph rebuild.
    icon_updated = _backfill_icons_from_discoveries(raw_rows)
    # Always apply solid relations from all discoveries (including already-seeded).
    relation_added = _add_relation_observations(raw_rows + eligible)
    print(
        f"[OK] applied {len(promo_rows)} Grok X discoveries; "
        f"icons updated={icon_updated}; solid relations +{relation_added}"
    )


if __name__ == "__main__":
    main()
