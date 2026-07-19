"""Collect and screen unseeded X handles from authenticated following pages."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from collector import (  # noqa: E402
    DEFAULT_TIMEOUT,
    X_AUTH_STATE_FILE,
    collect_authenticated_following_handles,
    extract_x_handle_from_url,
    fetch_x_web_user_details,
    load_playwright_cookies,
    load_x_profile_sources,
    merge_x_web_user_details_into_snapshot,
    resolve_x_cookie_file,
    x_web_cookie_headers,
)
from growth_probe_candidates import BIO_SCENE, HANDLE_SCENE, RESERVED, handle_to_id  # noqa: E402

NODES_FILE = ROOT / "data" / "nodes.json"
SEED_FILE = ROOT / "seed_entities.txt"
GENERATED_SNAPSHOT_FILE = ROOT / "data" / "source_snapshots.generated.json"
GROWTH_DIR = ROOT / "data" / "growth"
FOLLOWING_CANDIDATES_FILE = GROWTH_DIR / "following_candidates.json"
FOLLOWING_SCREENED_FILE = GROWTH_DIR / "following_screened.json"
FOLLOWING_SOURCES_FILE = GROWTH_DIR / "following_wave_sources.json"
FOLLOWING_KNOWN_FOLLOWS_FILE = GROWTH_DIR / "following_known_follows.json"
KNOWN_FOLLOW_NOTE = (
    "Wave-collected authenticated following edge to an already-seeded account "
    "(solid density pass)."
)


def _load_json_list(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path} must contain a list")
    return [entry for entry in payload if isinstance(entry, dict)]


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _cookie_names(cookie_file: Path) -> set[str]:
    return {str(cookie.get("name", "")) for cookie in load_playwright_cookies(cookie_file)}


def _explicit_following_source_ids() -> set[str]:
    payload = json.loads((ROOT / "data" / "x_profile_sources.json").read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("data/x_profile_sources.json must contain a list")
    return {
        str(entry.get("account_id", "")).strip()
        for entry in payload
        if isinstance(entry, dict) and entry.get("enabled", True) and entry.get("collect_following") is True
    }


def _existing_handles_and_ids() -> set[str]:
    existing: set[str] = set()
    for line in SEED_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) >= 2:
            existing.add(parts[1].casefold())
        if len(parts) >= 4 and parts[3]:
            for alias in parts[3].split(","):
                normalized = alias.strip().strip("@")
                if normalized:
                    existing.add(normalized.casefold())
    for source in load_x_profile_sources():
        existing.add(str(source.get("account_id", "")).casefold())
        handle = extract_x_handle_from_url(str(source.get("url", "")))
        if handle:
            existing.add(handle.casefold())
    return existing


def _source_rows(limit: int, min_followers: int, *, rescan: bool = False) -> list[dict[str, object]]:
    x_sources = {str(source["account_id"]): source for source in load_x_profile_sources()}
    following_source_ids = _explicit_following_source_ids()
    source_log_rows = _load_json_list(FOLLOWING_SOURCES_FILE)
    scanned = {
        str(row.get("account_id", ""))
        for row in source_log_rows
        if not row.get("error")
    }
    # Prefer sources that have never completed a known-follow materialization pass.
    known_follow_pass_ids = {
        str(row.get("account_id", ""))
        for row in source_log_rows
        if not row.get("error") and "known_seed_follows" in row
    }
    nodes = json.loads(NODES_FILE.read_text(encoding="utf-8"))
    rows: list[dict[str, object]] = []
    for node in nodes:
        if not isinstance(node, dict) or node.get("type") != "person":
            continue
        account_id = str(node.get("id", ""))
        if account_id not in following_source_ids:
            continue
        source = x_sources.get(account_id)
        if not source:
            continue
        if not rescan and account_id in scanned:
            continue
        followers = int(node.get("follower_count", 0) or 0)
        if followers < min_followers:
            continue
        rows.append(
            {
                "account_id": account_id,
                "url": source["url"],
                "follower_count": followers,
                "_needs_known_follow_pass": account_id not in known_follow_pass_ids,
            }
        )
    rows.sort(
        key=lambda row: (
            # rescan: first unfinished known-follow sources, then higher followers
            0 if row.get("_needs_known_follow_pass") else 1,
            -int(row["follower_count"]),
            str(row["account_id"]),
        )
    )
    cleaned: list[dict[str, object]] = []
    for row in rows[:limit]:
        cleaned.append(
            {
                "account_id": row["account_id"],
                "url": row["url"],
                "follower_count": row["follower_count"],
            }
        )
    return cleaned


def _seed_handle_to_account_id() -> dict[str, str]:
    """Map handle/alias/id (casefold) -> canonical person account_id from seed."""
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
        if len(parts) >= 4 and parts[3]:
            for alias in parts[3].split(","):
                normalized = alias.strip().strip("@")
                if normalized:
                    mapping[normalized.casefold()] = account_id
        # id with underscores restored as handle-ish form
        mapping[account_id.replace("-", "_").casefold()] = account_id
    return mapping


def _merge_known_follow_observations(
    known_follows: list[dict[str, object]],
) -> int:
    """Attach solid follow observations for already-seeded targets into generated snapshots."""
    if not known_follows:
        return 0
    if not GENERATED_SNAPSHOT_FILE.exists():
        return 0
    snapshots = json.loads(GENERATED_SNAPSHOT_FILE.read_text(encoding="utf-8"))
    if not isinstance(snapshots, list):
        raise ValueError(f"{GENERATED_SNAPSHOT_FILE} must contain a list")
    by_id = {
        str(snapshot.get("account_id", "")).strip(): snapshot
        for snapshot in snapshots
        if isinstance(snapshot, dict) and str(snapshot.get("account_id", "")).strip()
    }
    added = 0
    for row in known_follows:
        source_id = str(row.get("source_id", "")).strip()
        target_id = str(row.get("target_id", "")).strip()
        handle = str(row.get("handle", "")).strip()
        if not source_id or not target_id or source_id == target_id:
            continue
        snapshot = by_id.get(source_id)
        if not snapshot:
            continue
        observations = list(snapshot.get("observations") or [])
        already = any(
            isinstance(observation, dict)
            and str(observation.get("target", "")).strip() == target_id
            and str(observation.get("type", "")).strip() == "follow"
            for observation in observations
        )
        if already:
            continue
        source_url = str(snapshot.get("profile_url", "") or f"https://x.com/{source_id}")
        following_url = str(row.get("following_url", "") or f"{source_url.rstrip('/')}/following")
        observations.append(
            {
                "target": target_id,
                "type": "follow",
                "description": (
                    f"Authenticated X following list shows this account follows @{handle or target_id}."
                ),
                "source_urls": [source_url, following_url],
                "confidence": 0.64,
                "evidence_kind": "mixed",
                "needs_review": True,
                "review_notes": KNOWN_FOLLOW_NOTE,
            }
        )
        snapshot["observations"] = observations
        added += 1
    if added:
        _write_json(GENERATED_SNAPSHOT_FILE, snapshots)
    return added


def _candidate_has_allowed_source(row: dict[str, object], allowed_source_ids: set[str]) -> bool:
    return bool({str(source) for source in row.get("sources", [])} & allowed_source_ids)


def _merge_candidates(rows: list[dict[str, object]], allowed_source_ids: set[str]) -> list[dict[str, object]]:
    merged: dict[str, dict[str, object]] = {}
    for row in _load_json_list(FOLLOWING_CANDIDATES_FILE):
        account_id = str(row.get("account_id", "")).strip()
        if not _candidate_has_allowed_source(row, allowed_source_ids):
            continue
        if account_id:
            merged[account_id] = {
                **row,
                "sources": sorted({str(source) for source in row.get("sources", [])}),
            }
    for row in rows:
        account_id = str(row["account_id"])
        existing = merged.setdefault(
            account_id,
            {
                "handle": row["handle"],
                "account_id": account_id,
                "sources": [],
                "scene_handle": bool(HANDLE_SCENE.search(str(row["handle"]))),
            },
        )
        sources = {str(source) for source in existing.get("sources", [])}
        sources.update(str(source) for source in row.get("sources", []))
        existing["sources"] = sorted(sources)
        existing["source_count"] = len(sources)
    return sorted(
        merged.values(),
        key=lambda row: (
            -int(row.get("source_count", len(row.get("sources", []))) or 0),
            not bool(row.get("scene_handle")),
            str(row.get("account_id", "")),
        ),
    )


def _screen_candidates(
    candidates: list[dict[str, object]],
    *,
    cookie_file: Path,
    limit: int,
    timeout: int,
    pause_seconds: float,
) -> list[dict[str, object]]:
    headers = x_web_cookie_headers(cookie_file)
    screened_by_id = {
        str(row.get("account_id", "")): row for row in _load_json_list(FOLLOWING_SCREENED_FILE)
    }
    existing = _existing_handles_and_ids()
    rows: list[dict[str, object]] = []
    tried = 0
    for candidate in candidates:
        if tried >= limit:
            break
        account_id = str(candidate.get("account_id", "")).strip()
        handle = str(candidate.get("handle", "")).strip()
        if not account_id or not handle or account_id in screened_by_id:
            continue
        if account_id.casefold() in existing or handle.casefold() in existing:
            continue
        tried += 1
        row = {
            **candidate,
            "url": f"https://x.com/{handle}",
            "screened_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        try:
            details = fetch_x_web_user_details(handle, headers, timeout=timeout)
            if not details:
                row["ok_bio_scene"] = False
                row["error"] = "empty_or_unavailable"
            else:
                snapshot = {
                    "account_id": account_id,
                    "profile_url": f"https://x.com/{handle}",
                    "links": [f"https://x.com/{handle}"],
                    "summary": f"X profile for @{handle}.",
                    "profile_text": f"@{handle}",
                    "follower_count": 0,
                    "review_notes": "",
                }
                merged = merge_x_web_user_details_into_snapshot(snapshot, details)
                text = f"{merged.get('summary', '')}\n{merged.get('profile_text', '')}"
                row.update(
                    {
                        "fetched_url": merged.get("profile_url", f"https://x.com/{handle}"),
                        "ok_bio_scene": bool(BIO_SCENE.search(text) or candidate.get("scene_handle")),
                        "summary": merged.get("summary", ""),
                        "profile_text": merged.get("profile_text", ""),
                        "icon_url": merged.get("icon_url", ""),
                        "follower_count": int(merged.get("follower_count", 0) or 0),
                    }
                )
                print(
                    f"[OK] screened @{handle}: followers={row['follower_count']} "
                    f"scene={row['ok_bio_scene']}"
                )
        except Exception as exc:
            row["ok_bio_scene"] = False
            row["error"] = f"{type(exc).__name__}: {exc}"
            print(f"[WARN] screened @{handle}: {row['error']}")
        rows.append(row)
        if pause_seconds > 0:
            time.sleep(pause_seconds)
    screened_by_id.update({str(row["account_id"]): row for row in rows})
    return sorted(screened_by_id.values(), key=lambda row: str(row.get("account_id", "")))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Authenticated following growth wave")
    parser.add_argument("--cookie-file", type=Path, default=None)
    parser.add_argument("--auth-state", type=Path, default=X_AUTH_STATE_FILE)
    parser.add_argument("--source-limit", type=int, default=8)
    parser.add_argument("--following-limit", type=int, default=80)
    parser.add_argument("--screen-limit", type=int, default=40)
    parser.add_argument("--min-source-followers", type=int, default=3000)
    parser.add_argument("--pause-seconds", type=float, default=0.25)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument(
        "--rescan",
        action="store_true",
        help="Re-collect following for already-scanned sources (useful for solid known-follow edges).",
    )
    parser.add_argument(
        "--skip-screen",
        action="store_true",
        help="Skip public profile screening (still collect following and known solid follows).",
    )
    args = parser.parse_args()

    cookie_file = resolve_x_cookie_file(args.cookie_file)
    if cookie_file is None or not cookie_file.exists():
        raise FileNotFoundError("Missing cookie file for following growth wave.")

    cookie_names = _cookie_names(cookie_file)
    existing = _existing_handles_and_ids()
    handle_to_account_id = _seed_handle_to_account_id()
    grouped: dict[str, dict[str, object]] = {}
    known_follows: list[dict[str, object]] = _load_json_list(FOLLOWING_KNOWN_FOLLOWS_FILE)
    known_follow_keys = {
        (str(row.get("source_id", "")), str(row.get("target_id", "")))
        for row in known_follows
    }
    allowed_source_ids = _explicit_following_source_ids()
    source_log = [
        row for row in _load_json_list(FOLLOWING_SOURCES_FILE)
        if str(row.get("account_id", "")) in allowed_source_ids
    ]
    if args.rescan:
        rescan_ids = {
            str(row["account_id"])
            for row in _source_rows(
                args.source_limit,
                args.min_source_followers,
                rescan=True,
            )
        }
        source_log = [
            row for row in source_log if str(row.get("account_id", "")) not in rescan_ids
        ]
    if not args.auth_state.exists() and "auth_token" not in cookie_names:
        print(
            f"[WARN] {cookie_file} has no auth_token and {args.auth_state} does not exist; "
            "skipping authenticated following collection."
        )
        sources: list[dict[str, object]] = []
    else:
        sources = _source_rows(
            args.source_limit,
            args.min_source_followers,
            rescan=args.rescan,
        )
    for source in sources:
        source_id = str(source["account_id"])
        try:
            handles, following_url = collect_authenticated_following_handles(
                str(source["url"]),
                auth_state_path=args.auth_state,
                cookie_file_path=cookie_file,
                limit=max(1, args.following_limit),
            )
        except Exception as exc:
            source_log.append(
                {
                    **source,
                    "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            print(f"[WARN] following {source_id}: {exc}")
            continue
        added = 0
        known_added = 0
        for handle in handles:
            account_id = handle_to_id(handle)
            if handle.casefold() in RESERVED or account_id.casefold() in RESERVED:
                continue
            seed_target = handle_to_account_id.get(handle.casefold()) or handle_to_account_id.get(
                account_id.casefold()
            )
            if seed_target and seed_target != source_id:
                key = (source_id, seed_target)
                if key not in known_follow_keys:
                    known_follows.append(
                        {
                            "source_id": source_id,
                            "target_id": seed_target,
                            "handle": handle,
                            "following_url": following_url,
                            "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        }
                    )
                    known_follow_keys.add(key)
                    known_added += 1
                continue
            if account_id.casefold() in existing or handle.casefold() in existing:
                continue
            row = grouped.setdefault(
                account_id,
                {"handle": handle, "account_id": account_id, "sources": []},
            )
            row["sources"].append(source_id)
            added += 1
        source_log.append(
            {
                **source,
                "following_url": following_url,
                "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "handles_seen": len(handles),
                "new_unseeded_handles": added,
                "known_seed_follows": known_added,
            }
        )
        print(
            f"[OK] following {source_id}: seen={len(handles)} "
            f"new_unseeded={added} known_seed_follows={known_added}"
        )

    candidates = _merge_candidates(list(grouped.values()), allowed_source_ids)
    candidate_ids = {str(row.get("account_id", "")) for row in candidates}
    _write_json(FOLLOWING_CANDIDATES_FILE, candidates)
    _write_json(FOLLOWING_SOURCES_FILE, source_log)
    _write_json(FOLLOWING_KNOWN_FOLLOWS_FILE, known_follows)
    solid_follow_obs = _merge_known_follow_observations(known_follows)
    if solid_follow_obs:
        print(f"[OK] solid known-follow observations added: +{solid_follow_obs}")
    screened = _load_json_list(FOLLOWING_SCREENED_FILE)
    if not args.skip_screen and args.screen_limit > 0 and candidates:
        if {"auth_token", "ct0"}.issubset(cookie_names):
            screened = _screen_candidates(
                candidates,
                cookie_file=cookie_file,
                limit=args.screen_limit,
                timeout=args.timeout,
                pause_seconds=args.pause_seconds,
            )
            _write_json(FOLLOWING_SCREENED_FILE, screened)
        else:
            print(f"[WARN] {cookie_file} has no auth_token/ct0 pair; skipping X Web profile screening.")
    ok_count = sum(1 for row in screened if row.get("ok_bio_scene"))
    print(
        f"[OK] candidates={len(candidates)} screened={len(screened)} "
        f"ok_bio_scene={ok_count} known_follows={len(known_follows)}"
    )


if __name__ == "__main__":
    main()
