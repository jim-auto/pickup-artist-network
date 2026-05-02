"""Scan generated snapshots for X handles not yet seeded (diagnostic / growth helper)."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

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
        "share",
        "privacy",
    }
)


def handle_to_id(handle: str) -> str:
    h = handle.strip()
    if "_" in h:
        return "-".join(p for p in h.split("_") if p).lower()
    return h.lower()


def main() -> None:
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

    ctr: Counter[str] = Counter()
    gen_path = ROOT / "data/source_snapshots.generated.json"
    blob = gen_path.read_text(encoding="utf-8")
    for rx in (X_URL,):
        for m in rx.finditer(blob):
            ctr[m.group(1)] += 1
    for m in AT.finditer(blob):
        ctr[m.group(1)] += 1

    cands: list[tuple[int, str, str]] = []
    for h, c in ctr.most_common(800):
        hid = handle_to_id(h)
        if h.casefold() in RESERVED or hid.casefold() in RESERVED:
            continue
        if hid.casefold() in seed_ids or h.casefold() in aliases_cf:
            continue
        if hid.casefold() in nodes:
            continue
        if len(h) < 2 or len(h) > 15:
            continue
        cands.append((c, h, hid))

    print(f"counters: {len(ctr)} unique handles, {len(cands)} not yet person-seeded")
    for row in cands[:50]:
        print(row)


if __name__ == "__main__":
    main()
