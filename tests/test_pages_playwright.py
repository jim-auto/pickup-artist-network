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

    def test_graph_zoom_pan_and_click(self) -> None:
        """Wheel/buttons zoom, drag pans, and a visible node click fills the detail panel."""

        live = os.environ.get("PLAYWRIGHT_LIVE_PAGES", "").strip().lower() in {"1", "true", "yes"}
        if live:
            url = _live_pages_urls()[0]
            self._check_graph_interactions(url)
            return

        with _serve_repo_root(REPO_ROOT) as origin:
            self._check_graph_interactions(f"{origin}docs/")

    def _wait_for_graph(self, page) -> None:
        page.locator("#network canvas").wait_for(state="visible", timeout=90_000)
        page.wait_for_function(
            """() => {
              const el = document.getElementById('visible-nodes');
              if (!el) return false;
              const n = parseInt(el.textContent || '0', 10);
              return Number.isFinite(n) && n > 10 && window.graphNetwork && window.graphNetwork.getScale() > 0;
            }""",
            timeout=90_000,
        )
        page.locator("#network").scroll_into_view_if_needed()
        page.wait_for_function(
            """() => {
              const canvas = document.querySelector('#network canvas');
              if (!canvas) return false;
              const rect = canvas.getBoundingClientRect();
              return rect.width > 200 && rect.top >= 0 && rect.bottom <= (window.innerHeight + rect.height / 2);
            }""",
            timeout=15_000,
        )
        page.wait_for_timeout(500)

    def _graph_scale(self, page) -> float:
        return float(page.evaluate("() => window.graphNetwork.getScale()"))

    def _check_graph_interactions(self, url: str) -> None:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()
                errs: list[str] = []
                page.on("pageerror", lambda exc: errs.append(str(exc)))
                page.goto(url, wait_until="domcontentloaded", timeout=120_000)
                self._wait_for_graph(page)

                start_scale = self._graph_scale(page)
                self.assertGreater(start_scale, 0)

                page.locator("#zoom-in").click()
                page.wait_for_function(
                    """(prev) => window.graphNetwork.getScale() > prev + 0.01""",
                    arg=start_scale,
                    timeout=5_000,
                )
                zoomed_in = self._graph_scale(page)
                self.assertGreater(zoomed_in, start_scale)

                page.locator("#zoom-out").click()
                page.wait_for_function(
                    """(prev) => window.graphNetwork.getScale() < prev - 0.01""",
                    arg=zoomed_in,
                    timeout=5_000,
                )
                after_button = self._graph_scale(page)

                page.locator("#network canvas").hover()
                wheel_before = self._graph_scale(page)
                page.mouse.wheel(0, -800)
                page.wait_for_timeout(200)
                wheel_after_mouse = self._graph_scale(page)
                if wheel_after_mouse <= wheel_before + 0.01:
                    dispatched = page.evaluate(
                        """() => {
                          const canvas = document.querySelector('#network canvas');
                          const before = window.graphNetwork.getScale();
                          const rect = canvas.getBoundingClientRect();
                          canvas.dispatchEvent(new WheelEvent('wheel', {
                            bubbles: true,
                            cancelable: true,
                            clientX: rect.left + rect.width / 2,
                            clientY: rect.top + rect.height / 2,
                            deltaY: -480,
                            deltaMode: 0
                          }));
                          return { before, after: window.graphNetwork.getScale() };
                        }"""
                    )
                    self.assertGreater(
                        dispatched["after"],
                        dispatched["before"] + 0.01,
                        msg=f"wheel did not zoom: mouse={wheel_before}->{wheel_after_mouse} dispatch={dispatched}",
                    )
                else:
                    self.assertGreater(wheel_after_mouse, wheel_before)

                box = page.locator("#network canvas").bounding_box()
                self.assertIsNotNone(box)
                pan_before = page.evaluate("() => window.graphNetwork.getViewPosition()")
                start_x = box["x"] + 36
                start_y = box["y"] + 36
                hit = page.evaluate(
                    """({x, y}) => {
                      const el = document.elementFromPoint(x, y);
                      return el ? { tag: el.tagName, id: el.id, cls: String(el.className) } : null;
                    }""",
                    {"x": start_x, "y": start_y},
                )
                self.assertEqual((hit or {}).get("tag"), "CANVAS", msg=f"drag start missed canvas: {hit}")
                page.mouse.move(start_x, start_y)
                page.mouse.down()
                page.mouse.move(start_x + 160, start_y + 70, steps=20)
                page.mouse.up()
                page.wait_for_timeout(250)
                pan_after = page.evaluate("() => window.graphNetwork.getViewPosition()")
                moved = abs(pan_after["x"] - pan_before["x"]) + abs(pan_after["y"] - pan_before["y"])
                self.assertGreater(moved, 1, msg=f"drag did not pan: {pan_before} -> {pan_after} hit={hit}")

                page.wait_for_timeout(300)
                canvas_box = page.locator("#network canvas").bounding_box()
                self.assertIsNotNone(canvas_box)
                target = page.evaluate(
                    """() => {
                      const canvas = document.querySelector('#network canvas');
                      const w = canvas.clientWidth;
                      const h = canvas.clientHeight;
                      for (let y = 48; y < h - 48; y += 20) {
                        for (let x = 48; x < w - 48; x += 20) {
                          const id = window.graphNetwork.getNodeAt({ x, y });
                          if (id) return { id, x, y };
                        }
                      }
                      return null;
                    }"""
                )
                self.assertIsNotNone(target, "no visible node to click")
                page.mouse.click(canvas_box["x"] + target["x"], canvas_box["y"] + target["y"])
                page.wait_for_function(
                    """(nodeId) => {
                      const panel = document.getElementById('detail-panel');
                      const card = panel && panel.querySelector('.detail-card');
                      const selected = window.graphNetwork.getSelectedNodes();
                      return Boolean(card) || (selected && selected[0] === nodeId);
                    }""",
                    arg=target["id"],
                    timeout=5_000,
                )

                self.assertGreater(self._graph_scale(page), 0)
                self.assertGreater(after_button, 0)
                self.assertFalse(errs, msg=str(errs))
                page.context.close()
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
