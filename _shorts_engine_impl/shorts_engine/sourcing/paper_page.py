"""PAPER_CARD acquisition (spec §4 Stage 4): the cited paper's page 1.
(a) open-access PDF → pypdfium2 page-1 render; (b) else Playwright header
screenshot of the landing page; (c) both fail → None and the shot's declared
QUOTE_CARD fallback renders. Results cached by URL hash — papers don't
change, and the user wants this shot on every proof beat."""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from urllib.parse import urlparse

import requests

from shorts_engine import config

logger = logging.getLogger(__name__)

_UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 hrsu-shorts-engine")}

_screenshot = None  # test seam → screenshot_header
_screenshot_full = None  # test seam → screenshot_full_page
_fetch_bytes = None  # test seam → _default_fetch_bytes


def cache_key(url: str) -> str:
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()[:16]


def is_pdf_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.lower()
    host = parsed.netloc.lower()
    if path.endswith(".pdf"):
        return True
    if "/pdf/" in path or path.endswith("/pdf"):
        return host.endswith(("arxiv.org", "mdpi.com", "ncbi.nlm.nih.gov"))
    return False


def _default_fetch_bytes(url: str) -> bytes | None:
    try:
        r = requests.get(url, timeout=20, headers=_UA)
        return r.content if r.status_code == 200 else None
    except Exception as e:  # noqa: BLE001
        logger.info("paper fetch failed %s: %s", url, e)
        return None


def render_pdf_page1(pdf_bytes: bytes, out_png: Path) -> Path | None:
    try:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(pdf_bytes)
        page = pdf[0]
        scale = 1600 / page.get_size()[0]
        bitmap = page.render(scale=scale)
        img = bitmap.to_pil()
        out_png = Path(out_png)
        out_png.parent.mkdir(parents=True, exist_ok=True)
        img.convert("RGB").save(out_png)
        return out_png
    except Exception as e:  # noqa: BLE001 — corrupt/protected PDF: fall through
        logger.info("pdf page-1 render failed: %s", e)
        return None


def render_pdf_pages(pdf_bytes: bytes, out_dir: Path, cache_stem: str,
                     max_pages: int) -> list[Path]:
    """Render up to `max_pages` pages (vs. render_pdf_page1's page 1 only) — used when
    hunting for a chart/table/figure that could be anywhere in the cited paper, not just
    the title page."""
    try:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(pdf_bytes)
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for i in range(min(max_pages, len(pdf))):
            page = pdf[i]
            scale = 1600 / page.get_size()[0]
            bitmap = page.render(scale=scale)
            img = bitmap.to_pil()
            out_png = out_dir / f"{cache_stem}_p{i}.png"
            img.convert("RGB").save(out_png)
            paths.append(out_png)
        return paths
    except Exception as e:  # noqa: BLE001 — corrupt/protected PDF: no candidates
        logger.info("pdf multi-page render failed: %s", e)
        return []


def screenshot_full_page(url: str, out_png: Path) -> Path | None:
    """Playwright full-page screenshot (vs. screenshot_header's fixed 1200x900 header
    crop) — a chart/figure worth reusing could be anywhere on the article page."""
    try:
        from playwright.sync_api import sync_playwright
        out_png = Path(out_png)
        out_png.parent.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1200, "height": 1500})
            page.goto(url, timeout=30_000, wait_until="domcontentloaded")
            try:  # best-effort cookie banner dismissal
                btn = page.locator(
                    "button:visible",
                    has_text=__import__("re").compile(
                        r"accept|agree|got it|^ok$", __import__("re").I))
                if btn.count():
                    btn.first.click(timeout=3_000)
            except Exception:  # noqa: BLE001
                pass
            page.screenshot(path=str(out_png), full_page=True)
            browser.close()
        return out_png if out_png.exists() else None
    except Exception as e:  # noqa: BLE001 — any failure ⇒ caller gets no candidates
        logger.info("full-page screenshot failed %s: %s", url, e)
        return None


def fetch_figure_candidates(url: str, workspace: Path | None = None,
                            max_pages: int | None = None,
                            torture: bool = False) -> list[Path]:
    """Candidate images that MIGHT contain a real chart/table/diagram from a source this
    run already cites — for `request_source_figure`, never an open-web search. Judging
    which candidate actually matches the fact happens one layer up
    (`sourcing/figure_acquisition.py`), same division of labor as `ladder.py`'s tiers.
    `workspace` is accepted for call-site symmetry with the rest of the acquisition
    surface but unused — candidates cache under `config.PAPER_CACHE_DIR` like
    `fetch_front_page`, independent of any one run's workspace."""
    from shorts_engine import config as _config
    max_pages = max_pages or _config.FIGURE_MAX_PDF_PAGES
    _config.PAPER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    stem = cache_key(url) + "_fig"
    cached = sorted(_config.PAPER_CACHE_DIR.glob(f"{stem}_p*.png"))
    if cached:
        return cached
    if torture:
        return []
    fetch = _fetch_bytes or _default_fetch_bytes
    if is_pdf_url(url):
        data = fetch(url)
        if not data:
            return []
        return render_pdf_pages(data, _config.PAPER_CACHE_DIR, stem, max_pages)
    shot = _screenshot_full or screenshot_full_page
    out_png = _config.PAPER_CACHE_DIR / f"{stem}_p0.png"
    result = shot(url, out_png)
    return [result] if result else []


def screenshot_header(url: str, out_png: Path) -> Path | None:
    """Playwright header screenshot: title/authors/journal visible."""
    try:
        from playwright.sync_api import sync_playwright
        out_png = Path(out_png)
        out_png.parent.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1200, "height": 1500})
            page.goto(url, timeout=30_000, wait_until="domcontentloaded")
            try:  # best-effort cookie banner dismissal
                btn = page.locator(
                    "button:visible",
                    has_text=__import__("re").compile(
                        r"accept|agree|got it|^ok$", __import__("re").I))
                if btn.count():
                    btn.first.click(timeout=3_000)
            except Exception:  # noqa: BLE001
                pass
            page.screenshot(path=str(out_png),
                            clip={"x": 0, "y": 0, "width": 1200, "height": 900})
            browser.close()
        return out_png if out_png.exists() else None
    except Exception as e:  # noqa: BLE001 — any failure ⇒ fallback card renders
        logger.info("landing screenshot failed %s: %s", url, e)
        return None


def fetch_front_page(url: str, torture: bool = False) -> Path | None:
    config.PAPER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = config.PAPER_CACHE_DIR / (cache_key(url) + ".png")
    if cached.exists():
        return cached
    if torture:
        return None
    fetch = _fetch_bytes or _default_fetch_bytes
    shot = _screenshot or screenshot_header
    if is_pdf_url(url):
        data = fetch(url)
        if data and render_pdf_page1(data, cached):
            return cached
        return None
    return shot(url, cached)
