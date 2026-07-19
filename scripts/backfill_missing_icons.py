"""Backfill missing/default X profile icons into generated snapshots.

Uses authenticated UserByScreenName (same path as collector --refresh-missing-x-web-profiles)
and rejects sticky/default avatars as "not real icons".

Examples:
  python scripts/backfill_missing_icons.py --limit 50 --retry-skips
  python scripts/backfill_missing_icons.py --limit 100
  python build_site.py --skip-collector
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(ROOT))

from collector import (  # noqa: E402
    GENERATED_SNAPSHOT_FILE,
    X_PROFILE_CONFIG,
    X_WEB_PROFILE_SKIP_FILE,
    refresh_missing_x_web_profiles,
)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Backfill missing X profile icons")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--pause-seconds", type=float, default=0.35)
    parser.add_argument("--retry-skips", action="store_true")
    parser.add_argument("--output", type=Path, default=GENERATED_SNAPSHOT_FILE)
    args = parser.parse_args()

    refreshed = refresh_missing_x_web_profiles(
        x_profile_config_path=X_PROFILE_CONFIG,
        output_path=args.output,
        limit=args.limit,
        pause_seconds=args.pause_seconds,
        skip_file_path=X_WEB_PROFILE_SKIP_FILE,
        retry_skipped=args.retry_skips,
    )
    with_icon = sum(1 for row in refreshed if str(row.get("icon_url", "")).strip())
    print(f"[OK] refreshed={len(refreshed)} with_real_icon={with_icon} -> {args.output}")
    print("[NEXT] python build_site.py --skip-collector")


if __name__ == "__main__":
    main()
