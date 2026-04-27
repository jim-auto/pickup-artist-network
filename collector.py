from __future__ import annotations

import argparse
import html as html_lib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from scraper import GENERATED_SNAPSHOT_FILE, detect_platform_id_from_url, load_seed_entities

COLLECTOR_CONFIG = Path("data/collector_sources.json")
X_PROFILE_CONFIG = Path("data/x_profile_sources.json")
SEED_FILE = Path("seed_entities.txt")
DEFAULT_TIMEOUT = 20
DEFAULT_MAX_LINKS = 12
DEFAULT_DENY_URL_KEYWORDS = (
    "login",
    "signup",
    "sign-up",
    "register",
    "terms",
    "privacy",
    "policy",
    "cookies",
    "search",
    "career",
    "careers",
    "jobs",
    "affiliate",
    "plus",
)
USER_AGENT = "pickup-artist-network-collector/0.1 (+https://github.com/jim-auto/pickup-artist-network)"
TEXT_WHITESPACE_RE = re.compile(r"\s+")
X_HANDLE_URL_RE = re.compile(r"https?://(?:www\.)?(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,15})(?:[/?#]|$)", re.IGNORECASE)
X_STATUS_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:x\.com|twitter\.com)/[A-Za-z0-9_]{1,15}/status/\d+(?:[/?#]|$)",
    re.IGNORECASE,
)
MAX_SUMMARY_CHARS = 180
MAX_PROFILE_TEXT_CHARS = 420
MAX_PROFILE_TEXT_LINES = 3


def normalize_platform_list(values: object, field_name: str) -> list[str] | None:
    if values is None:
        return None
    if not isinstance(values, list):
        raise ValueError(f"{field_name} must be a list")
    normalized = [str(value).strip() for value in values if str(value).strip()]
    return normalized


def normalize_keyword_list(values: object, field_name: str) -> list[str]:
    if values in (None, []):
        return []
    if not isinstance(values, list):
        raise ValueError(f"{field_name} must be a list")
    return [str(value).strip().lower() for value in values if str(value).strip()]


def compress_line(text: str, max_chars: int) -> str:
    normalized = TEXT_WHITESPACE_RE.sub(" ", text.strip())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 1].rstrip() + "…"


def compress_summary_text(text: str, max_chars: int = MAX_SUMMARY_CHARS) -> str:
    return compress_line(text, max_chars=max_chars)


def compress_profile_text(
    parts: list[str],
    *,
    max_lines: int = MAX_PROFILE_TEXT_LINES,
    max_chars: int = MAX_PROFILE_TEXT_CHARS,
) -> str:
    lines: list[str] = []
    for part in parts:
        for raw_line in str(part).splitlines():
            line = compress_line(raw_line, max_chars=180)
            if not line or line in lines:
                continue
            lines.append(line)
            if len(lines) >= max_lines:
                break
        if len(lines) >= max_lines:
            break

    profile_text = "\n".join(lines)
    if len(profile_text) <= max_chars:
        return profile_text
    return profile_text[: max_chars - 1].rstrip() + "…"


