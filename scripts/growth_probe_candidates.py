"""Probe unseeded X handles from generated snapshots; print UTF-8 summaries."""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from collector import extract_x_profile_snapshot, fetch_page  # noqa: E402

X_URL = re.compile(
    r"https?://(?:www\.)?(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,15})(?:[/?#]|$)",
    re.IGNORECASE,
)
AT = re.compile(r"@([A-Za-z0-9_]{1,15})")

RESERVED = frozenset(
    {
        "home",
        "search",
        "explore",
        "settings",
        "i",
        "intent",
        "hashtag",
        "share",
        "account",
        "compose",
        "notifications",
        "messages",
        "login",
        "signup",
        "tos",
        "privacy",
        "about",
        "help",
        "support",
        "ads",
        "download",
        "teams",
        "spaces",
        "live",
        "topics",
        "who",
        "following",
        "followers",
        "verified_followers",
        "highlights",
        "likes",
        "lists",
        "communities",
        "jobs",
        "oauth",
        "user",
        "tinder",
        "youtube",
        "instagram",
        "note",
        "line",
        "mbh",
        "justinbieber",
        "city_shibuya",
        "city-shibuya",
        "shinjuku_info",
        "another",
        "46",
        "lv1",
        "gw5",
        "pua",
        "riseuplab",
        "rise",
    }
)

HANDLE_SCENE = re.compile(
    r"(pua|nanpa|nampa|nnp|mote|street|suto|nst|yamate|np$|_np|_pua|korea|wing|lesson|god|pick)",
    re.IGNORECASE,
)

BIO_SCENE = re.compile(
    r"(ナンパ|ストナン|スト即|講習|長期|師匠|弟子|ナンパ師|即数|路上|ウリセン|マッチングアプリ|ネト即)",
)


def handle_to_id(handle: str) -> str:
    h = handle.strip()
    if "_" in h:
        return "-".join(p for p in h.split("_") if p).lower()
    return h.lower()


def load_seed_and_person_nodes() -> tuple[set[str], set[str]]:
    seed_ids: set[str] = set()
    aliases_cf: set[str] = set()
    for line in (ROOT / "seed_entities.txt").read_text(encoding="utf-8").splitlines():
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
    nodes = {
        n["id"].casefold()
        for n in json.loads((ROOT / "data/nodes.json").read_text(encoding="utf-8"))
        if n.get("type") == "person"
    }
    return seed_ids | aliases_cf, nodes


def candidate_handles() -> list[tuple[int, str]]:
    banned_prefix = ("city_", "twitter", "x.com")
    cf_ids, nodes = load_seed_and_person_nodes()
    blobs = [
        (ROOT / "data/source_snapshots.generated.json").read_text(encoding="utf-8"),
        (ROOT / "data/review_candidates.json").read_text(encoding="utf-8"),
    ]
    ctr: Counter[str] = Counter()
    for blob in blobs:
        for m in X_URL.finditer(blob):
            ctr[m.group(1)] += 1
        for m in AT.finditer(blob):
            ctr[m.group(1)] += 1

    out: list[tuple[int, str]] = []
    for h, c in ctr.most_common(1500):
        hid = handle_to_id(h)
        if h.casefold() in RESERVED or hid.casefold() in RESERVED:
            continue
        if any(h.casefold().startswith(p) for p in banned_prefix):
            continue
        if hid.casefold() in cf_ids or h.casefold() in cf_ids:
            continue
        if hid.casefold() in nodes:
            continue
        if len(h) < 2 or len(h) > 15:
            continue
        if not HANDLE_SCENE.search(h) and c < 2:
            continue
        out.append((c, h))
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    candidates = candidate_handles()[:55]
    for _c, h in candidates:
        hid = handle_to_id(h)
        url = f"https://x.com/{h}"
        try:
            body, fu = fetch_page(url)
            snap = extract_x_profile_snapshot(hid, url, body, fetched_url=fu, label="growth-probe")
            text = (snap.get("profile_text") or "") + (snap.get("summary") or "")
            ok = bool(BIO_SCENE.search(text))
            flag = "OK" if ok else "??"
            print(f"{flag}\t{h}\t{hid}\t{snap.get('summary', '')[:120]}")
        except Exception as exc:
            print(f"ERR\t{h}\t-\t{exc}")


if __name__ == "__main__":
    main()
