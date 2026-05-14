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
    load_x_profile_sources,
    merge_x_web_user_details_into_snapshot,
    resolve_x_cookie_file,
    x_web_cookie_headers,
)
from growth_probe_candidates import BIO_SCENE, HANDLE_SCENE, RESERVED, handle_to_id  # noqa: E402

NODES_FILE = ROOT / "data" / "nodes.json"
SEED_FILE = ROOT / "seed_entities.txt"
GROWTH_DIR = ROOT / "data" / "growth"
FOLLOWING_CANDIDATES_FILE = GROWTH_DIR / "following_candidates.json"
FOLLOWING_SCREENED_FILE = GROWTH_DIR / "following_screened.json"
FOLLOWING_SOURCES_FILE = GROWTH_DIR / "following_wave_sources.json"


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


def _source_rows(limit: int, min_followers: int) -> list[dict[str, object]]:
    x_sources = {str(source["account_id"]): source for source in load_x_profile_sources()}
    scanned = {
        str(row.get("account_id", ""))
        for row in _load_json_list(FOLLOWING_SOURCES_FILE)
        if not row.get("error")
    }
    nodes = json.loads(NODES_FILE.read_text(encoding="utf-8"))
    rows: list[dict[str, object]] = []
    for node in nodes:
        if not isinstance(node, dict) or node.get("type") != "person":
            continue
        account_id = str(node.get("id", ""))
        source = x_sources.get(account_id)
        if not source or account_id in scanned:
            continue
        followers = int(node.get("follower_count", 0) or 0)
        if followers < min_followers:
            continue
        rows.append(
            {
                "account_id": account_id,
                "url": source["url"],
                "follower_count": followers,
            }
        )
    rows.sort(key=lambda row: (-int(row["follower_count"]), str(row["account_id"])))
    return rows[:limit]


def _merge_candidates(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    merged: dict[str, dict[str, object]] = {}
    for row in _load_json_list(FOLLOWING_CANDIDATES_FILE):
        account_id = str(row.get("account_id", "")).strip()
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
    args = parser.parse_args()

    cookie_file = resolve_x_cookie_file(args.cookie_file)
    if cookie_file is None or not cookie_file.exists():
        raise FileNotFoundError("Missing cookie file for following growth wave.")

    existing = _existing_handles_and_ids()
    grouped: dict[str, dict[str, object]] = {}
    source_log = _load_json_list(FOLLOWING_SOURCES_FILE)
    for source in _source_rows(args.source_limit, args.min_source_followers):
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
        for handle in handles:
            account_id = handle_to_id(handle)
            if handle.casefold() in RESERVED or account_id.casefold() in RESERVED:
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
            }
        )
        print(f"[OK] following {source_id}: seen={len(handles)} new_unseeded={added}")

    candidates = _merge_candidates(list(grouped.values()))
    _write_json(FOLLOWING_CANDIDATES_FILE, candidates)
    _write_json(FOLLOWING_SOURCES_FILE, source_log)
    screened = _screen_candidates(
        candidates,
        cookie_file=cookie_file,
        limit=max(0, args.screen_limit),
        timeout=args.timeout,
        pause_seconds=args.pause_seconds,
    )
    _write_json(FOLLOWING_SCREENED_FILE, screened)
    ok_count = sum(1 for row in screened if row.get("ok_bio_scene"))
    print(f"[OK] candidates={len(candidates)} screened={len(screened)} ok_bio_scene={ok_count}")


if __name__ == "__main__":
    main()
