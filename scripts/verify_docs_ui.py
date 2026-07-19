"""Verify the generated docs UI in Playwright."""

from __future__ import annotations

import argparse
import contextlib
import functools
import http.server
import socket
import sys
import threading
from pathlib import Path
from typing import Iterator

ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "docs"


def _free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


@contextlib.contextmanager
def serve_docs(docs_dir: Path, host: str, port: int) -> Iterator[str]:
    chosen_port = port or _free_port(host)
    handler = functools.partial(QuietHandler, directory=str(docs_dir))
    server = http.server.ThreadingHTTPServer((host, chosen_port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{host}:{chosen_port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


OVERFLOW_SCRIPT = """() => {
  const viewportRight = document.documentElement.clientWidth + 1;
  const isScrollableX = (el) => {
    const style = getComputedStyle(el);
    const ox = style.overflowX;
    return (ox === "auto" || ox === "scroll") && el.scrollWidth > el.clientWidth + 1;
  };
  const insideScrollableX = (el) => {
    let node = el;
    while (node && node !== document.body) {
      if (isScrollableX(node)) return true;
      node = node.parentElement;
    }
    return false;
  };
  const offenders = [];
  for (const el of document.querySelectorAll("body *")) {
    const style = getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden") continue;
    // Tables intentionally scroll inside .table-wrap on mobile; ignore those.
    if (insideScrollableX(el)) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width > 0 && rect.right > viewportRight) {
      offenders.push({
        tag: el.tagName,
        id: el.id,
        cls: String(el.className),
        left: Math.round(rect.left),
        right: Math.round(rect.right),
        text: (el.textContent || "").trim().slice(0, 80),
      });
    }
  }
  return offenders.slice(0, 20);
}"""


def verify_case(
    page: object,
    url: str,
    name: str,
    width: int,
    height: int,
    *,
    is_mobile: bool,
    open_review: bool,
    timeout_ms: int,
    screenshot_dir: Path | None,
) -> list[str]:
    errors: list[str] = []
    page_errors: list[str] = []
    page.context.set_default_timeout(timeout_ms)
    page.on("pageerror", lambda exc: page_errors.append(str(exc)))
    page.set_viewport_size({"width": width, "height": height})
    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    page.wait_for_selector("#thin-review-summary", state="attached", timeout=timeout_ms)
    page.wait_for_timeout(2000)
    if open_review:
        page.evaluate(
            """() => {
              document.querySelectorAll("details.foldout").forEach((details) => {
                details.open = true;
              });
            }"""
        )
        page.locator("#thin-review-summary").scroll_into_view_if_needed()
        page.wait_for_timeout(500)

    summary = (page.locator("#thin-review-summary").text_content(timeout=timeout_ms) or "").strip()
    candidate_status = (
        page.locator("#thin-candidates-table-status").text_content(timeout=timeout_ms) or ""
    ).strip()
    decision_status = (
        page.locator("#thin-candidate-decisions-table-status").text_content(timeout=timeout_ms) or ""
    ).strip()
    overflow = page.evaluate(OVERFLOW_SCRIPT)

    if not summary:
        errors.append(f"{name}: thin summary is empty")
    for token in ("keep", "exclude", "review"):
        if token not in summary:
            errors.append(f"{name}: thin summary is missing {token!r}")
    if not candidate_status:
        errors.append(f"{name}: thin candidate table status is empty")
    if not decision_status:
        errors.append(f"{name}: thin decision table status is empty")
    if overflow:
        errors.append(f"{name}: horizontal overflow: {overflow!r}")
    if page_errors:
        errors.append(f"{name}: page errors: {page_errors!r}")

    if screenshot_dir:
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshot_dir / f"docs-ui-{name}.png"), full_page=True)

    print(
        f"[OK] {name}: summary={summary.replace(chr(10), ' | ')} "
        f"candidates={candidate_status!r} decisions={decision_status!r}"
    )
    return errors


def run(url: str, timeout_ms: int, screenshot_dir: Path | None) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[FAIL] Playwright is not installed. Run: pip install -r requirements-dev.txt")
        return 2

    cases = [
        {
            "name": "desktop",
            "width": 1440,
            "height": 1200,
            "is_mobile": False,
            "open_review": False,
        },
        {
            "name": "mobile",
            "width": 390,
            "height": 1200,
            "is_mobile": True,
            "open_review": False,
        },
        {
            "name": "mobile-review-open",
            "width": 390,
            "height": 1400,
            "is_mobile": True,
            "open_review": True,
        },
    ]
    all_errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for case in cases:
                context = browser.new_context(
                    viewport={"width": case["width"], "height": case["height"]},
                    is_mobile=bool(case["is_mobile"]),
                )
                page = context.new_page()
                try:
                    all_errors.extend(
                        verify_case(
                            page,
                            url,
                            str(case["name"]),
                            int(case["width"]),
                            int(case["height"]),
                            is_mobile=bool(case["is_mobile"]),
                            open_review=bool(case["open_review"]),
                            timeout_ms=timeout_ms,
                            screenshot_dir=screenshot_dir,
                        )
                    )
                finally:
                    context.close()
        finally:
            browser.close()

    if all_errors:
        for error in all_errors:
            print(f"[FAIL] {error}")
        return 1
    print("[OK] docs UI verification passed")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="", help="Existing docs URL. If omitted, serve docs/.")
    parser.add_argument("--docs-dir", type=Path, default=DOCS_DIR, help="Directory to serve.")
    parser.add_argument("--host", default="127.0.0.1", help="Local bind host when serving docs.")
    parser.add_argument("--port", type=int, default=0, help="Local port; 0 picks a free port.")
    parser.add_argument("--timeout-ms", type=int, default=60000, help="Playwright timeout.")
    parser.add_argument(
        "--screenshot-dir",
        type=Path,
        default=None,
        help="Optional directory for verification screenshots.",
    )
    args = parser.parse_args()

    if args.url:
        raise SystemExit(run(args.url, args.timeout_ms, args.screenshot_dir))
    if not args.docs_dir.exists():
        print(f"[FAIL] docs directory not found: {args.docs_dir}")
        raise SystemExit(2)
    with serve_docs(args.docs_dir, args.host, args.port) as url:
        print(f"[OK] serving docs at {url}")
        raise SystemExit(run(url, args.timeout_ms, args.screenshot_dir))


if __name__ == "__main__":
    main()
