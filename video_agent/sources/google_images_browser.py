"""Google Images via Playwright + tf-playwright-stealth, pinned to a single
worker thread, with a persistent browser profile and a single long-lived
page that we navigate across queries (no tab churn).

Why each piece exists:
  * sync Playwright API cannot cross threads → we own a single-worker
    ThreadPoolExecutor; every Playwright call goes through it.
  * tf-playwright-stealth patches ~30 fingerprint vectors. Google's
    detection gets past our hand-rolled init_script easily; the library
    is the maintained baseline.
  * launch_persistent_context with a user_data_dir means cookies + the
    CAPTCHA-solved NID token survive between runs. After you solve a
    CAPTCHA once, the profile looks like a returning real user.
  * One page, reused across .search() calls — no new_page/close churn.
    Same tab stays open, only the URL bar changes.
  * _human_browse adds bezier mouse drift + jittered wheel scrolls +
    a reading-pause idle, defeating the behavioural-telemetry side of
    Google's bot detection that fingerprinting alone won't address.
"""
from __future__ import annotations
import atexit
import logging
import os
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote_plus

try:
    from playwright.sync_api import sync_playwright
except Exception:
    sync_playwright = None  # type: ignore

try:
    from playwright_stealth import stealth_sync
except ImportError:
    try:
        from tf_playwright_stealth import stealth_sync  # type: ignore[no-redef]
    except ImportError:
        stealth_sync = None  # type: ignore[assignment]

from video_agent.sources.base import BaseSource, RawCandidate

log = logging.getLogger(__name__)

_INTERACTIVE = os.getenv("GOOGLE_IMAGES_INTERACTIVE", "0") == "1"
_PROFILE_DIR = Path("output/_browser_profile/google_images")

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

_EXTRACT_JS = """
() => {
    function getImgUrl(href) {
        try {
            const u = new URL(href, window.location.href);
            const direct = u.searchParams.get('imgurl');
            if (direct) return direct;
            const q = u.searchParams.get('url') || u.searchParams.get('q');
            if (q && q.startsWith('http')) return q;
        } catch (e) {}
        return '';
    }
    const seen = new Set();
    const out = [];
    for (const img of document.querySelectorAll('img')) {
        const src = img.src || img.getAttribute('data-src') || '';
        if (!src.startsWith('http')) continue;
        if (seen.has(src)) continue;
        seen.add(src);
        let original = '';
        let node = img;
        for (let i = 0; i < 5 && node; i++) {
            if (node.tagName === 'A' && node.href) {
                const x = getImgUrl(node.href);
                if (x) { original = x; break; }
                if (node.href.startsWith('http') &&
                    !node.href.includes('google.com')) {
                    original = node.href; break;
                }
            }
            node = node.parentElement;
        }
        out.push({
            src: src, original: original, alt: img.alt || '',
            w: img.naturalWidth || img.width || 0,
            h: img.naturalHeight || img.height || 0,
        });
    }
    return out;
}
"""

_SKIP_HOSTS = ("encrypted-tbn", "gstatic.com")
_MIN_WIDTH = 300


def _bezier_point(p0, p1, p2, p3, t):
    u = 1 - t
    return (
        u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
        u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1],
    )


def _human_browse(page) -> None:
    """Mouse-drift + jittered wheel scrolls + reading pause."""
    try:
        w, h = 1366, 900
        p0 = (random.randint(50, 200), random.randint(50, 200))
        p3 = (random.randint(w - 400, w - 50), random.randint(h - 400, h - 50))
        p1 = (random.randint(100, w - 100), random.randint(100, h - 100))
        p2 = (random.randint(100, w - 100), random.randint(100, h - 100))
        steps = random.randint(18, 28)
        for i in range(steps + 1):
            x, y = _bezier_point(p0, p1, p2, p3, i / steps)
            page.mouse.move(x, y, steps=1)
        for _ in range(random.randint(1, 2)):
            page.mouse.wheel(0, random.randint(300, 900))
            page.wait_for_timeout(random.randint(150, 400))
        page.wait_for_timeout(random.randint(600, 1200))
    except Exception as e:
        log.debug("_human_browse skipped (%s)", e)


