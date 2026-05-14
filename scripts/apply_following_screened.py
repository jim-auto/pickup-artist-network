"""Promote screened following-growth profiles into seeds and generated snapshots."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(ROOT))

SEED_FILE = ROOT / "seed_entities.txt"
X_PROFILE_FILE = ROOT / "data" / "x_profile_sources.json"
GENERATED_SNAPSHOT_FILE = ROOT / "data" / "source_snapshots.generated.json"
FOLLOWING_SCREENED_FILE = ROOT / "data" / "growth" / "following_screened.json"

X_PROFILE_LABEL = "following由来候補（screened）"
NOTE = "Following-guided public profile screening from data/growth/following_screened.json."


def _load_json_list(path: Path) -> list[dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path} must contain a list")
    return [entry for entry in payload if isinstance(entry, dict)]


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _existing_seed_values() -> set[str]:
    values: set[str] = set()
    for line in SEED_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) >= 2:
            values.add(parts[1].casefold())
        if len(parts) >= 4 and parts[3]:
            for alias in parts[3].split(","):
                normalized = alias.strip().strip("@")
                if normalized:
                    values.add(normalized.casefold())
    return values


def _screened_rows(limit: int | None) -> list[dict[str, object]]:
    existing = _existing_seed_values()
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in _load_json_list(FOLLOWING_SCREENED_FILE):
        account_id = str(row.get("account_id", "")).strip()
        handle = str(row.get("handle", "")).strip()
        if not account_id or not handle or not row.get("ok_bio_scene"):
            continue
        if account_id.casefold() in existing or handle.casefold() in existing:
            continue
        if account_id.casefold() in seen:
            continue
        seen.add(account_id.casefold())
        rows.append(row)
    rows.sort(key=lambda row: (-int(row.get("follower_count", 0) or 0), str(row["account_id"])))
    if limit is not None:
        rows = rows[: max(0, limit)]
    return rows


def _append_seed_rows(rows: list[dict[str, object]]) -> None:
    text = SEED_FILE.read_text(encoding="utf-8")
    lines = text.splitlines()
    insert_at = next((i for i, line in enumerate(lines) if line.startswith("community|")), len(lines))
    block = [
        f"person|{row['account_id']}|@{row['handle']}|{row['handle']}|real"
        for row in rows
    ]
    SEED_FILE.write_text(
        "\n".join(lines[:insert_at] + block + lines[insert_at:]) + ("\n" if text.endswith("\n") else ""),
        encoding="utf-8",
    )


def _append_x_profile_rows(rows: list[dict[str, object]]) -> None:
    data = _load_json_list(X_PROFILE_FILE)
    seen_id = {str(row.get("account_id", "")).casefold() for row in data}
    seen_url = {str(row.get("url", "")).rstrip("/").casefold() for row in data}
    for row in rows:
        account_id = str(row["account_id"])
        url = f"https://x.com/{row['handle']}"
        if account_id.casefold() in seen_id or url.casefold() in seen_url:
            continue
        data.append({"account_id": account_id, "url": url, "label": X_PROFILE_LABEL})
        seen_id.add(account_id.casefold())
        seen_url.add(url.casefold())
    _write_json(X_PROFILE_FILE, data)


def _snapshot_for_row(row: dict[str, object]) -> dict[str, object]:
    handle = str(row["handle"])
    account_id = str(row["account_id"])
    source_url = f"https://x.com/{handle}"
    sources = [str(source) for source in row.get("sources", []) if str(source).strip()]
    observations: list[dict[str, object]] = []
    if "マチアプ" in str(row.get("summary", "")) or "マッチングアプリ" in str(row.get("summary", "")):
        observations.append(
            {
                "target": "matching-apps",
                "type": "activity",
                "description": "Public X profile mentions matching-app activity.",
                "source_urls": [source_url],
                "confidence": 0.68,
                "evidence_kind": "fact",
                "needs_review": True,
                "review_notes": NOTE,
            }
        )
    return {
        "account_id": account_id,
        "profile_url": source_url,
        "pinned_post_url": "",
        "icon_url": str(row.get("icon_url", "")),
        "profile_text": str(row.get("profile_text", "")),
        "pinned_post_text": "",
        "links": [source_url],
        "summary": str(row.get("summary", "")),
        "evidence_kind": "fact",
        "needs_review": True,
        "review_notes": f"{NOTE} Sources following this account: {', '.join(sources)}.",
        "summary_evidence_kind": "fact",
        "snapshot_origin": "generated",
        "collector": {
            "type": "following_screened_profile",
            "source_url": source_url,
            "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
        "observations": observations,
        "follower_count": int(row.get("follower_count", 0) or 0),
    }


def _merge_generated_snapshots(rows: list[dict[str, object]]) -> None:
    snapshots = _load_json_list(GENERATED_SNAPSHOT_FILE)
    seen_ids = {str(snapshot.get("account_id", "")).casefold() for snapshot in snapshots}
    for row in rows:
        if str(row["account_id"]).casefold() not in seen_ids:
            snapshots.append(_snapshot_for_row(row))
            seen_ids.add(str(row["account_id"]).casefold())

    row_by_id = {str(row["account_id"]): row for row in rows}
    for snapshot in snapshots:
        source_id = str(snapshot.get("account_id", ""))
        if not source_id:
            continue
        observations = list(snapshot.get("observations", []))
        changed = False
        for row in rows:
            target_id = str(row["account_id"])
            sources = {str(source) for source in row.get("sources", [])}
            if source_id not in sources:
                continue
            observation = {
                "target": target_id,
                "type": "follow",
                "description": f"Authenticated X following list shows this account follows @{row['handle']}.",
                "source_urls": [str(snapshot.get("profile_url", "")), f"https://x.com/{row['handle']}"],
                "confidence": 0.64,
                "evidence_kind": "mixed",
                "needs_review": True,
                "review_notes": NOTE,
            }
            if observation not in observations:
                observations.append(observation)
                changed = True
        if changed:
            snapshot["observations"] = observations

    _write_json(GENERATED_SNAPSHOT_FILE, snapshots)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Apply following-screened growth rows")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    rows = _screened_rows(args.limit)
    print(f"apply_candidates={len(rows)}")
    for row in rows:
        print(f"{row['handle']}\t{row['account_id']}\t{row.get('follower_count', 0)}")
    if args.dry_run or not rows:
        return
    _append_seed_rows(rows)
    _append_x_profile_rows(rows)
    _merge_generated_snapshots(rows)
    print(f"[OK] applied {len(rows)} following-screened profiles")


if __name__ == "__main__":
    main()