def load_collector_sources(path: Path = COLLECTOR_CONFIG) -> list[dict[str, object]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("collector_sources.json must contain a list")

    sources: list[dict[str, object]] = []
    for index, entry in enumerate(payload):
        if not isinstance(entry, dict):
            raise ValueError(f"collector_sources[{index}] must be an object")
        if not entry.get("enabled", True):
            continue
        account_id = str(entry.get("account_id", "")).strip()
        source_url = str(entry.get("url", "")).strip()
        if not account_id:
            raise ValueError(f"collector_sources[{index}].account_id is required")
        if not source_url.startswith(("http://", "https://")):
            raise ValueError(f"collector_sources[{index}].url must be http(s)")
        sources.append(
            {
                "account_id": account_id,
                "url": source_url,
                "label": str(entry.get("label", "")).strip(),
                "max_links": int(entry.get("max_links", DEFAULT_MAX_LINKS)),
                "allowed_platforms": normalize_platform_list(
                    entry.get("allowed_platforms", []),
                    f"collector_sources[{index}].allowed_platforms",
                ),
                "deny_url_keywords": normalize_keyword_list(
                    entry.get("deny_url_keywords", list(DEFAULT_DENY_URL_KEYWORDS)),
                    f"collector_sources[{index}].deny_url_keywords",
                ),
            }
        )
    return sources


def load_x_profile_sources(
    path: Path = X_PROFILE_CONFIG,
    seed_path: Path = SEED_FILE,
) -> list[dict[str, object]]:
    if not path.exists():
        return []

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("x_profile_sources.json must contain a list")

    seed_entities = {entity["id"]: entity for entity in load_seed_entities(seed_path)}
    sources: list[dict[str, object]] = []
    for index, entry in enumerate(payload):
        if not isinstance(entry, dict):
            raise ValueError(f"x_profile_sources[{index}] must be an object")
        if not entry.get("enabled", True):
            continue
        account_id = str(entry.get("account_id", "")).strip()
        source_url = str(entry.get("url", "")).strip()
        if not account_id:
            raise ValueError(f"x_profile_sources[{index}].account_id is required")
        if account_id not in seed_entities:
            raise ValueError(f"x_profile_sources[{index}] references unknown account_id: {account_id}")
        if seed_entities[account_id]["type"] not in {"person", "community", "location", "platform"}:
            raise ValueError(
                f"x_profile_sources[{index}] account type must be person/community/location/platform: {account_id}"
            )
        host = urlparse(source_url).netloc.lower().removeprefix("www.")
        if host not in {"x.com", "twitter.com"}:
            raise ValueError(f"x_profile_sources[{index}].url must point to x.com or twitter.com")
        pinned_post_url = str(entry.get("pinned_post_url", "")).strip()
        if pinned_post_url and not X_STATUS_URL_RE.match(pinned_post_url):
            raise ValueError(
                f"x_profile_sources[{index}].pinned_post_url must point to an x.com/twitter.com status URL"
            )
        sources.append(
            {
                "account_id": account_id,
                "url": source_url,
                "label": str(entry.get("label", "x profile")).strip(),
                "pinned_post_url": pinned_post_url,
            }
        )
    return sources


def fetch_page(url: str, timeout: int = DEFAULT_TIMEOUT) -> tuple[bytes, str]:
    response = requests.get(
        url,
        timeout=timeout,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    return response.content, str(response.url)


def extract_summary_parts(soup: BeautifulSoup) -> tuple[str, str]:
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    description = extract_meta_content(
        soup,
        (
            'meta[name="description"]',
            'meta[property="og:description"]',
            'meta[name="twitter:description"]',
        ),
    )
    return title, description


def extract_meta_content(soup: BeautifulSoup, selectors: tuple[str, ...]) -> str:
    for selector in selectors:
        tag = soup.select_one(selector)
        if tag and tag.get("content"):
            return str(tag["content"]).strip()
    return ""


def extract_link_href(soup: BeautifulSoup, selectors: tuple[str, ...]) -> str:
    for selector in selectors:
        tag = soup.select_one(selector)
        if tag is None:
            continue
        if tag.get("href"):
            return str(tag["href"]).strip()
        if tag.get("content"):
            return str(tag["content"]).strip()
    return ""


def extract_body_excerpt(soup: BeautifulSoup, max_parts: int = 4) -> str:
    parts: list[str] = []
    for selector in ("h1", "h2", "p", "li"):
        for tag in soup.select(selector):
            text = tag.get_text(" ", strip=True)
            if len(text) < 20 or text in parts:
                continue
            parts.append(text)
            if len(parts) >= max_parts:
                return "\n".join(parts)
    return "\n".join(parts)


def extract_x_handle_from_url(url: str) -> str:
    match = X_HANDLE_URL_RE.match(url.strip())
    if not match:
        return ""
    return match.group(1)


def normalize_embedded_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return TEXT_WHITESPACE_RE.sub(" ", html_lib.unescape(value).strip())


def find_matching_brace(text: str, start_index: int, *, max_chars: int = 8000) -> int | None:
    if start_index < 0 or start_index >= len(text) or text[start_index] != "{":
        return None

    depth = 0
    in_string = False
    escaped = False
    end_limit = min(len(text), start_index + max_chars)
    for index in range(start_index, end_limit):
        character = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue

        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    return None


def extract_json_object_containing_anchor(text: str, anchor_index: int) -> dict[str, object] | None:
    start_limit = max(0, anchor_index - 6000)
    for start_index in range(anchor_index, start_limit - 1, -1):
        if text[start_index] != "{":
            continue
        end_index = find_matching_brace(text, start_index)
        if end_index is None or anchor_index >= end_index:
            continue
        try:
            candidate = json.loads(text[start_index:end_index])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            return candidate
    return None


def extract_external_url_from_user_object(user_object: dict[str, object]) -> str:
    entities = user_object.get("entities", {})
    if not isinstance(entities, dict):
        return ""
    url_meta = entities.get("url", {})
    if not isinstance(url_meta, dict):
        return ""
    for entry in url_meta.get("urls", []):
        if not isinstance(entry, dict):
            continue
        expanded_url = normalize_embedded_text(entry.get("expanded_url"))
        if expanded_url:
            return expanded_url
    return ""


def normalize_x_profile_image_url(url: str) -> str:
    normalized = normalize_embedded_text(url)
    if not normalized:
        return ""
    return re.sub(r"_normal(\.(?:jpg|jpeg|png|webp))$", r"_400x400\1", normalized, flags=re.IGNORECASE)


def extract_profile_image_url_from_user_object(user_object: dict[str, object]) -> str:
    for field in ("profile_image_url_https", "profile_image_url"):
        image_url = normalize_x_profile_image_url(str(user_object.get(field, "")).strip())
        if image_url:
            return image_url
    return ""


def extract_x_embedded_profile(
    soup: BeautifulSoup,
    *,
    source_url: str,
    fetched_url: str | None = None,
) -> dict[str, str]:
    handle = extract_x_handle_from_url(fetched_url or source_url)
    if not handle:
        return {}

    handle_pattern = f'"screen_name":"{handle}"'
    for script in soup.find_all("script"):
        script_text = script.string or script.get_text("", strip=False)
        if not script_text or handle_pattern not in script_text:
            continue

        match_index = script_text.find(handle_pattern)
        while match_index != -1:
            user_object = extract_json_object_containing_anchor(script_text, match_index)
            if user_object and str(user_object.get("screen_name", "")).casefold() == handle.casefold():
                return {
                    "handle": handle,
                    "display_name": normalize_embedded_text(user_object.get("name")),
                    "description": normalize_embedded_text(user_object.get("description")),
                    "location": normalize_embedded_text(user_object.get("location")),
                    "external_url": extract_external_url_from_user_object(user_object),
                    "avatar_url": extract_profile_image_url_from_user_object(user_object),
                }
            match_index = script_text.find(handle_pattern, match_index + len(handle_pattern))

    return {"handle": handle}


def extract_display_name_from_title(raw_title: str, handle: str) -> str:
    if not raw_title:
        return ""
    candidate = raw_title.split(" / ")[0].strip()
    if handle:
        candidate = re.sub(
            rf"\s*\(@{re.escape(handle)}\)\s*$",
            "",
            candidate,
            flags=re.IGNORECASE,
        ).strip()
        if candidate.casefold() in {handle.casefold(), f"@{handle}".casefold()}:
            return ""
    return candidate


def format_x_identity(display_name: str, handle: str) -> str:
    cleaned_name = display_name.strip()
    cleaned_handle = handle.strip().lstrip("@")
    if cleaned_name and cleaned_handle:
        if cleaned_name.casefold() in {cleaned_handle.casefold(), f"@{cleaned_handle}".casefold()}:
            return f"@{cleaned_handle}"
        return f"{cleaned_name} (@{cleaned_handle})"
    if cleaned_name:
        return cleaned_name
    if cleaned_handle:
        return f"@{cleaned_handle}"
    return ""


def build_x_summary(display_name: str, handle: str, description: str, location: str) -> str:
    if description:
        return compress_summary_text(description)

    identity = format_x_identity(display_name, handle)
    if identity and location:
        return compress_summary_text(f"X profile for {identity}. Location: {location}.")
    if identity:
        return compress_summary_text(f"X profile for {identity}.")
    return "Public X profile."


def clean_x_post_hint_text(text: str) -> str:
    normalized = normalize_embedded_text(text)
    if not normalized:
        return ""
    normalized = re.sub(r"\s*/\s*(X|Twitter)$", "", normalized, flags=re.IGNORECASE).strip()
    for marker in (" on X: ", " on Twitter: "):
        if marker in normalized:
            normalized = normalized.split(marker, 1)[1].strip()
            break
    return compress_line(normalized.strip(" \"'“”"), max_chars=280)


def extract_x_post_hint(
    source_url: str,
    html: str | bytes,
    *,
    fetched_url: str | None = None,
) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    description = extract_meta_content(
        soup,
        (
            'meta[property="og:description"]',
            'meta[name="twitter:description"]',
            'meta[name="description"]',
        ),
    )
    canonical_url = extract_link_href(
        soup,
        (
            'link[rel="canonical"]',
            'meta[property="og:url"]',
        ),
    )
    return {
        "pinned_post_url": canonical_url or fetched_url or source_url,
        "pinned_post_text": clean_x_post_hint_text(description or title),
    }


def extract_x_profile_details(
    soup: BeautifulSoup,
    *,
    source_url: str,
    fetched_url: str | None = None,
) -> dict[str, str]:
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    meta_title = extract_meta_content(
        soup,
        (
            'meta[property="og:title"]',
            'meta[name="twitter:title"]',
        ),
    )
    description = extract_meta_content(
        soup,
        (
            'meta[property="og:description"]',
            'meta[name="description"]',
            'meta[name="twitter:description"]',
        ),
    )
    embedded_profile = extract_x_embedded_profile(soup, source_url=source_url, fetched_url=fetched_url)
    handle = embedded_profile.get("handle", "") or extract_x_handle_from_url(fetched_url or source_url)
    display_name = embedded_profile.get("display_name", "") or extract_display_name_from_title(meta_title or title, handle)
    return {
        "display_name": display_name,
        "handle": handle,
        "title": meta_title or title,
        "description": embedded_profile.get("description", "") or description,
        "location": embedded_profile.get("location", ""),
        "external_url": embedded_profile.get("external_url", ""),
        "avatar_url": embedded_profile.get("avatar_url", ""),
    }


def extract_filtered_links(
    soup: BeautifulSoup,
    base_url: str,
    *,
    source_account_id: str = "",
    max_links: int = DEFAULT_MAX_LINKS,
    allowed_platforms: list[str] | None = None,
    deny_url_keywords: list[str] | None = None,
) -> list[str]:
    deny_keywords = [keyword.casefold() for keyword in (deny_url_keywords or [])]
    seen_platforms: set[str] = set()
    links: list[str] = []
    for tag in soup.select("a[href]"):
        absolute_url = urljoin(base_url, str(tag["href"]).strip())
        if not absolute_url.startswith(("http://", "https://")):
            continue
        lowered_url = absolute_url.casefold()
        if any(keyword in lowered_url for keyword in deny_keywords):
            continue
        platform_id = detect_platform_id_from_url(absolute_url)
        if not platform_id:
            continue
        if source_account_id and platform_id == source_account_id:
            continue
        if allowed_platforms is not None and platform_id not in allowed_platforms:
            continue
        if platform_id in seen_platforms:
            continue
        if absolute_url in links:
            continue
        seen_platforms.add(platform_id)
        links.append(absolute_url)
        if len(links) >= max_links:
            break
    return links


def extract_snapshot_from_html(
    account_id: str,
    source_url: str,
    html: str | bytes,
    *,
    label: str = "",
    fetched_url: str | None = None,
    max_links: int = DEFAULT_MAX_LINKS,
    allowed_platforms: list[str] | None = None,
    deny_url_keywords: list[str] | None = None,
) -> dict[str, object]:
    soup = BeautifulSoup(html, "html.parser")
    title, description = extract_summary_parts(soup)
    body_excerpt = extract_body_excerpt(soup)
    links = extract_filtered_links(
        soup,
        fetched_url or source_url,
        source_account_id=account_id,
        max_links=max_links,
        allowed_platforms=allowed_platforms,
        deny_url_keywords=deny_url_keywords,
    )

    summary = compress_summary_text(
        description or title or (body_excerpt.splitlines()[0] if body_excerpt else "")
    )
    profile_text = compress_profile_text([title, description, body_excerpt])

    return {
        "account_id": account_id,
        "profile_url": fetched_url or source_url,
        "pinned_post_url": "",
        "profile_text": profile_text,
        "pinned_post_text": "",
        "links": links,
        "summary": summary,
        "evidence_kind": "fact",
        "needs_review": True,
        "review_notes": "Auto-collected from a public page. Review before relying on inferred meaning.",
        "snapshot_origin": "generated",
        "collector": {
            "type": "public_page",
            "label": label,
            "source_url": source_url,
            "fetched_url": fetched_url or source_url,
            "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
        "observations": [],
    }


def extract_x_profile_snapshot(
    account_id: str,
    source_url: str,
    html: str | bytes,
    *,
    label: str = "",
    fetched_url: str | None = None,
    pinned_post_url: str = "",
    pinned_post_html: str | bytes | None = None,
    pinned_post_fetched_url: str | None = None,
    pinned_post_fetch_error: str = "",
) -> dict[str, object]:
    soup = BeautifulSoup(html, "html.parser")
    details = extract_x_profile_details(soup, source_url=source_url, fetched_url=fetched_url)
    identity = format_x_identity(details["display_name"], details["handle"])
    summary = build_x_summary(
        details["display_name"],
        details["handle"],
        details["description"],
        details["location"],
    )
    profile_text = compress_profile_text(
        [
            identity,
            details["description"] or details["title"],
            f"Location: {details['location']}" if details["location"] else "",
            f"External link: {details['external_url']}" if details["external_url"] else "",
        ]
    )
    pinned_post = (
        extract_x_post_hint(
            pinned_post_url,
            pinned_post_html,
            fetched_url=pinned_post_fetched_url,
        )
        if pinned_post_url and pinned_post_html is not None
        else {
            "pinned_post_url": pinned_post_url or "",
            "pinned_post_text": "",
        }
    )
    review_notes_parts = [
        "Auto-collected from a public X profile. Review generated bio and pinned-post hints."
    ]
    if pinned_post_fetch_error:
        review_notes_parts.append(
            f"Pinned-post URL fetch failed: {compress_line(pinned_post_fetch_error, max_chars=140)}."
        )
    elif pinned_post_url and pinned_post["pinned_post_text"]:
        review_notes_parts.append("Pinned-post hint was auto-collected from a configured X status URL.")
    elif pinned_post_url:
        review_notes_parts.append(
            "Pinned-post URL was configured, but logged-out X HTML did not expose a reliable text hint."
        )
    else:
        review_notes_parts.append(
            "Profile HTML did not expose a pinned-post URL; configure pinned_post_url in data/x_profile_sources.json to attach a generated pinned-post hint."
        )

    links: list[str] = []
    for candidate_url in [fetched_url or source_url, details["external_url"]]:
        normalized = str(candidate_url).strip()
        if normalized and normalized not in links:
            links.append(normalized)

    return {
        "account_id": account_id,
        "profile_url": fetched_url or source_url,
        "pinned_post_url": pinned_post["pinned_post_url"],
        "icon_url": str(details["avatar_url"]).strip(),
        "profile_text": profile_text,
        "pinned_post_text": pinned_post["pinned_post_text"],
        "links": links,
        "summary": summary,
        "evidence_kind": "fact",
        "needs_review": True,
        "review_notes": " ".join(review_notes_parts),
        "summary_evidence_kind": "fact",
        "snapshot_origin": "generated",
        "collector": {
            "type": "x_profile",
            "label": label,
            "source_url": source_url,
            "fetched_url": fetched_url or source_url,
            "pinned_post_source_url": pinned_post_url or "",
            "pinned_post_fetched_url": pinned_post_fetched_url or pinned_post["pinned_post_url"] or "",
            "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
        "observations": [],
    }


def collect_snapshots(
    sources: list[dict[str, object]],
    timeout: int = DEFAULT_TIMEOUT,
    continue_on_error: bool = True,
) -> list[dict[str, object]]:
    snapshots: list[dict[str, object]] = []
    for source in sources:
        try:
            html, fetched_url = fetch_page(str(source["url"]), timeout=timeout)
        except requests.RequestException as exc:
            if not continue_on_error:
                raise
            print(f"[WARN] skipped {source['url']}: {exc}")
            continue
        snapshots.append(
            extract_snapshot_from_html(
                str(source["account_id"]),
                str(source["url"]),
                html,
                label=str(source.get("label", "")),
                fetched_url=fetched_url,
                max_links=int(source.get("max_links", DEFAULT_MAX_LINKS)),
                allowed_platforms=[
                    str(value) for value in source.get("allowed_platforms", [])
                ],
                deny_url_keywords=[
                    str(value) for value in source.get("deny_url_keywords", [])
                ],
            )
        )
    return snapshots


def collect_x_profile_snapshots(
    sources: list[dict[str, object]],
    timeout: int = DEFAULT_TIMEOUT,
    continue_on_error: bool = True,
) -> list[dict[str, object]]:
    snapshots: list[dict[str, object]] = []
    for source in sources:
        try:
            html, fetched_url = fetch_page(str(source["url"]), timeout=timeout)
        except requests.RequestException as exc:
            if not continue_on_error:
                raise
            print(f"[WARN] skipped X profile {source['url']}: {exc}")
            continue
        pinned_post_url = str(source.get("pinned_post_url", "")).strip()
        pinned_post_html: str | bytes | None = None
        pinned_post_fetched_url = ""
        pinned_post_fetch_error = ""
        if pinned_post_url:
            try:
                pinned_post_html, pinned_post_fetched_url = fetch_page(
                    pinned_post_url,
                    timeout=timeout,
                )
            except requests.RequestException as exc:
                if not continue_on_error:
                    raise
                pinned_post_fetch_error = str(exc)
                print(f"[WARN] skipped pinned post {pinned_post_url}: {exc}")
        snapshots.append(
            extract_x_profile_snapshot(
                str(source["account_id"]),
                str(source["url"]),
                html,
                label=str(source.get("label", "")),
                fetched_url=fetched_url,
                pinned_post_url=pinned_post_url,
                pinned_post_html=pinned_post_html,
                pinned_post_fetched_url=pinned_post_fetched_url or None,
                pinned_post_fetch_error=pinned_post_fetch_error,
            )
        )
    return snapshots


def merge_generated_snapshots(snapshots: list[dict[str, object]]) -> list[dict[str, object]]:
    def priority(snapshot: dict[str, object]) -> int:
        collector_meta = snapshot.get("collector", {})
        collector_type = collector_meta.get("type") if isinstance(collector_meta, dict) else ""
        if collector_type == "x_profile":
            return 30
        if collector_type == "public_page":
            return 20
        return 10

    grouped: dict[str, list[dict[str, object]]] = {}
    for snapshot in snapshots:
        grouped.setdefault(str(snapshot["account_id"]), []).append(snapshot)

    merged_snapshots: list[dict[str, object]] = []
    scalar_fields = (
        "profile_url",
        "pinned_post_url",
        "icon_url",
        "profile_text",
        "pinned_post_text",
        "summary",
        "review_notes",
        "evidence_kind",
        "summary_evidence_kind",
    )

    for account_id in sorted(grouped):
        ordered = sorted(grouped[account_id], key=priority, reverse=True)
        merged = {
            "account_id": account_id,
            "profile_url": "",
            "pinned_post_url": "",
            "profile_text": "",
            "pinned_post_text": "",
            "links": [],
            "summary": "",
            "evidence_kind": "fact",
            "needs_review": False,
            "review_notes": "",
            "summary_evidence_kind": "fact",
            "snapshot_origin": "generated",
            "collector": {"sources": []},
            "observations": [],
        }
        notes: list[str] = []
        observations: list[dict[str, object]] = []

        for snapshot in ordered:
            for field in scalar_fields:
                value = str(snapshot.get(field, "")).strip()
                if value and not str(merged.get(field, "")).strip():
                    merged[field] = value
            merged["links"] = list(dict.fromkeys([*merged["links"], *snapshot.get("links", [])]))
            merged["needs_review"] = bool(merged["needs_review"] or snapshot.get("needs_review", False))
            note = str(snapshot.get("review_notes", "")).strip()
            if note and note not in notes:
                notes.append(note)
            collector_meta = snapshot.get("collector", {})
            if collector_meta and collector_meta not in merged["collector"]["sources"]:
                merged["collector"]["sources"].append(collector_meta)
            for observation in snapshot.get("observations", []):
                if observation not in observations:
                    observations.append(observation)

        merged["review_notes"] = " ".join(notes).strip()
        merged["observations"] = observations
        merged_snapshots.append(merged)

    return merged_snapshots


def collect_to_file(
    config_path: Path = COLLECTOR_CONFIG,
    x_profile_config_path: Path = X_PROFILE_CONFIG,
    output_path: Path = GENERATED_SNAPSHOT_FILE,
    timeout: int = DEFAULT_TIMEOUT,
    continue_on_error: bool = True,
) -> list[dict[str, object]]:
    sources = load_collector_sources(config_path)
    public_snapshots = collect_snapshots(
        sources,
        timeout=timeout,
        continue_on_error=continue_on_error,
    )
    x_profile_sources = load_x_profile_sources(x_profile_config_path)
    x_profile_snapshots = collect_x_profile_snapshots(
        x_profile_sources,
        timeout=timeout,
        continue_on_error=continue_on_error,
    )
    snapshots = merge_generated_snapshots([*public_snapshots, *x_profile_snapshots])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(snapshots, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return snapshots


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect lightweight public-page snapshots for pickup-artist-network."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=COLLECTOR_CONFIG,
        help="Path to collector source config JSON.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=GENERATED_SNAPSHOT_FILE,
        help="Path to generated snapshot JSON output.",
    )
    parser.add_argument(
        "--x-profile-config",
        type=Path,
        default=X_PROFILE_CONFIG,
        help="Path to X profile source config JSON.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help="HTTP timeout in seconds.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail immediately if any configured page cannot be fetched.",
    )
    args = parser.parse_args()

    snapshots = collect_to_file(
        args.config,
        args.x_profile_config,
        args.output,
        timeout=args.timeout,
        continue_on_error=not args.strict,
    )
    print(
        f"[OK] collected {len(snapshots)} generated snapshots -> {args.output}"
    )


if __name__ == "__main__":
    main()