class GoogleImagesBrowserSource(BaseSource):
    name = "google_images"
    authority_weight = 5

    def __init__(self):
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="gimg-pw")
        self._pw = None
        self._ctx = None
        self._page = None
        self._init_failed = False
        try:
            self._executor.submit(self._init_browser).result(timeout=20)
        except Exception as e:
            log.warning("GoogleImages: init dispatch failed (%s)", e)
            self._init_failed = True
        atexit.register(self._shutdown)

    def _init_browser(self):
        if sync_playwright is None:
            log.warning("GoogleImages: playwright not available; this source returns []")
            self._init_failed = True
            return
        try:
            self._pw = sync_playwright().start()
            _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            self._ctx = self._pw.chromium.launch_persistent_context(
                user_data_dir=str(_PROFILE_DIR.resolve()),
                headless=not _INTERACTIVE,
                user_agent=_UA,
                viewport={"width": 1366, "height": 900},
                locale="en-US",
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            try:
                stealth_sync(self._ctx)
            except Exception as e:
                log.warning("stealth_sync failed (%s); continuing without stealth", e)
            if self._ctx.pages:
                self._page = self._ctx.pages[0]
            else:
                self._page = self._ctx.new_page()
            if _INTERACTIVE:
                log.info("GoogleImages: INTERACTIVE — visible browser")
            log.info("GoogleImages: persistent profile loaded at %s", _PROFILE_DIR)
        except Exception as e:
            log.warning("GoogleImages: Playwright init failed (%s); "
                        "this source returns []", e)
            self._init_failed = True

    def _do_search(self, query: str, limit: int) -> list[RawCandidate]:
        if self._init_failed or self._page is None:
            return []
        try:
            self._page.goto(
                f"https://www.google.com/search?q={quote_plus(query)}"
                f"&tbm=isch&hl=en&safe=active",
                timeout=15000, wait_until="domcontentloaded",
            )
            if "/sorry/" in self._page.url or "captcha" in self._page.url.lower():
                if _INTERACTIVE:
                    print("\n" + "=" * 70, file=sys.stderr, flush=True)
                    print(f">>> Google CAPTCHA hit for query: {query!r}",
                          file=sys.stderr, flush=True)
                    print(">>> Solve it in the Chromium window then press ENTER.",
                          file=sys.stderr, flush=True)
                    print("=" * 70, file=sys.stderr, flush=True)
                    # input() raises EOFError from non-main threads on Windows;
                    # use msvcrt which works from any thread on Windows.
                    if sys.platform == "win32":
                        import msvcrt, time as _time
                        print(">>> (press ENTER in this terminal window)",
                              file=sys.stderr, flush=True)
                        while True:
                            if msvcrt.kbhit():
                                ch = msvcrt.getwch()
                                if ch in ('\r', '\n'):
                                    break
                            _time.sleep(0.05)
                    else:
                        try:
                            input()
                        except EOFError:
                            pass
                    if "/sorry/" in self._page.url:
                        try:
                            self._page.goto(
                                f"https://www.google.com/search?q="
                                f"{quote_plus(query)}&tbm=isch&hl=en",
                                timeout=20000, wait_until="domcontentloaded",
                            )
                            self._page.wait_for_timeout(1500)
                        except Exception as e:
                            log.warning("post-CAPTCHA reload failed: %s", e)
                            return []
                    if "/sorry/" in self._page.url:
                        log.warning("GoogleImages: still on CAPTCHA — giving up on %r", query)
                        return []
                    log.info("GoogleImages: CAPTCHA cleared, continuing")
                else:
                    log.warning(
                        "GoogleImages: CAPTCHA for %r — set "
                        "GOOGLE_IMAGES_INTERACTIVE=1 to solve manually.", query,
                    )
                    return []
            self._page.wait_for_timeout(1200)
            try:
                self._page.wait_for_selector("img", timeout=4000)
            except Exception:
                pass
            _human_browse(self._page)
            data = self._page.evaluate(_EXTRACT_JS)
        except Exception as e:
            log.warning("GoogleImages: page interaction failed for %r: %s", query, e)
            data = []

        originals: list[RawCandidate] = []
        thumbs: list[RawCandidate] = []
        seen: set[str] = set()
        for d in data:
            src = d.get("src", "")
            original = d.get("original", "") or ""
            w = int(d.get("w") or 0)
            h = int(d.get("h") or 0)
            alt = d.get("alt", "") or ""
            url = original if original else src
            if not url or url in seen:
                continue
            # Filter out thumbnail-sized images (data-URI placeholders, etc.)
            if w > 0 and w < _MIN_WIDTH:
                continue
            seen.add(url)
            is_thumb = any(s in url for s in _SKIP_HOSTS)
            cand = RawCandidate(
                source=self.name, url=url, caption=alt,
                width=w, height=h,
            )
            (thumbs if is_thumb else originals).append(cand)

        results = (originals or thumbs)[:limit]
        if results:
            log.info("GoogleImages: %d candidates for %r", len(results), query)
        else:
            log.warning("GoogleImages: 0 candidates for %r", query)
        return results

    def search(self, query: str, limit: int = 5) -> list[RawCandidate]:
        if self._init_failed:
            return []
        try:
            timeout = None if _INTERACTIVE else 25
            return self._executor.submit(
                self._do_search, query, limit
            ).result(timeout=timeout)
        except Exception as e:
            log.warning("GoogleImages: search dispatch failed for %r: %s", query, e)
            return []

    def _shutdown(self):
        def _close():
            try:
                if self._page:
                    try:
                        self._page.close()
                    except Exception:
                        pass
                if self._ctx:
                    self._ctx.close()
                if self._pw:
                    self._pw.stop()
            except Exception:
                pass
        try:
            self._executor.submit(_close).result(timeout=5)
        except Exception:
            pass
        try:
            self._executor.shutdown(wait=False)
        except Exception:
            pass
