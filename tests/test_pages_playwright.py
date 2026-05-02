"""GitHub Pages smoke checks via Playwright (requires Playwright + Chromium).

By default the repo root is served over loopback HTTP so ``docs/`` matches the
GitHub Pages trailing-slash issue (``…/docs`` vs ``…/docs/``).

Set PLAYWRIGHT_LIVE_PAGES=1 to hit the real site (PICKUP_ARTIST_PAGES_URL or the
default github.io URL).
"""

from __future__ import annotations

import contextlib
import os
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LIVE_BASE = "https://jim-auto.github.io/pickup-artist-network"


def _live_pages_urls() -> tuple[str, ...]:
    base = os.environ.get("PICKUP_ARTIST_PAGES_URL", DEFAULT_LIVE_BASE).rstrip("/")
    return (f"{base}/", f"{base}")


@contextlib.contextmanager
def _serve_repo_root(root: Path):
    """Serve ``root`` at http://127.0.0.1:<port>/ (threaded, ephemeral port)."""

    class _Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root), **kwargs)

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@unittest.skipIf(sync_playwright is None, "playwright not installed (pip install -r requirements-dev.txt)")
@unittest.skipIf(os.environ.get("SKIP_PLAYWRIGHT", "").strip().lower() in {"1", "true", "yes"}, "SKIP_PLAYWRIGHT set")
class PickupArtistPagesPlaywrightTests(unittest.TestCase):
    def test_graph_data_loads_with_and_without_trailing_slash(self) -> None:
        """graph-data.json resolves under the site path when the URL omits a trailing slash."""

        live = os.environ.get("PLAYWRIGHT_LIVE_PAGES", "").strip().lower() in {"1", "true", "yes"}
        if live:
            urls = _live_pages_urls()
            for url in urls:
                with self.subTest(url=url):
                    self._check_single_url(url)
            return

        with _serve_repo_root(REPO_ROOT) as origin:
            for url in (f"{origin}docs/", f"{origin}docs"):
                with self.subTest(url=url):
                    self._check_single_url(url)

    def _check_single_url(self, url: str) -> None:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_context().new_page()
                errs: list[str] = []
                page.on("pageerror", lambda exc: errs.append(str(exc)))

                page.goto(url, wait_until="domcontentloaded", timeout=120_000)

                page.locator("#total-nodes").wait_for(state="attached", timeout=60_000)
                page.wait_for_function(
                    """() => {
                      const el = document.getElementById('total-nodes');
                      if (!el) return false;
                      const n = parseInt(el.textContent || '0', 10);
                      return Number.isFinite(n) && n > 50;
                    }""",
                    timeout=90_000,
                )

                self.assertGreater(page.locator("#network canvas").count(), 0)
                total = int(page.locator("#total-nodes").text_content() or "0")
                self.assertGreater(total, 50)
                visible = int(page.locator("#visible-nodes").text_content() or "0")
                self.assertGreater(visible, 0)
                self.assertFalse(errs, msg=str(errs))
                page.context.close()
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
