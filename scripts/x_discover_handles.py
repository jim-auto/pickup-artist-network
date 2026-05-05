"""X の検索（ユーザー）からハンドルを拾い、シード候補にする。

ゲストのブラウザでは /search がログインへ飛ぶため、次のいずれかが必要:
  - data/.x_auth_state.json（collector.py --login-x 等）
  - data/.x_cookies.json（エクスポート済みクッキー JSON）

認証ファイルが無い場合は終了コード 2 でメッセージのみ（スレや検索エンジン HTML には依存しない）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import quote

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from collector import (
    X_AUTH_STATE_FILE,
    extract_x_following_handles_from_hrefs,
    load_playwright_cookies,
    resolve_x_cookie_file,
)
from growth_probe_candidates import RESERVED, HANDLE_SCENE, handle_to_id

SEED_FILE = ROOT / "seed_entities.txt"
X_PROFILE_FILE = ROOT / "data" / "x_profile_sources.json"

LABEL = "Xユーザー検索（Playwright・保存認証）"

DEFAULT_QUERIES = [
    "nanpa",
    "nampa",
    "pua",
    "nanpa pua",
    "nanpa coach",
    "street nanpa",
    "ナンパ",
    "ストナン",
    "ナンパ師",
]


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


def _discover_handles(
    queries: list[str],
    *,
    auth_state_path: Path,
    cookie_file_path: Path | None,
    pause_ms: int,
) -> set[str]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright が必要です: pip install playwright && playwright install chromium") from exc

    collected: set[str] = set()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        if auth_state_path.exists():
            context = browser.new_context(storage_state=str(auth_state_path))
        else:
            if not cookie_file_path or not cookie_file_path.exists():
                browser.close()
                raise FileNotFoundError(
                    "X の認証がありません。data/.x_auth_state.json を作るか、"
                    "data/.x_cookies.json を配置してください。"
                )
            context = browser.new_context()
            context.add_cookies(load_playwright_cookies(cookie_file_path))

        page = context.new_page()
        try:
            page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=120000)
            page.wait_for_timeout(1500)
            if "flow/login" in page.url:
                browser.close()
                raise RuntimeError(
                    "セッションが無効です。python collector.py --login-x で認証し直してください。"
                )
            for raw_q in queries:
                q = raw_q.strip()
                if not q:
                    continue
                url = f"https://x.com/search?q={quote(q)}&f=user"
                page.goto(url, wait_until="domcontentloaded", timeout=120000)
                page.wait_for_timeout(pause_ms)
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(800)
                if "flow/login" in page.url or "/login" in page.url:
                    browser.close()
                    raise RuntimeError(
                        "X がログイン画面にリダイレクトしました。"
                        "認証を更新してください（python collector.py --login-x）。"
                    )
                hrefs = page.eval_on_selector_all(
                    "a[href]",
                    "elements => elements.map(element => element.getAttribute('href') || '')",
                )
                normalized: list[str] = []
                for href in hrefs:
                    if not href or href.startswith("#"):
                        continue
                    if href.startswith("/search"):
                        continue
                    normalized.append(
                        f"https://x.com{href}" if href.startswith("/") else href
                    )
                for h in extract_x_following_handles_from_hrefs(
                    normalized,
                    source_handle="",
                ):
                    collected.add(h)
        finally:
            browser.close()
    return collected


def _filter_new_handles(raw: set[str], existing_cf: set[str], *, scene_only: bool) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for h in raw:
        hid = handle_to_id(h)
        if h.casefold() in RESERVED or hid.casefold() in RESERVED:
            continue
        if scene_only and not HANDLE_SCENE.search(h):
            continue
        if len(h) < 2 or len(h) > 15:
            continue
        if hid.casefold() in existing_cf or h.casefold() in existing_cf:
            continue
        rows.append((h, hid))
    rows.sort(key=lambda t: (t[1].lower(), t[0].lower()))
    return rows


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
    ap = argparse.ArgumentParser(description="X ユーザー検索からハンドルを抽出してシードに追加")
    ap.add_argument(
        "--query",
        action="append",
        dest="queries",
        help="検索語（複数指定可）。未指定時は既定の日本語・英語セット",
    )
    ap.add_argument(
        "--auth-state",
        type=Path,
        default=X_AUTH_STATE_FILE,
        help="Playwright storage state JSON",
    )
    ap.add_argument(
        "--cookie-file",
        type=Path,
        default=None,
        help="Selenium 形式クッキー JSON（未指定時は data/.x_cookies.json があれば使用）",
    )
    ap.add_argument(
        "--pause-ms",
        type=int,
        default=3500,
        help="各検索ページ読み込み後の待機（ミリ秒）",
    )
    ap.add_argument(
        "--scene-only",
        action="store_true",
        help="ハンドルがシーン由来パターンにマッチするものだけ残す",
    )
    ap.add_argument("--apply", action="store_true", help="seed_entities と x_profile_sources を更新")
    args = ap.parse_args()

    cookie_path = resolve_x_cookie_file(args.cookie_file)
    if not args.auth_state.exists() and (
        cookie_path is None or not Path(cookie_path).exists()
    ):
        print(
            "エラー: X の認証がありません。\n"
            "  python collector.py --login-x\n"
            "または data/.x_cookies.json を用意してください。\n"
            "（ゲストでは x.com/search はログインへ飛ぶため、ここからは X 直叩きのみ対応します）",
            file=sys.stderr,
        )
        sys.exit(2)

    queries = args.queries if args.queries else DEFAULT_QUERIES
    try:
        raw_handles = _discover_handles(
            queries,
            auth_state_path=args.auth_state,
            cookie_file_path=cookie_path,
            pause_ms=max(500, args.pause_ms),
        )
    except (RuntimeError, FileNotFoundError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        sys.exit(1)

    existing = _seed_and_alias_cf()
    new_rows = _filter_new_handles(raw_handles, existing, scene_only=args.scene_only)
    print(f"raw={len(raw_handles)} new_after_filters={len(new_rows)}")
    for h, hid in new_rows[:100]:
        print(f"{h}\t{hid}")
    if len(new_rows) > 100:
        print(f"... {len(new_rows) - 100} more")

    if args.apply and new_rows:
        _insert_persons(new_rows)
        _append_x_profiles(new_rows)
        print(f"[OK] applied +{len(new_rows)} persons")


if __name__ == "__main__":
    main()
