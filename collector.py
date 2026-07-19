from __future__ import annotations

import argparse
import html as html_lib
import io
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from scraper import GENERATED_SNAPSHOT_FILE, detect_platform_id_from_url, load_seed_entities

COLLECTOR_CONFIG = Path("data/collector_sources.json")
X_PROFILE_CONFIG = Path("data/x_profile_sources.json")
SEED_FILE = Path("seed_entities.txt")
X_AUTH_STATE_FILE = Path("data/.x_auth_state.json")
X_COOKIE_FILE = Path("data/.x_cookies.json")
X_WEB_PROFILE_SKIP_FILE = Path("data/x_web_profile_skip.json")
DEFAULT_TIMEOUT = 20
X_API_USERS_BY_URL = "https://api.x.com/2/users/by"
X_API_USER_FIELDS = "description,location,name,profile_image_url,public_metrics,url,username,verified"
X_WEB_USER_BY_SCREEN_NAME_URL = "https://x.com/i/api/graphql/IGgvgiOx4QZndDHuD3x9TQ/UserByScreenName"
X_WEB_BEARER_TOKEN = (
    "Bearer "
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D"
    "1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)
X_WEB_USER_FEATURES = {
    "hidden_profile_subscriptions_enabled": True,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "responsive_web_profile_redirect_enabled": False,
    "rweb_tipjar_consumption_enabled": False,
    "verified_phone_label_enabled": False,
    "subscriptions_verification_info_is_identity_verified_enabled": True,
    "subscriptions_verification_info_verified_since_enabled": True,
    "highlights_tweets_tab_ui_enabled": True,
    "responsive_web_twitter_article_notes_tab_enabled": True,
    "subscriptions_feature_can_gift_premium": True,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True,
}
X_COOKIE_DOMAINS = {".x.com", "x.com", ".twitter.com", "twitter.com"}
X_WEB_USER_FIELD_TOGGLES = {
    "withPayments": False,
    "withAuxiliaryUserLabels": True,
}


def resolve_x_cookie_file(cookie_file_path: Path | None) -> Path | None:
    """Use explicit cookie JSON when passed; else fall back to data/.x_cookies.json if present."""
    if cookie_file_path is not None:
        return cookie_file_path
    if X_COOKIE_FILE.exists():
        return X_COOKIE_FILE
    return None
DEFAULT_MAX_LINKS = 48
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
DEFAULT_FOLLOWING_LIMIT = 120
X_RESERVED_PATH_SEGMENTS = {
    "about",
    "account",
    "compose",
    "explore",
    "hashtag",
    "home",
    "i",
    "intent",
    "login",
    "messages",
    "notifications",
    "privacy",
    "search",
    "settings",
    "share",
    "signup",
    "tos",
}


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


def load_dotenv_values(path: Path) -> dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(f"Missing dotenv file: {path}")
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def load_x_login_credentials(dotenv_path: Path | None = None) -> tuple[str, str]:
    username = os.getenv("TWITTER_USERNAME") or os.getenv("X_USERNAME") or ""
    password = os.getenv("TWITTER_PASSWORD") or os.getenv("X_PASSWORD") or ""
    if username and password:
        return username, password
    if dotenv_path is None:
        return "", ""

    dotenv_values = load_dotenv_values(dotenv_path)
    username = dotenv_values.get("TWITTER_USERNAME") or dotenv_values.get("X_USERNAME") or ""
    password = dotenv_values.get("TWITTER_PASSWORD") or dotenv_values.get("X_PASSWORD") or ""
    return username, password


def load_x_api_bearer_token(dotenv_path: Path | None = None) -> str:
    for env_name in ("X_BEARER_TOKEN", "TWITTER_BEARER_TOKEN", "X_API_BEARER_TOKEN"):
        value = os.getenv(env_name, "").strip()
        if value:
            return value
    if dotenv_path is None:
        return ""
    dotenv_values = load_dotenv_values(dotenv_path)
    for env_name in ("X_BEARER_TOKEN", "TWITTER_BEARER_TOKEN", "X_API_BEARER_TOKEN"):
        value = dotenv_values.get(env_name, "").strip()
        if value:
            return value
    return ""


def load_playwright_cookies(cookie_file_path: Path) -> list[dict[str, object]]:
    if not cookie_file_path.exists():
        raise FileNotFoundError(f"Missing cookie file: {cookie_file_path}")
    payload = json.loads(cookie_file_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Cookie file must contain a list: {cookie_file_path}")

    cookies: list[dict[str, object]] = []
    for index, entry in enumerate(payload):
        if not isinstance(entry, dict):
            raise ValueError(f"cookie[{index}] must be an object")
        name = str(entry.get("name", "")).strip()
        value = str(entry.get("value", "")).strip()
        domain = str(entry.get("domain", "")).strip()
        if not name or not value or not domain:
            continue
        cookie: dict[str, object] = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": str(entry.get("path", "/") or "/"),
            "httpOnly": bool(entry.get("httpOnly", False)),
            "secure": bool(entry.get("secure", True)),
        }
        expiry = entry.get("expiry")
        if isinstance(expiry, (int, float)) and expiry > 0:
            cookie["expires"] = float(expiry)
        same_site = str(entry.get("sameSite", "")).strip()
        if same_site in {"Lax", "None", "Strict"}:
            cookie["sameSite"] = same_site
        cookies.append(cookie)
    return cookies


def load_cookie_entries(cookie_file_path: Path) -> list[dict[str, object]]:
    """Read cookie entries from either Playwright storage_state or Selenium-style JSON."""
    if not cookie_file_path.exists():
        raise FileNotFoundError(f"Missing cookie file: {cookie_file_path}")
    payload = json.loads(cookie_file_path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        cookies = payload.get("cookies", [])
    else:
        cookies = payload
    if not isinstance(cookies, list):
        raise ValueError(f"Cookie payload must contain a cookie list: {cookie_file_path}")
    return [entry for entry in cookies if isinstance(entry, dict)]


def cookie_file_has_names(cookie_file_path: Path, required_names: set[str]) -> bool:
    if not cookie_file_path.exists():
        return False
    try:
        cookies = load_cookie_entries(cookie_file_path)
    except (OSError, json.JSONDecodeError, ValueError):
        return False
    names = {str(cookie.get("name", "")) for cookie in cookies}
    return required_names.issubset(names)


def normalize_x_cookie_entries(cookie_file_path: Path) -> list[dict[str, object]]:
    cookies: list[dict[str, object]] = []
    for index, entry in enumerate(load_cookie_entries(cookie_file_path)):
        name = str(entry.get("name", "")).strip()
        value = str(entry.get("value", "")).strip()
        domain = str(entry.get("domain", "")).strip().lower()
        if not name or not value or not domain:
            continue
        if domain not in X_COOKIE_DOMAINS:
            raise ValueError(f"cookie[{index}] has unsupported domain for X auth import: {domain}")
        cookie: dict[str, object] = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": str(entry.get("path", "/") or "/"),
            "httpOnly": bool(entry.get("httpOnly", False)),
            "secure": bool(entry.get("secure", True)),
        }
        expiry = entry.get("expires", entry.get("expiry"))
        if isinstance(expiry, (int, float)) and expiry > 0:
            cookie["expiry"] = float(expiry)
        same_site = str(entry.get("sameSite", "")).strip()
        if same_site in {"Lax", "None", "Strict"}:
            cookie["sameSite"] = same_site
        cookies.append(cookie)
    by_name = {str(cookie["name"]): cookie for cookie in cookies}
    missing = sorted({"auth_token", "ct0"} - set(by_name))
    if missing:
        raise ValueError(f"Imported X cookie file is missing required cookies: {', '.join(missing)}")
    return cookies


def import_x_cookies(
    source_path: Path,
    *,
    output_path: Path = X_COOKIE_FILE,
    force: bool = False,
) -> list[str]:
    if output_path.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing cookie file without --force: {output_path}")
    cookies = normalize_x_cookie_entries(source_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(cookies, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    names = sorted({str(cookie["name"]) for cookie in cookies})
    return names


def _cookie_expiry_text(cookie: dict[str, object]) -> str:
    raw_expiry = cookie.get("expires", cookie.get("expiry"))
    if not isinstance(raw_expiry, (int, float)) or raw_expiry <= 0:
        return "session/unknown"
    try:
        return datetime.fromtimestamp(float(raw_expiry), tz=timezone.utc).isoformat(timespec="seconds")
    except (OSError, OverflowError, ValueError):
        return "invalid"


def x_auth_status_lines(
    *,
    auth_state_path: Path = X_AUTH_STATE_FILE,
    cookie_file_path: Path | None = None,
) -> list[str]:
    cookie_file = resolve_x_cookie_file(cookie_file_path)
    rows = [
        ("auth_state", auth_state_path, {"auth_token"}),
        ("cookie_file", cookie_file, {"auth_token", "ct0"}),
    ]
    lines = ["X auth status:"]
    found_required: set[str] = set()
    for label, path, required in rows:
        if path is None:
            lines.append(f"[WARN] {label}: not configured")
            continue
        if not path.exists():
            lines.append(f"[WARN] {label}: missing ({path})")
            continue
        try:
            cookies = load_cookie_entries(path)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            lines.append(f"[FAIL] {label}: unreadable ({path}): {type(exc).__name__}: {exc}")
            continue
        by_name = {str(cookie.get("name", "")): cookie for cookie in cookies}
        present = sorted(name for name in required if name in by_name)
        missing = sorted(required - set(by_name))
        found_required.update(present)
        if missing:
            lines.append(f"[WARN] {label}: missing {', '.join(missing)} ({path})")
        else:
            lines.append(f"[OK] {label}: required cookies present ({path})")
        visible_names = ", ".join(sorted(name for name in by_name if name)) or "none"
        lines.append(f"       cookie names: {visible_names}")
        for name in present:
            domain = str(by_name[name].get("domain", "") or "unknown")
            lines.append(f"       {name}: domain={domain}, expires={_cookie_expiry_text(by_name[name])}")
    if {"auth_token", "ct0"}.issubset(found_required):
        lines.append("[OK] authenticated cookie pair available for X web requests")
    elif "auth_token" in found_required:
        lines.append("[WARN] auth_token found, but ct0 is missing; X web requests may fail")
    else:
        lines.append("[FAIL] no auth_token found; authenticated following collection will redirect to login")
    lines.append("[WARN] auth files contain secret-bearing browser state; do not commit or share them")
    return lines


def fill_first_visible(page: object, selectors: list[str], value: str) -> bool:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.is_visible(timeout=5000):
                locator.fill("")
                locator.fill(value)
                return True
        except Exception:
            continue
    return False


def type_first_visible(
    page: object,
    selectors: list[str],
    value: str,
    *,
    timeout_ms: int = 5000,
    delay_ms: int = 90,
) -> bool:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.is_visible(timeout=timeout_ms):
                locator.click()
                locator.fill("")
                locator.type(value, delay=delay_ms)
                return True
        except Exception:
            continue
    return False


def add_x_stealth_init_script(context: object) -> None:
    context.add_init_script(
        """
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'languages', { get: () => ['ja-JP', 'ja', 'en-US', 'en'] });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        window.chrome = window.chrome || { runtime: {} };
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters && parameters.name === 'notifications'
                ? Promise.resolve({ state: Notification.permission })
                : originalQuery(parameters)
        );
        """
    )


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
        if seed_entities[account_id]["type"] not in {"person", "community", "location", "platform", "content"}:
            raise ValueError(
                f"x_profile_sources[{index}] account type must be person/community/location/platform/content: {account_id}"
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
                "collect_following": bool(entry.get("collect_following", True)),
                "following_limit": int(entry.get("following_limit", DEFAULT_FOLLOWING_LIMIT)),
            }
        )
    return sources


def fetch_page(url: str, timeout: int = DEFAULT_TIMEOUT) -> tuple[bytes, str]:
    """Fetch HTML bytes; prefer Scrapling (TLS mimic + stealth headers), fall back to requests."""
    try:
        from scrapling.fetchers import Fetcher
    except ImportError:
        Fetcher = None  # type: ignore[assignment,misc]

    if Fetcher is not None:
        try:
            page = Fetcher.get(
                url,
                timeout=timeout,
                impersonate="chrome",
                stealthy_headers=True,
            )
            status = int(getattr(page, "status", 200) or 200)
            if status >= 400:
                raise requests.HTTPError(f"{status} Client Error for url: {getattr(page, 'url', url)}")
            body = page.body
            if not isinstance(body, bytes):
                body = bytes(body)
            return body, str(page.url)
        except requests.RequestException:
            raise
        except Exception:
            pass

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


def extract_x_following_handles_from_hrefs(
    hrefs: list[str],
    *,
    source_handle: str = "",
) -> list[str]:
    handles: list[str] = []
    source_lower = source_handle.strip().lstrip("@").casefold()
    for raw_href in hrefs:
        href = str(raw_href).strip()
        if not href:
            continue
        handle = ""
        if href.startswith(("http://", "https://")):
            handle = extract_x_handle_from_url(href)
        elif href.startswith("/"):
            cleaned = href.split("?", 1)[0].split("#", 1)[0]
            segments = [segment for segment in cleaned.split("/") if segment]
            if len(segments) != 1:
                continue
            handle = segments[0]
        if not handle or not re.fullmatch(r"[A-Za-z0-9_]{1,15}", handle):
            continue
        lowered = handle.casefold()
        if lowered == source_lower or lowered in X_RESERVED_PATH_SEGMENTS:
            continue
        if handle not in handles:
            handles.append(handle)
    return handles


def _wait_after_manual_x_login(ready_file: Path | None) -> None:
    if ready_file is None:
        input()
        return
    ready_path = ready_file.resolve()
    ready_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        ready_path.unlink()
    except FileNotFoundError:
        pass
    print("=" * 60)
    print("X login browser opened.")
    print("Log in in the browser, then create this file to save auth state:")
    print(f"  {ready_path}")
    print("Example (PowerShell):  New-Item -ItemType File -Path .\\data\\.x_login_ready -Force")
    print("=" * 60)
    while not ready_path.exists():
        time.sleep(0.5)
    try:
        ready_path.unlink()
    except OSError:
        pass


def login_x_and_save_auth_state(
    auth_state_path: Path = X_AUTH_STATE_FILE,
    *,
    ready_file: Path | None = None,
) -> Path:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is required for authenticated X login.") from exc
    try:
        from playwright_stealth import Stealth
    except ImportError:
        Stealth = None  # type: ignore[assignment,misc]

    def run_manual_login_attempt(playwright: object, *, use_minimal_stealth: bool) -> None:
        browser = playwright.chromium.launch(
            headless=False,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = browser.new_context(
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
            viewport={"width": 1366, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        )
        if use_minimal_stealth:
            add_x_stealth_init_script(context)
        try:
            page = context.new_page()
            page.goto("https://x.com/i/flow/login", wait_until="domcontentloaded", timeout=120000)
            page.wait_for_timeout(3000)
            body_text = page.locator("body").text_content(timeout=10000) or ""
            if "JavaScriptを使用できません" in body_text:
                raise RuntimeError("X returned the JavaScript-disabled page during manual login.")
            if ready_file is None:
                print("=" * 60)
                print("X login browser opened.")
                print("Log in in the browser, then press Enter here to save auth state.")
                print("=" * 60)
            _wait_after_manual_x_login(ready_file)
            page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=120000)
            page.wait_for_timeout(3000)
            cookies = context.cookies()
            has_auth = any(c["name"] == "auth_token" for c in cookies)
            if not has_auth:
                body = page.locator("body").inner_text(timeout=5000) or ""
                if "login" in page.url.casefold() or "flow/login" in page.url:
                    raise RuntimeError("Still on X login flow after manual confirmation.")
                if "For you" not in body and "タイムライン" not in body and "Following" not in body:
                    print("WARNING: Login may not be complete. Continuing anyway...")
            context.storage_state(path=str(auth_state_path))
        finally:
            browser.close()

    auth_state_path.parent.mkdir(parents=True, exist_ok=True)
    attempts = [(True, False), (False, True), (False, False)] if Stealth is not None else [(False, True), (False, False)]
    last_error: RuntimeError | None = None
    for use_stealth_wrapper, use_minimal_stealth in attempts:
        try:
            if use_stealth_wrapper:
                with Stealth().use_sync(sync_playwright()) as playwright:
                    run_manual_login_attempt(playwright, use_minimal_stealth=False)
            else:
                with sync_playwright() as playwright:
                    run_manual_login_attempt(playwright, use_minimal_stealth=use_minimal_stealth)
            return auth_state_path
        except RuntimeError as exc:
            last_error = exc
            if "JavaScript-disabled" in str(exc) and (use_stealth_wrapper or use_minimal_stealth):
                next_mode = "minimal stealth" if use_stealth_wrapper else "plain Playwright"
                print(f"[WARN] X returned the JavaScript-disabled page; retrying with {next_mode}.")
                continue
            raise
    if last_error is not None:
        raise last_error
    return auth_state_path


def auto_login_x_and_save_auth_state(
    auth_state_path: Path = X_AUTH_STATE_FILE,
    *,
    dotenv_path: Path | None = None,
) -> Path:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is required for authenticated X login.") from exc
    try:
        from playwright_stealth import Stealth
    except ImportError:
        Stealth = None  # type: ignore[assignment,misc]

    username, password = load_x_login_credentials(dotenv_path)
    if not username or not password:
        dotenv_note = f" or dotenv file {dotenv_path}" if dotenv_path is not None else ""
        raise RuntimeError(f"Missing TWITTER_USERNAME / TWITTER_PASSWORD in environment{dotenv_note}.")

    def run_login_attempt(playwright: object, *, use_minimal_stealth: bool) -> None:
        browser = playwright.chromium.launch(
            headless=False,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = browser.new_context(
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
            viewport={"width": 1366, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        )
        if use_minimal_stealth:
            add_x_stealth_init_script(context)
        try:
            page = context.new_page()
            page.goto("https://x.com/i/flow/login", wait_until="domcontentloaded", timeout=120000)
            page.wait_for_timeout(8000)

            body_text = page.locator("body").text_content(timeout=10000) or ""
            if "JavaScriptを使用できません" in body_text:
                raise RuntimeError("X returned the JavaScript-disabled page during automatic login.")
            if "問題が発生" in body_text or "やりなおす" in body_text:
                page.goto("https://x.com/i/flow/login", wait_until="domcontentloaded", timeout=120000)
                page.wait_for_timeout(8000)

            if not type_first_visible(
                page,
                ['input[autocomplete="username"]', 'input[name="text"]', 'input[type="text"]'],
                username,
                timeout_ms=10000,
            ):
                raise RuntimeError("Could not find the X username input.")
            page.keyboard.press("Enter")
            page.wait_for_timeout(5000)

            challenge_input = page.locator('input[data-testid="ocfEnterTextTextInput"]').first
            try:
                if challenge_input.is_visible(timeout=4000):
                    challenge_input.click()
                    challenge_input.fill("")
                    challenge_input.type(username, delay=90)
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(4000)
            except Exception:
                pass

            if not type_first_visible(page, ['input[name="password"]'], password, timeout_ms=15000):
                raise RuntimeError("Could not find the X password input.")
            page.keyboard.press("Enter")
            page.wait_for_timeout(8000)
            page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=120000)
            page.wait_for_timeout(5000)
            cookies = context.cookies()
            has_auth = any(c["name"] == "auth_token" for c in cookies)
            if not has_auth:
                if "login" in page.url.casefold() or "flow/login" in page.url:
                    raise RuntimeError("Automatic X login still ended on the login flow.")
                body = page.locator("body").inner_text(timeout=5000) or ""
                if "For you" not in body and "タイムライン" not in body:
                    raise RuntimeError("Automatic X login did not reach the timeline.")
            context.storage_state(path=str(auth_state_path))
        finally:
            browser.close()

    auth_state_path.parent.mkdir(parents=True, exist_ok=True)
    attempts = [(True, False), (False, True), (False, False)] if Stealth is not None else [(False, True), (False, False)]
    last_error: RuntimeError | None = None
    for use_stealth_wrapper, use_minimal_stealth in attempts:
        try:
            if use_stealth_wrapper:
                with Stealth().use_sync(sync_playwright()) as playwright:
                    run_login_attempt(playwright, use_minimal_stealth=False)
            else:
                with sync_playwright() as playwright:
                    run_login_attempt(playwright, use_minimal_stealth=use_minimal_stealth)
            return auth_state_path
        except RuntimeError as exc:
            last_error = exc
            if "JavaScript-disabled" in str(exc) and (use_stealth_wrapper or use_minimal_stealth):
                next_mode = "minimal stealth" if use_stealth_wrapper else "plain Playwright"
                print(f"[WARN] X returned the JavaScript-disabled page; retrying with {next_mode}.")
                continue
            raise
    if last_error is not None:
        raise last_error
    return auth_state_path


def collect_authenticated_following_handles(
    source_url: str,
    *,
    auth_state_path: Path = X_AUTH_STATE_FILE,
    cookie_file_path: Path | None = None,
    limit: int = DEFAULT_FOLLOWING_LIMIT,
) -> tuple[list[str], str]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is required for authenticated following collection.") from exc

    source_handle = extract_x_handle_from_url(source_url)
    if not source_handle:
        raise ValueError(f"Could not extract X handle from URL: {source_url}")
    following_url = f"https://x.com/{source_handle}/following"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        auth_state_has_auth = cookie_file_has_names(auth_state_path, {"auth_token"})
        cookie_file = resolve_x_cookie_file(cookie_file_path)
        if auth_state_has_auth:
            context = browser.new_context(storage_state=str(auth_state_path))
        else:
            if cookie_file is None or not cookie_file.exists():
                browser.close()
                raise FileNotFoundError(
                    f"Missing X auth state file: {auth_state_path}. Provide --cookie-file or create auth state first."
                )
            context = browser.new_context()
            context.add_cookies(load_playwright_cookies(cookie_file))
        page = context.new_page()
        page.goto(following_url, wait_until="domcontentloaded", timeout=120000)
        page.wait_for_timeout(2500)
        if "login" in page.url.casefold():
            browser.close()
            raise RuntimeError("Authenticated following page redirected to X login.")
        try:
            page.locator('div[data-testid="cellInnerDiv"] a[href]').first.wait_for(timeout=12000)
        except Exception:
            page.wait_for_timeout(4000)

        collected: list[str] = []
        stagnant_rounds = 0
        for _ in range(8):
            hrefs = page.eval_on_selector_all(
                'div[data-testid="cellInnerDiv"] a[href]',
                "elements => elements.map(element => element.getAttribute('href') || '')",
            )
            handles = extract_x_following_handles_from_hrefs(hrefs, source_handle=source_handle)
            previous_count = len(collected)
            for handle in handles:
                if handle not in collected:
                    collected.append(handle)
            if len(collected) > previous_count:
                stagnant_rounds = 0
            else:
                stagnant_rounds += 1
            if len(collected) >= limit or stagnant_rounds >= 2:
                break
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1800)

        browser.close()
    return collected[:limit], following_url


def build_following_observations(
    source_account_id: str,
    followed_handles: list[str],
    *,
    handle_to_account_id: dict[str, str],
    source_url: str,
    following_url: str,
) -> list[dict[str, object]]:
    observations: list[dict[str, object]] = []
    seen_targets: set[str] = set()
    for handle in followed_handles:
        target_id = handle_to_account_id.get(handle.casefold(), "")
        if not target_id or target_id == source_account_id or target_id in seen_targets:
            continue
        seen_targets.add(target_id)
        observations.append(
            {
                "target": target_id,
                "type": "follow",
                "description": f"Authenticated X following list shows this account follows @{handle}.",
                "source_urls": [source_url, following_url],
                "confidence": 0.64,
                "evidence_kind": "mixed",
                "needs_review": True,
                "review_notes": "Collected from an authenticated X following page because logged-out following pages redirect to login.",
            }
        )
    return observations


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


def is_missing_or_default_icon(icon_url: object) -> bool:
    """True when icon is empty or X's default/sticky placeholder avatar."""
    value = str(icon_url or "").strip()
    if not value:
        return True
    lowered = value.casefold()
    return (
        "/default_profile_" in lowered
        or "abs.twimg.com/sticky/default_profile_images" in lowered
        or lowered.endswith("default_profile.png")
    )


def extract_profile_image_url_from_user_object(user_object: dict[str, object]) -> str:
    for field in ("profile_image_url_https", "profile_image_url"):
        image_url = normalize_x_profile_image_url(str(user_object.get(field, "")).strip())
        if image_url:
            return image_url
    return ""


def extract_follower_count_from_user_object(user_object: dict[str, object]) -> int:
    for field in ("followers_count", "normal_followers_count"):
        value = user_object.get(field)
        if value in (None, ""):
            continue
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            continue
    return 0


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
                    "follower_count": str(extract_follower_count_from_user_object(user_object)),
                }
            match_index = script_text.find(handle_pattern, match_index + len(handle_pattern))

    return {"handle": handle, "follower_count": "0"}


def fetch_x_api_user_details(
    sources: list[dict[str, object]],
    bearer_token: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, dict[str, object]]:
    if not bearer_token:
        return {}
    handle_to_account_id = {
        extract_x_handle_from_url(str(source["url"])).casefold(): str(source["account_id"])
        for source in sources
        if extract_x_handle_from_url(str(source["url"]))
    }
    handles = sorted(handle_to_account_id)
    details_by_account_id: dict[str, dict[str, object]] = {}
    headers = {"Authorization": f"Bearer {bearer_token}", "User-Agent": USER_AGENT}
    for start in range(0, len(handles), 100):
        batch = handles[start : start + 100]
        response = requests.get(
            X_API_USERS_BY_URL,
            params={"usernames": ",".join(batch), "user.fields": X_API_USER_FIELDS},
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        for user in payload.get("data", []):
            if not isinstance(user, dict):
                continue
            username = str(user.get("username", "")).casefold()
            account_id = handle_to_account_id.get(username)
            if account_id:
                details_by_account_id[account_id] = user
    return details_by_account_id


def merge_x_api_user_details_into_snapshot(
    snapshot: dict[str, object],
    user_details: dict[str, object],
) -> dict[str, object]:
    merged = dict(snapshot)
    username = normalize_embedded_text(user_details.get("username"))
    display_name = normalize_embedded_text(user_details.get("name"))
    description = normalize_embedded_text(user_details.get("description"))
    location = normalize_embedded_text(user_details.get("location"))
    external_url = normalize_embedded_text(user_details.get("url"))
    profile_image_url = normalize_x_profile_image_url(str(user_details.get("profile_image_url", "")).strip())
    public_metrics = user_details.get("public_metrics", {})
    follower_count = 0
    if isinstance(public_metrics, dict):
        follower_count = extract_follower_count_from_user_object(
            {"followers_count": public_metrics.get("followers_count")}
        )

    if username:
        merged["profile_url"] = f"https://x.com/{username}"
        merged["links"] = list(dict.fromkeys([*merged.get("links", []), f"https://x.com/{username}"]))
    if external_url:
        merged["links"] = list(dict.fromkeys([*merged.get("links", []), external_url]))
    if profile_image_url and not is_missing_or_default_icon(profile_image_url):
        merged["icon_url"] = profile_image_url
    if follower_count > int(merged.get("follower_count", 0) or 0):
        merged["follower_count"] = follower_count
    if description:
        merged["summary"] = compress_line(description, max_chars=MAX_SUMMARY_CHARS)
    profile_lines = []
    if display_name or username:
        profile_lines.append(f"{display_name or '@' + username} (@{username})" if username else display_name)
    if description:
        profile_lines.append(description)
    if location:
        profile_lines.append(f"Location: {location}")
    if profile_lines:
        merged["profile_text"] = compress_profile_text(profile_lines)
    merged["review_notes"] = " ".join(
        dict.fromkeys(
            [
                str(merged.get("review_notes", "")).strip(),
                "X API public profile fields were used to refresh icon and follower coverage.",
            ]
        )
    ).strip()
    return merged


def x_web_cookie_headers(cookie_file_path: Path) -> dict[str, str]:
    cookies = load_playwright_cookies(cookie_file_path)
    cookie_jar = {str(cookie["name"]): str(cookie["value"]) for cookie in cookies}
    auth_token = cookie_jar.get("auth_token", "")
    csrf_token = cookie_jar.get("ct0", "")
    if not auth_token or not csrf_token:
        raise RuntimeError(f"Cookie file must include auth_token and ct0: {cookie_file_path}")
    return {
        "authorization": X_WEB_BEARER_TOKEN,
        "x-csrf-token": csrf_token,
        "x-twitter-active-user": "yes",
        "x-twitter-auth-type": "OAuth2Session",
        "x-twitter-client-language": "ja",
        "user-agent": "Mozilla/5.0",
        "cookie": "; ".join(f"{key}={value}" for key, value in cookie_jar.items()),
    }


def fetch_x_web_user_details(
    handle: str,
    headers: dict[str, str],
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, object]:
    params = {
        "variables": json.dumps(
            {"screen_name": handle, "withGrokTranslatedBio": True},
            separators=(",", ":"),
        ),
        "features": json.dumps(X_WEB_USER_FEATURES, separators=(",", ":")),
        "fieldToggles": json.dumps(X_WEB_USER_FIELD_TOGGLES, separators=(",", ":")),
    }
    response = requests.get(
        X_WEB_USER_BY_SCREEN_NAME_URL,
        params=params,
        headers={**headers, "referer": f"https://x.com/{handle}"},
        timeout=timeout,
    )
    if response.status_code == 429:
        raise RuntimeError("X web profile lookup was rate limited.")
    response.raise_for_status()
    payload = response.json()
    result = (((payload.get("data") or {}).get("user") or {}).get("result") or {})
    if result.get("__typename") != "User":
        return {}
    return result


def merge_x_web_user_details_into_snapshot(
    snapshot: dict[str, object],
    user_details: dict[str, object],
) -> dict[str, object]:
    core = user_details.get("core", {}) if isinstance(user_details.get("core"), dict) else {}
    legacy = user_details.get("legacy", {}) if isinstance(user_details.get("legacy"), dict) else {}
    avatar = user_details.get("avatar", {}) if isinstance(user_details.get("avatar"), dict) else {}
    location_payload = user_details.get("location", {}) if isinstance(user_details.get("location"), dict) else {}
    screen_name = normalize_embedded_text(core.get("screen_name"))
    display_name = normalize_embedded_text(core.get("name"))
    description = normalize_embedded_text(legacy.get("description"))
    location = normalize_embedded_text(location_payload.get("location") or legacy.get("location"))
    icon_url = normalize_x_profile_image_url(
        str(avatar.get("image_url") or legacy.get("profile_image_url_https") or "").strip()
    )
    follower_count = extract_follower_count_from_user_object(
        {
            "followers_count": legacy.get("followers_count"),
            "normal_followers_count": legacy.get("normal_followers_count"),
        }
    )
    api_payload = {
        "username": screen_name,
        "name": display_name,
        "description": description,
        "location": location,
        "profile_image_url": icon_url,
        "public_metrics": {"followers_count": follower_count},
    }
    merged = merge_x_api_user_details_into_snapshot(snapshot, api_payload)
    merged["review_notes"] = " ".join(
        dict.fromkeys(
            [
                str(merged.get("review_notes", "")).strip(),
                "Authenticated X web UserByScreenName public fields were used to refresh icon and follower coverage.",
            ]
        )
    ).strip()
    return merged


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
        "follower_count": embedded_profile.get("follower_count", "0"),
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
    profile_fetch_error: str = "",
    following_observations: list[dict[str, object]] | None = None,
    following_note: str = "",
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
    if profile_fetch_error:
        review_notes_parts.append(
            "Profile fetch failed during this run, so the snapshot fell back to the configured X URL and preserved minimal account metadata. "
            + compress_line(profile_fetch_error, max_chars=180)
        )
    if following_note.strip():
        review_notes_parts.append(following_note.strip())

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
        "follower_count": int(str(details.get("follower_count", "0") or "0")),
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
        "observations": list(following_observations or []),
    }


def collect_snapshots(
    sources: list[dict[str, object]],
    timeout: int = DEFAULT_TIMEOUT,
    continue_on_error: bool = True,
    *,
    max_links_override: int | None = None,
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
        max_links = int(source.get("max_links", DEFAULT_MAX_LINKS))
        if max_links_override is not None:
            max_links = max(1, max_links_override)
        snapshots.append(
            extract_snapshot_from_html(
                str(source["account_id"]),
                str(source["url"]),
                html,
                label=str(source.get("label", "")),
                fetched_url=fetched_url,
                max_links=max_links,
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
    auth_state_path: Path = X_AUTH_STATE_FILE,
    cookie_file_path: Path | None = None,
    x_api_bearer_token: str = "",
    *,
    following_limit_override: int | None = None,
) -> list[dict[str, object]]:
    snapshots: list[dict[str, object]] = []
    api_details_by_account_id: dict[str, dict[str, object]] = {}
    if x_api_bearer_token:
        try:
            api_details_by_account_id = fetch_x_api_user_details(
                sources,
                x_api_bearer_token,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            if not continue_on_error:
                raise
            print(f"[WARN] skipped X API profile lookup: {exc}")
    handle_to_account_id = {
        extract_x_handle_from_url(str(source["url"])).casefold(): str(source["account_id"])
        for source in sources
        if extract_x_handle_from_url(str(source["url"]))
    }
    for source in sources:
        source_url = str(source["url"])
        html: str | bytes = "<html><body></body></html>"
        fetched_url = source_url
        profile_fetch_error = ""
        try:
            html, fetched_url = fetch_page(source_url, timeout=timeout)
        except requests.RequestException as exc:
            if not continue_on_error:
                raise
            profile_fetch_error = str(exc)
            print(f"[WARN] skipped X profile {source['url']}: {exc}")
        pinned_post_url = str(source.get("pinned_post_url", "")).strip()
        pinned_post_html: str | bytes | None = None
        pinned_post_fetched_url = ""
        pinned_post_fetch_error = ""
        following_observations: list[dict[str, object]] = []
        following_note = ""
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
        if bool(source.get("collect_following", False)):
            try:
                following_limit = int(source.get("following_limit", DEFAULT_FOLLOWING_LIMIT))
                if following_limit_override is not None:
                    following_limit = max(1, following_limit_override)
                followed_handles, following_url = collect_authenticated_following_handles(
                    str(source["url"]),
                    auth_state_path=auth_state_path,
                    cookie_file_path=cookie_file_path,
                    limit=following_limit,
                )
                following_observations = build_following_observations(
                    str(source["account_id"]),
                    followed_handles,
                    handle_to_account_id=handle_to_account_id,
                    source_url=str(source["url"]),
                    following_url=following_url,
                )
                following_note = (
                    f"Authenticated X following collection saw {len(followed_handles)} handles and matched "
                    f"{len(following_observations)} tracked accounts."
                )
            except Exception as exc:
                following_note = (
                    "Authenticated X following collection was requested but skipped: "
                    + compress_line(str(exc), max_chars=180)
                )
                if not continue_on_error:
                    raise
                print(f"[WARN] skipped authenticated following for {source['url']}: {exc}")
        snapshot = extract_x_profile_snapshot(
            str(source["account_id"]),
            source_url,
            html,
            label=str(source.get("label", "")),
            fetched_url=fetched_url,
            pinned_post_url=pinned_post_url,
            pinned_post_html=pinned_post_html,
            pinned_post_fetched_url=pinned_post_fetched_url or None,
            pinned_post_fetch_error=pinned_post_fetch_error,
            profile_fetch_error=profile_fetch_error,
            following_observations=following_observations,
            following_note=following_note,
        )
        api_details = api_details_by_account_id.get(str(source["account_id"]))
        if api_details:
            snapshot = merge_x_api_user_details_into_snapshot(snapshot, api_details)
        snapshots.append(snapshot)
    return snapshots


def merge_generated_snapshots(snapshots: list[dict[str, object]]) -> list[dict[str, object]]:
    def add_note_parts(notes: list[str], note: str) -> None:
        for part in re.split(r"(?<=\.)\s+", note.strip()):
            normalized = part.strip()
            if normalized and normalized not in notes:
                notes.append(normalized)

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
            "follower_count": 0,
        }
        notes: list[str] = []
        observations: list[dict[str, object]] = []

        for snapshot in ordered:
            for field in scalar_fields:
                value = str(snapshot.get(field, "")).strip()
                if value and not str(merged.get(field, "")).strip():
                    merged[field] = value
            follower_count = int(snapshot.get("follower_count", 0) or 0)
            if follower_count > int(merged.get("follower_count", 0) or 0):
                merged["follower_count"] = follower_count
            merged["links"] = list(dict.fromkeys([*merged["links"], *snapshot.get("links", [])]))
            merged["needs_review"] = bool(merged["needs_review"] or snapshot.get("needs_review", False))
            note = str(snapshot.get("review_notes", "")).strip()
            if note:
                add_note_parts(notes, note)
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


def load_existing_generated_snapshots(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("generated snapshot file must contain a list")
    snapshots: list[dict[str, object]] = []
    for entry in payload:
        if isinstance(entry, dict) and str(entry.get("account_id", "")).strip():
            snapshots.append(entry)
    return snapshots


def load_x_web_profile_skip(path: Path = X_WEB_PROFILE_SKIP_FILE) -> dict[str, object]:
    if not path.exists():
        return {"profiles": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("X web profile skip file must contain an object")
    profiles = payload.get("profiles", {})
    if not isinstance(profiles, dict):
        raise ValueError("X web profile skip file profiles must contain an object")
    return {"profiles": profiles}


def save_x_web_profile_skip(payload: dict[str, object], path: Path = X_WEB_PROFILE_SKIP_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def record_x_web_profile_skip(
    payload: dict[str, object],
    *,
    account_id: str,
    handle: str,
    source_url: str,
    reason: str,
) -> bool:
    profiles = payload.setdefault("profiles", {})
    if not isinstance(profiles, dict):
        raise ValueError("X web profile skip payload profiles must contain an object")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    existing = profiles.get(account_id)
    entry = {
        "handle": handle,
        "source_url": source_url,
        "reason": reason,
        "last_checked_at": now,
    }
    if existing == entry:
        return False
    profiles[account_id] = entry
    return True


def merge_refreshed_snapshots_into_existing(
    existing_snapshots: list[dict[str, object]],
    refreshed_snapshots: list[dict[str, object]],
) -> list[dict[str, object]]:
    def add_note_parts(notes: list[str], note: str) -> None:
        for part in re.split(r"(?<=\.)\s+", note.strip()):
            normalized = part.strip()
            if normalized and normalized not in notes:
                notes.append(normalized)

    def merge_one(existing: dict[str, object], refreshed: dict[str, object]) -> dict[str, object]:
        merged = {**existing}
        for field in (
            "profile_url",
            "pinned_post_url",
            "icon_url",
            "profile_text",
            "pinned_post_text",
            "summary",
            "evidence_kind",
            "summary_evidence_kind",
        ):
            value = str(refreshed.get(field, "")).strip()
            if value and not str(merged.get(field, "")).strip():
                merged[field] = value
        follower_count = int(refreshed.get("follower_count", 0) or 0)
        if follower_count > int(merged.get("follower_count", 0) or 0):
            merged["follower_count"] = follower_count
        refreshed_icon = str(refreshed.get("icon_url", "")).strip()
        existing_icon = str(merged.get("icon_url", "")).strip()
        # Never persist X default/sticky avatars as if they were real icons.
        if refreshed_icon and not is_missing_or_default_icon(refreshed_icon):
            if is_missing_or_default_icon(existing_icon) or existing_icon != refreshed_icon:
                merged["icon_url"] = refreshed_icon
        elif is_missing_or_default_icon(existing_icon):
            merged["icon_url"] = ""
        merged["links"] = list(
            dict.fromkeys([*list(merged.get("links", [])), *list(refreshed.get("links", []))])
        )
        merged["needs_review"] = bool(merged.get("needs_review", False) or refreshed.get("needs_review", False))
        notes: list[str] = []
        add_note_parts(notes, str(merged.get("review_notes", "")))
        add_note_parts(notes, str(refreshed.get("review_notes", "")))
        merged["review_notes"] = " ".join(notes).strip()
        observations = list(merged.get("observations", []))
        for observation in refreshed.get("observations", []):
            if observation not in observations:
                observations.append(observation)
        merged["observations"] = observations

        refreshed_collector = refreshed.get("collector", {})
        existing_collector = merged.get("collector", {})
        if isinstance(refreshed_collector, dict) and refreshed_collector:
            if isinstance(existing_collector, dict) and isinstance(existing_collector.get("sources"), list):
                sources = list(existing_collector["sources"])
                if refreshed_collector not in sources:
                    sources.append(refreshed_collector)
                merged["collector"] = {**existing_collector, "sources": sources}
            elif isinstance(existing_collector, dict) and existing_collector:
                sources = [existing_collector]
                if refreshed_collector not in sources:
                    sources.append(refreshed_collector)
                merged["collector"] = {"sources": sources}
            else:
                merged["collector"] = refreshed_collector
        return merged

    if not refreshed_snapshots:
        return existing_snapshots
    refreshed_by_id = {
        str(snapshot.get("account_id", "")).strip(): snapshot
        for snapshot in refreshed_snapshots
        if str(snapshot.get("account_id", "")).strip()
    }
    seen: set[str] = set()
    merged_snapshots: list[dict[str, object]] = []
    for snapshot in existing_snapshots:
        account_id = str(snapshot.get("account_id", "")).strip()
        refreshed = refreshed_by_id.get(account_id)
        if refreshed:
            merged_snapshots.append(merge_one(snapshot, refreshed))
            seen.add(account_id)
        else:
            merged_snapshots.append(snapshot)
    for account_id in sorted(set(refreshed_by_id) - seen):
        merged_snapshots.append(refreshed_by_id[account_id])
    return merged_snapshots


def preserve_missing_generated_snapshots(
    existing_snapshots: list[dict[str, object]],
    fresh_snapshots: list[dict[str, object]],
    *,
    configured_account_ids: set[str],
) -> list[dict[str, object]]:
    fresh_account_ids = {
        str(snapshot.get("account_id", "")).strip()
        for snapshot in fresh_snapshots
        if str(snapshot.get("account_id", "")).strip()
    }
    preserved: list[dict[str, object]] = []
    for snapshot in existing_snapshots:
        account_id = str(snapshot.get("account_id", "")).strip()
        if not account_id or account_id in fresh_account_ids or account_id not in configured_account_ids:
            continue
        preserved.append(snapshot)
    return preserved


def refresh_missing_x_web_profiles(
    *,
    x_profile_config_path: Path = X_PROFILE_CONFIG,
    output_path: Path = GENERATED_SNAPSHOT_FILE,
    cookie_file_path: Path | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    limit: int | None = None,
    pause_seconds: float = 0.25,
    skip_file_path: Path = X_WEB_PROFILE_SKIP_FILE,
    retry_skipped: bool = False,
    force_refresh_icons: bool = False,
    account_ids: set[str] | None = None,
) -> list[dict[str, object]]:
    def snapshot_has_x_web_profile(snapshot: dict[str, object]) -> bool:
        collector_meta = snapshot.get("collector", {})
        if not isinstance(collector_meta, dict):
            return False
        if collector_meta.get("type") == "x_web_profile":
            return True
        sources = collector_meta.get("sources", [])
        if not isinstance(sources, list):
            return False
        return any(isinstance(source, dict) and source.get("type") == "x_web_profile" for source in sources)

    def snapshot_needs_icon_or_followers(snapshot: dict[str, object] | None) -> bool:
        if force_refresh_icons:
            return True
        if not snapshot:
            return True
        # Always re-fetch when avatar is empty/default, even if followers already exist.
        if is_missing_or_default_icon(snapshot.get("icon_url")):
            return True
        # Still chase first-time profile coverage when followers are unknown and
        # we never successfully completed an x_web_profile pass.
        if int(snapshot.get("follower_count", 0) or 0) <= 0 and not snapshot_has_x_web_profile(
            snapshot
        ):
            return True
        return False

    cookie_file = resolve_x_cookie_file(cookie_file_path)
    if cookie_file is None:
        raise FileNotFoundError("Missing cookie file for authenticated X web profile refresh.")
    headers = x_web_cookie_headers(cookie_file)
    existing_snapshots = load_existing_generated_snapshots(output_path)
    existing_by_id = {
        str(snapshot.get("account_id", "")).strip(): snapshot
        for snapshot in existing_snapshots
        if str(snapshot.get("account_id", "")).strip()
    }
    # Previously we skipped anyone with follower_count>0, which left many
    # default/empty icons unrefreshed. Select by missing icon OR followers.
    # force_refresh_icons re-pulls real avatars so outdated icons (e.g. エース)
    # are replaced with the current pbs image.
    skip_payload = load_x_web_profile_skip(skip_file_path)
    skipped_ids = set(skip_payload.get("profiles", {}).keys()) if not retry_skipped else set()
    allowed_ids = {item.strip() for item in (account_ids or set()) if str(item).strip()}
    sources = [
        source
        for source in load_x_profile_sources(x_profile_config_path)
        if str(source.get("account_id")) not in skipped_ids
        and extract_x_handle_from_url(str(source.get("url", "")))
        and (not allowed_ids or str(source.get("account_id")) in allowed_ids)
        and snapshot_needs_icon_or_followers(existing_by_id.get(str(source.get("account_id"))))
    ]
    # Prefer accounts that already have traffic but missing icons, then the rest.
    # When force-refreshing, prefer higher-follower hubs first (visible wrong icons).
    sources.sort(
        key=lambda source: (
            0
            if is_missing_or_default_icon(
                (existing_by_id.get(str(source.get("account_id"))) or {}).get("icon_url")
            )
            and int((existing_by_id.get(str(source.get("account_id"))) or {}).get("follower_count", 0) or 0) > 0
            else 1,
            -int((existing_by_id.get(str(source.get("account_id"))) or {}).get("follower_count", 0) or 0)
            if force_refresh_icons
            else 0,
            str(source["account_id"]),
        )
    )
    if limit is not None:
        sources = sources[: max(0, limit)]

    refreshed_snapshots: list[dict[str, object]] = []
    skip_changed = False
    for index, source in enumerate(sources, 1):
        account_id = str(source["account_id"])
        source_url = str(source["url"])
        handle = extract_x_handle_from_url(source_url)
        if not handle:
            continue
        try:
            details = fetch_x_web_user_details(handle, headers, timeout=timeout)
        except RuntimeError as exc:
            if "rate limited" in str(exc):
                print(f"[WARN] stopped X web profile refresh at {index}/{len(sources)}: {exc}")
                break
            print(f"[WARN] skipped X web profile {handle}: {exc}")
            continue
        except requests.RequestException as exc:
            print(f"[WARN] skipped X web profile {handle}: {exc}")
            continue
        if not details:
            skip_changed = record_x_web_profile_skip(
                skip_payload,
                account_id=account_id,
                handle=handle,
                source_url=source_url,
                reason="empty_or_unavailable",
            ) or skip_changed
            print(f"[SKIP] X web profile {account_id} @{handle}: empty or unavailable")
            continue
        snapshot = {
            "account_id": account_id,
            "profile_url": source_url,
            "pinned_post_url": "",
            "profile_text": f"@{handle}",
            "pinned_post_text": "",
            "links": [source_url],
            "summary": f"X profile for @{handle}.",
            "evidence_kind": "fact",
            "needs_review": True,
            "review_notes": "",
            "summary_evidence_kind": "fact",
            "snapshot_origin": "generated",
            "collector": {
                "type": "x_web_profile",
                "source_url": source_url,
                "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
            "observations": [],
        }
        refreshed = merge_x_web_user_details_into_snapshot(snapshot, details)
        # GraphQL sometimes only returns sticky default avatars. Fall back to
        # logged-out HTML embedded user data which still carries the real icon.
        if is_missing_or_default_icon(refreshed.get("icon_url")):
            try:
                html_body, fetched_url = fetch_page(source_url, timeout=timeout)
                soup = BeautifulSoup(html_body, "html.parser")
                embedded = extract_x_embedded_profile(
                    soup, source_url=source_url, fetched_url=fetched_url
                )
                avatar = normalize_x_profile_image_url(str(embedded.get("avatar_url", "")).strip())
                if avatar and not is_missing_or_default_icon(avatar):
                    refreshed["icon_url"] = avatar
                    notes = str(refreshed.get("review_notes", "")).strip()
                    refreshed["review_notes"] = " ".join(
                        dict.fromkeys(
                            [
                                notes,
                                "Logged-out X profile HTML embedded avatar was used after UserByScreenName returned a default icon.",
                            ]
                        )
                    ).strip()
            except Exception as exc:
                print(f"[WARN] HTML icon fallback failed for @{handle}: {exc}")
        if is_missing_or_default_icon(refreshed.get("icon_url")):
            refreshed["icon_url"] = ""
        refreshed_snapshots.append(refreshed)
        follower_count = int(refreshed.get("follower_count", 0) or 0)
        has_real_icon = not is_missing_or_default_icon(refreshed.get("icon_url"))
        icon_note = " with icon" if has_real_icon else " without real icon"
        print(f"[OK] X web profile {account_id} @{handle}: {follower_count} followers{icon_note}")
        if pause_seconds > 0:
            time.sleep(pause_seconds)

    if refreshed_snapshots:
        merged = merge_refreshed_snapshots_into_existing(existing_snapshots, refreshed_snapshots)
        output_path.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if skip_changed:
        save_x_web_profile_skip(skip_payload, skip_file_path)
    return refreshed_snapshots


def collect_to_file(
    config_path: Path = COLLECTOR_CONFIG,
    x_profile_config_path: Path = X_PROFILE_CONFIG,
    output_path: Path = GENERATED_SNAPSHOT_FILE,
    timeout: int = DEFAULT_TIMEOUT,
    continue_on_error: bool = True,
    auth_state_path: Path = X_AUTH_STATE_FILE,
    cookie_file_path: Path | None = None,
    x_api_bearer_token: str = "",
    *,
    max_links_override: int | None = None,
    following_limit_override: int | None = None,
    public_pages_only: bool = False,
) -> list[dict[str, object]]:
    cookie_file_path = resolve_x_cookie_file(cookie_file_path)
    x_api_bearer_token = x_api_bearer_token.strip() or load_x_api_bearer_token()
    existing_snapshots = load_existing_generated_snapshots(output_path)
    sources = load_collector_sources(config_path)
    public_snapshots = collect_snapshots(
        sources,
        timeout=timeout,
        continue_on_error=continue_on_error,
        max_links_override=max_links_override,
    )
    x_profile_sources = load_x_profile_sources(x_profile_config_path)
    if public_pages_only:
        x_profile_snapshots: list[dict[str, object]] = []
    else:
        x_profile_snapshots = collect_x_profile_snapshots(
            x_profile_sources,
            timeout=timeout,
            continue_on_error=continue_on_error,
            auth_state_path=auth_state_path,
            cookie_file_path=cookie_file_path,
            x_api_bearer_token=x_api_bearer_token,
            following_limit_override=following_limit_override,
        )
    configured_account_ids = {
        *[str(source["account_id"]) for source in sources],
        *[str(source["account_id"]) for source in x_profile_sources],
    }
    preserved_snapshots = preserve_missing_generated_snapshots(
        existing_snapshots,
        [*public_snapshots, *x_profile_snapshots],
        configured_account_ids=set(configured_account_ids),
    )
    snapshots = merge_generated_snapshots([*public_snapshots, *x_profile_snapshots, *preserved_snapshots])
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
    parser.add_argument(
        "--login-x",
        action="store_true",
        help="Open a browser for manual X login and save local auth state for following collection.",
    )
    parser.add_argument(
        "--login-x-auto",
        action="store_true",
        help="Use TWITTER_USERNAME / TWITTER_PASSWORD to log in automatically and save local auth state.",
    )
    parser.add_argument(
        "--check-x-auth",
        action="store_true",
        help="Safely report whether local X auth state/cookie files contain required authenticated cookies.",
    )
    parser.add_argument(
        "--import-x-cookies",
        type=Path,
        default=None,
        metavar="PATH",
        help="Import Selenium/Playwright-style X cookies containing auth_token and ct0 into the local cookie file.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow commands such as --import-x-cookies to overwrite their output file.",
    )
    parser.add_argument(
        "--login-x-ready-file",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "With --login-x only: after logging in in the browser, create this file to continue "
            "(avoids typing Enter in this terminal; file is removed after use)."
        ),
    )
    parser.add_argument(
        "--auth-state",
        type=Path,
        default=X_AUTH_STATE_FILE,
        help="Path to local X auth state JSON used for authenticated following collection.",
    )
    parser.add_argument(
        "--dotenv",
        type=Path,
        default=None,
        help="Optional dotenv file used to load TWITTER_USERNAME / TWITTER_PASSWORD and X API bearer tokens.",
    )
    parser.add_argument(
        "--x-api-bearer-token",
        default="",
        help="Optional X API v2 Bearer Token. If omitted, X_BEARER_TOKEN / TWITTER_BEARER_TOKEN / X_API_BEARER_TOKEN is used when present.",
    )
    parser.add_argument(
        "--cookie-file",
        type=Path,
        default=None,
        help="Optional Selenium-style cookie JSON used for authenticated following collection when auth state is missing.",
    )
    parser.add_argument(
        "--max-links",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Override max distinct platform links captured per public-page snapshot "
            f"(config default is {DEFAULT_MAX_LINKS} unless each source sets max_links)."
        ),
    )
    parser.add_argument(
        "--following-limit",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Override how many X following accounts to read per profile when authenticated "
            f"(config default is {DEFAULT_FOLLOWING_LIMIT} unless each source sets following_limit)."
        ),
    )
    parser.add_argument(
        "--public-pages-only",
        action="store_true",
        help="Skip X profile HTTP collection; refresh public-page snapshots only and preserve existing X snapshots.",
    )
    parser.add_argument(
        "--refresh-missing-x-web-profiles",
        action="store_true",
        help="Use authenticated X web UserByScreenName public fields to fill missing follower_count/icon_url snapshots.",
    )
    parser.add_argument(
        "--x-web-refresh-limit",
        type=int,
        default=None,
        metavar="N",
        help="Limit how many missing X web profiles to try in one run.",
    )
    parser.add_argument(
        "--x-web-refresh-pause",
        type=float,
        default=0.25,
        metavar="SECONDS",
        help="Pause between authenticated X web profile lookups.",
    )
    parser.add_argument(
        "--x-web-skip-file",
        type=Path,
        default=X_WEB_PROFILE_SKIP_FILE,
        help="JSON file that records unavailable X web profiles skipped by refresh runs.",
    )
    parser.add_argument(
        "--retry-x-web-skips",
        action="store_true",
        help="Retry profiles previously recorded as unavailable in the X web skip file.",
    )
    parser.add_argument(
        "--refresh-stale-x-icons",
        action="store_true",
        help=(
            "Re-fetch current X avatars even when an icon already exists "
            "(fixes outdated icons like エース@体刺し一門)."
        ),
    )
    parser.add_argument(
        "--x-web-account-ids",
        type=str,
        default="",
        help="Comma-separated account_ids to limit X web refresh (optional).",
    )
    args = parser.parse_args()
    args.cookie_file = resolve_x_cookie_file(args.cookie_file)

    if args.login_x:
        saved_path = login_x_and_save_auth_state(
            args.auth_state,
            ready_file=args.login_x_ready_file,
        )
        print(f"[OK] saved X auth state -> {saved_path}")
        return
    if args.login_x_auto:
        saved_path = auto_login_x_and_save_auth_state(args.auth_state, dotenv_path=args.dotenv)
        print(f"[OK] saved X auth state -> {saved_path}")
        return
    if args.check_x_auth:
        print("\n".join(x_auth_status_lines(auth_state_path=args.auth_state, cookie_file_path=args.cookie_file)))
        return
    if args.import_x_cookies is not None:
        output_path = args.cookie_file or X_COOKIE_FILE
        names = import_x_cookies(args.import_x_cookies, output_path=output_path, force=args.force)
        print(f"[OK] imported X cookies -> {output_path}")
        print(f"[OK] cookie names: {', '.join(names)}")
        print("[WARN] imported cookies contain authenticated secrets; do not commit or share them")
        return
    if args.refresh_missing_x_web_profiles or args.refresh_stale_x_icons:
        account_ids = {
            part.strip()
            for part in str(args.x_web_account_ids or "").split(",")
            if part.strip()
        }
        refreshed = refresh_missing_x_web_profiles(
            x_profile_config_path=args.x_profile_config,
            output_path=args.output,
            cookie_file_path=args.cookie_file,
            timeout=args.timeout,
            limit=args.x_web_refresh_limit,
            pause_seconds=args.x_web_refresh_pause,
            skip_file_path=args.x_web_skip_file,
            retry_skipped=args.retry_x_web_skips,
            force_refresh_icons=bool(args.refresh_stale_x_icons),
            account_ids=account_ids or None,
        )
        mode = "stale icons" if args.refresh_stale_x_icons else "missing X web profiles"
        print(f"[OK] refreshed {len(refreshed)} {mode} -> {args.output}")
        return

    snapshots = collect_to_file(
        args.config,
        args.x_profile_config,
        args.output,
        timeout=args.timeout,
        continue_on_error=not args.strict,
        auth_state_path=args.auth_state,
        cookie_file_path=args.cookie_file,
        x_api_bearer_token=args.x_api_bearer_token or load_x_api_bearer_token(args.dotenv),
        max_links_override=args.max_links,
        following_limit_override=args.following_limit,
        public_pages_only=args.public_pages_only,
    )
    print(
        f"[OK] collected {len(snapshots)} generated snapshots -> {args.output}"
    )


if __name__ == "__main__":
    main()
