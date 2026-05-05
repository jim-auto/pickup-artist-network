"""芋づる式拡張: スナップショット JSON から X ハンドルを拾い、未シードを seed / x_profile に追記する."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from growth_probe_candidates import AT, HANDLE_SCENE, RESERVED, X_URL, handle_to_id

SEED_FILE = ROOT / "seed_entities.txt"
NODES_FILE = ROOT / "data" / "nodes.json"
X_PROFILE_FILE = ROOT / "data" / "x_profile_sources.json"
GENERATED_SNAPSHOT = ROOT / "data" / "source_snapshots.generated.json"
MANUAL_SNAPSHOT = ROOT / "data" / "source_snapshots.json"
REVIEW_CANDIDATES = ROOT / "data" / "review_candidates.json"

BANNED_PREFIX = ("city_", "twitter", "x.com")

X_PROFILE_LABEL = "芋づる式（スナップショット言及）"


def _load_blobs(include_review: bool) -> list[str]:
    out: list[str] = []
    for path in (GENERATED_SNAPSHOT, MANUAL_SNAPSHOT):
        if path.exists():
            out.append(path.read_text(encoding="utf-8"))
    if include_review and REVIEW_CANDIDATES.exists():
        out.append(REVIEW_CANDIDATES.read_text(encoding="utf-8"))
    return out


def _seed_alias_node_sets() -> tuple[set[str], set[str]]:
    seed_ids: set[str] = set()
    aliases_cf: set[str] = set()
    for line in SEED_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [x.strip() for x in line.split("|")]
        if len(parts) < 3:
            continue
        seed_ids.add(parts[1].casefold())
        if len(parts) >= 4 and parts[3]:
            for alias in parts[3].split(","):
                a = alias.strip().strip("@")
                if a:
                    aliases_cf.add(a.casefold())
    cf_ids = seed_ids | aliases_cf
    nodes: set[str] = set()
    if NODES_FILE.exists():
        nodes = {
            n["id"].casefold()
            for n in json.loads(NODES_FILE.read_text(encoding="utf-8"))
            if n.get("type") == "person"
        }
    return cf_ids, nodes


def _handle_counts(blobs: list[str]) -> Counter[str]:
    ctr: Counter[str] = Counter()
    for blob in blobs:
        for m in X_URL.finditer(blob):
            ctr[m.group(1)] += 1
        for m in AT.finditer(blob):
            ctr[m.group(1)] += 1
    return ctr


def iter_candidates(
    *,
    min_count_scene: int,
    min_count_other: int,
    include_review: bool,
    scene_only: bool,
) -> list[tuple[int, str, str]]:
    """Return (count, handle, entity_id) sorted for stable output."""
    cf_ids, nodes = _seed_alias_node_sets()
    ctr = _handle_counts(_load_blobs(include_review))
    out: list[tuple[int, str, str]] = []
    for h, c in ctr.items():
        hid = handle_to_id(h)
        if h.casefold() in RESERVED or hid.casefold() in RESERVED:
            continue
        if any(h.casefold().startswith(p) for p in BANNED_PREFIX):
            continue
        if hid.casefold() in cf_ids or h.casefold() in cf_ids:
            continue
        if hid.casefold() in nodes:
            continue
        if len(h) < 2 or len(h) > 15:
            continue
        scene = bool(HANDLE_SCENE.search(h))
        if scene_only and not scene:
            continue
        need = min_count_scene if scene else min_count_other
        if c < need:
            continue
        out.append((c, h, hid))
    out.sort(key=lambda t: (-t[0], t[2].lower(), t[1].lower()))
    return out


def _existing_seed_ids() -> set[str]:
    ids: set[str] = set()
    for line in SEED_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [x.strip() for x in line.split("|")]
        if len(parts) >= 2 and parts[0] == "person":
            ids.add(parts[1].casefold())
    return ids


def _insert_person_rows(person_lines: list[str]) -> None:
    text = SEED_FILE.read_text(encoding="utf-8")
    lines = text.splitlines()
    insert_at = next((i for i, ln in enumerate(lines) if ln.startswith("community|")), len(lines))
    block = person_lines
    new_lines = lines[:insert_at] + block + lines[insert_at:]
    SEED_FILE.write_text("\n".join(new_lines) + ("\n" if text.endswith("\n") else ""), encoding="utf-8")


def _append_x_profile_rows(rows: list[dict[str, str]]) -> None:
    data = json.loads(X_PROFILE_FILE.read_text(encoding="utf-8"))
    seen_url = {str(r["url"]).rstrip("/").casefold() for r in data}
    seen_id = {str(r["account_id"]).casefold() for r in data}
    for row in rows:
        url_cf = str(row["url"]).rstrip("/").casefold()
        aid = str(row["account_id"]).casefold()
        if url_cf in seen_url or aid in seen_id:
            continue
        seen_url.add(url_cf)
        seen_id.add(aid)
        data.append(row)
    X_PROFILE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="芋づる式: スナップショットから未シード X を追記")
    ap.add_argument(
        "--apply",
        action="store_true",
        help="seed_entities.txt と x_profile_sources.json を更新する（既定はドライラン）",
    )
    ap.add_argument(
        "--include-review-candidates",
        action="store_true",
        help="data/review_candidates.json もスキャンする（ノイズ増の可能性あり）",
    )
    ap.add_argument(
        "--min-count-scene",
        type=int,
        default=1,
        help="ハンドルがシーン由来パターンにマッチするときの最小言及回数（既定: 1）",
    )
    ap.add_argument(
        "--min-count-other",
        type=int,
        default=3,
        help="シーン非マッチ時の最小言及回数（既定: 3。短い汎用ハンドルの誤検出を抑える）",
    )
    ap.add_argument(
        "--scene-only",
        action="store_true",
        help="シーン由来ハンドルパターンにマッチしたものだけ（より保守的）",
    )
    args = ap.parse_args()

    cands = iter_candidates(
        min_count_scene=args.min_count_scene,
        min_count_other=args.min_count_other,
        include_review=args.include_review_candidates,
        scene_only=args.scene_only,
    )
    print(f"candidates: {len(cands)} (blobs={len(_load_blobs(args.include_review_candidates))})")
    for c, h, hid in cands[:80]:
        print(f"{c}\t{h}\t{hid}")

    if not args.apply or not cands:
        return

    existing = _existing_seed_ids()
    seed_lines: list[str] = []
    x_rows: list[dict[str, str]] = []
    for _c, h, hid in cands:
        if hid.casefold() in existing:
            continue
        existing.add(hid.casefold())
        seed_lines.append(f"person|{hid}|@{h}|{h}|real")
        x_rows.append(
            {
                "account_id": hid,
                "url": f"https://x.com/{h}",
                "label": X_PROFILE_LABEL,
            }
        )

    if not seed_lines:
        print("apply: nothing new (all already in seeds)")
        return

    _insert_person_rows(seed_lines)
    _append_x_profile_rows(x_rows)
    print(f"apply: +{len(seed_lines)} persons / x_profile rows")


if __name__ == "__main__":
    main()
