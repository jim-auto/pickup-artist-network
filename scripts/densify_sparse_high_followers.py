"""Materialize missing solid edges that leave high-follower people looking isolated.

Sources:
  - data/growth/following_candidates.json  (seed -> seed follows not yet in snapshots)
  - person bios / aliases containing @handles of other seeded people
  - explicit high-value sub/main account links (e.g. K@M sub)

Run:
  python scripts/densify_sparse_high_followers.py
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

from growth_probe_candidates import handle_to_id  # noqa: E402

GENERATED_SNAPSHOT_FILE = ROOT / "data" / "source_snapshots.generated.json"
FOLLOWING_CANDIDATES_FILE = ROOT / "data" / "growth" / "following_candidates.json"
FOLLOWING_SCREENED_FILE = ROOT / "data" / "growth" / "following_screened.json"
SEED_FILE = ROOT / "seed_entities.txt"
NODES_FILE = ROOT / "data" / "nodes.json"

FOLLOW_NOTE = (
    "Densify pass: authenticated following candidate materialised as solid follow "
    "between already-seeded accounts (high-follower isolation fix)."
)
MENTION_NOTE = (
    "Densify pass: public profile/bio @handle mention of an already-seeded account."
)
SUB_NOTE = (
    "Densify pass: sub/main account link from public bio (メイン→@handle)."
)
HANDLE_RE = re.compile(r"@([A-Za-z0-9_]{2,30})")


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
    # Prefer generated snapshot aliases / profile handles when present.
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


def materialize_sub_main_links(
    snapshots_by_id: dict[str, dict[str, object]],
    handle_map: dict[str, str],
    seed_ids: set[str],
) -> int:
    """Promote screened sub-accounts that point at a seeded main with メイン→@handle."""
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
        # Ensure sub snapshot exists (lightweight) so graph can include the link from main.
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
                # Append seed only if missing; keep real-person flag.
                with SEED_FILE.open("a", encoding="utf-8") as handle:
                    handle.write(
                        f"person|{sub_id}|{row.get('summary') or sub_handle}|{sub_handle}|real\n"
                    )
                seed_ids.add(sub_id)
        # Sub -> main mention and main -> sub affiliation-like mention.
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


def refresh_high_follower_profiles(
    snapshots_by_id: dict[str, dict[str, object]],
) -> int:
    """Refresh a few known high-follower hubs with fresher public profile text."""
    # K@Mの王 currently brands as マチアプの王; keep the edge graph and update summary.
    updates = {
        "k-suto-nan": {
            "summary": "K@マチアプの王 / マチアプで毎日遊んでる人",
            "profile_text": (
                "K@マチアプの王 (@K_suto_nan)\n"
                "マチアプで毎日遊んでる人\n"
                "Location: 路上ゴキブリニキ"
            ),
            "follower_count": 3008,
            "icon_url": "https://pbs.twimg.com/profile_images/1559207028425629696/-4K4mxsX_400x400.jpg",
        }
    }
    changed = 0
    for account_id, payload in updates.items():
        snapshot = snapshots_by_id.get(account_id)
        if not snapshot:
            continue
        for key, value in payload.items():
            if snapshot.get(key) != value:
                snapshot[key] = value
                changed += 1
        notes = str(snapshot.get("review_notes", "") or "").strip()
        marker = "High-follower hub profile refreshed for densify pass."
        if marker not in notes:
            snapshot["review_notes"] = f"{notes} {marker}".strip()
            changed += 1
        snapshot["screened_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return changed


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Densify sparse high-follower isolation")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    seed_ids = _seed_ids()
    handle_map = _handle_to_account_id()
    snapshots = _load_json_list(GENERATED_SNAPSHOT_FILE)
    snapshots_by_id = {
        str(row.get("account_id", "")).strip(): row
        for row in snapshots
        if str(row.get("account_id", "")).strip()
    }

    follow_added = materialize_candidate_follows(snapshots_by_id, handle_map, seed_ids)
    mention_added = materialize_bio_mentions(snapshots_by_id, handle_map, seed_ids)
    sub_added = materialize_sub_main_links(snapshots_by_id, handle_map, seed_ids)
    refreshed = refresh_high_follower_profiles(snapshots_by_id)

    if not args.dry_run:
        # Preserve original order; append any new snapshots at end.
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
        f"[OK] follow+={follow_added} mention+={mention_added} "
        f"sub_main+={sub_added} profile_fields_touched={refreshed} dry_run={args.dry_run}"
    )
    print("[NEXT] python build_site.py --skip-collector")


if __name__ == "__main__":
    main()
