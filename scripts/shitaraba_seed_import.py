"""公開したらばアーカイブ HTML から X ハンドルを抽出し person シードを増やす（X 認証不要）."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from growth_probe_candidates import AT, RESERVED, X_URL, handle_to_id

SEED_FILE = ROOT / "seed_entities.txt"
X_PROFILE_FILE = ROOT / "data" / "x_profile_sources.json"

DEFAULT_URL = (
    "https://jbbs.shitaraba.net/bbs/read_archive.cgi/internet/23860/1699109412/-100"
)
USER_AGENT = "pickup-artist-network-shitaraba-import/0.1 (+local research)"

LABEL = "したらば公開アーカイブ（無認証・208）"


def _seed_and_alias_cf() -> set[str]:
    cf: set[str] = set()
    for line in SEED_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [x.strip() for x in line.split("|")]
        if len(parts) < 3:
            continue
        cf.add(parts[1].casefold())
        if len(parts) >= 4 and parts[3]:
            for alias in parts[3].split(","):
                a = alias.strip().strip("@")
                if a:
                    cf.add(a.casefold())
    return cf


def _extract_handles(html: str) -> set[str]:
    found: set[str] = set()
    for rx in (X_URL, AT):
        for m in rx.finditer(html):
            found.add(m.group(1))
    return found


def _filter_handles(handles: set[str], existing_cf: set[str]) -> list[tuple[str, str]]:
    """Return sorted (handle, entity_id) to add. One row per entity id (hid)."""
    by_hid: dict[str, tuple[str, str]] = {}
    banned_prefix = ("city_", "twitter", "x.com")
    for h in handles:
        hid = handle_to_id(h)
        if len(h) < 2 or len(h) > 15:
            continue
        if h.casefold() in RESERVED or hid.casefold() in RESERVED:
            continue
        if any(h.casefold().startswith(p) for p in banned_prefix):
            continue
        if hid.casefold() in existing_cf or h.casefold() in existing_cf:
            continue
        hkey = hid.casefold()
        if hkey in by_hid:
            continue
        by_hid[hkey] = (h, hid)
    out = list(by_hid.values())
    out.sort(key=lambda t: (t[1].lower(), t[0].lower()))
    return out


def _insert_persons(rows: list[tuple[str, str]]) -> None:
    text = SEED_FILE.read_text(encoding="utf-8")
    lines = text.splitlines()
    insert_at = next((i for i, ln in enumerate(lines) if ln.startswith("community|")), len(lines))
    block = [f"person|{hid}|@{h}|{h}|real" for h, hid in rows]
    new_lines = lines[:insert_at] + block + lines[insert_at:]
    SEED_FILE.write_text("\n".join(new_lines) + ("\n" if text.endswith("\n") else ""), encoding="utf-8")


def _append_x_profiles(rows: list[tuple[str, str]]) -> None:
    data = json.loads(X_PROFILE_FILE.read_text(encoding="utf-8"))
    seen_url = {str(r["url"]).rstrip("/").casefold() for r in data}
    seen_id = {str(r["account_id"]).casefold() for r in data}
    for h, hid in rows:
        url = f"https://x.com/{h}"
        ucf = url.rstrip("/").casefold()
        if ucf in seen_url or hid.casefold() in seen_id:
            continue
        seen_url.add(ucf)
        seen_id.add(hid.casefold())
        data.append({"account_id": hid, "url": url, "label": LABEL})
    X_PROFILE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="したらば公開スレから X ハンドルをシードに追加")
    ap.add_argument("--url", action="append", dest="urls", help="アーカイブ URL（複数可）")
    ap.add_argument(
        "--urls-file",
        type=Path,
        help="1 行 1 URL のテキスト（# 行と空行は無視）",
    )
    ap.add_argument("--apply", action="store_true", help="seed / x_profile を更新")
    ap.add_argument(
        "--max-new",
        type=int,
        default=None,
        help="追加する person 上限（ソート済みリストの先頭から採用）",
    )
    args = ap.parse_args()
    urls: list[str] = list(args.urls) if args.urls else []
    if args.urls_file is not None:
        for line in args.urls_file.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                urls.append(s)
    if not urls:
        urls = [DEFAULT_URL]

    blobs: list[str] = []
    for url in urls:
        r = requests.get(url, timeout=60, headers={"User-Agent": USER_AGENT})
        if r.status_code != 200:
            print(f"[WARN] skip {url!r}: HTTP {r.status_code}", file=sys.stderr)
            continue
        blobs.append(r.text)

    handles: set[str] = set()
    for blob in blobs:
        handles |= _extract_handles(blob)

    existing = _seed_and_alias_cf()
    to_add = _filter_handles(handles, existing)
    full_new = len(to_add)
    if args.max_new is not None and args.max_new >= 0:
        to_add = to_add[: args.max_new]
    extra = f" (capped from {full_new})" if args.max_new is not None else ""
    print(f"found {len(handles)} raw handles, {len(to_add)} new after filters{extra}")
    for h, hid in to_add[:120]:
        print(f"{h}\t{hid}")
    if len(to_add) > 120:
        print(f"... and {len(to_add) - 120} more")

    if args.apply and to_add:
        _insert_persons(to_add)
        _append_x_profiles(to_add)
        print(f"[OK] applied +{len(to_add)} persons")


if __name__ == "__main__":
    main()
