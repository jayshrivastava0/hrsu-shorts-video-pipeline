"""Parse the blog HTML's References block and expose those URLs as a source.

The HRSU blog content generator produces this structure (see content_generator.py
`_build_references_html`):

    <div class="hrsu-source-card">
      <h4>References</h4>
      <ol>
        <li>★ <a href="https://www.epa.gov/...">Title</a></li>
        <li><a href="https://example.com/...">Title</a></li>
        ...
      </ol>
    </div>

We extract each reference URL, mark authoritative ones (have a ★ before the link),
strip internal HRSU domains (to avoid blog→blog loops), and expose the result both
as a list and as a `BaseSource` that returns og:image candidates for B-roll.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup

from video_agent.sources.base import BaseSource, RawCandidate

log = logging.getLogger(__name__)


# Domains we MUST NOT include — would create an internal loop or cite ourselves.
_INTERNAL_HOSTS = {
    "hrsuindore.com", "www.hrsuindore.com",
    "blog.hrsuindore.com",
}


@dataclass
class BlogReference:
    url: str
    title: str
    authoritative: bool

    @property
    def hostname(self) -> str:
        try:
            return urlparse(self.url).netloc.lower().removeprefix("www.")
        except Exception:
            return ""


def _is_internal(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
        if host in _INTERNAL_HOSTS:
            return True
        # Catch any *.hrsuindore.com subdomain
        if host.endswith(".hrsuindore.com"):
            return True
    except Exception:
        return False
    return False


def parse_blog_references(blog_html: str) -> list[BlogReference]:
    """Extract references from the blog's hrsu-source-card block.

    Returns references in the same order they appear on the page,
    with internal HRSU links removed.
    """
    if not blog_html:
        return []
    try:
        soup = BeautifulSoup(blog_html, "html.parser")
    except Exception as e:
        log.warning("blog_references: HTML parse failed: %s", e)
        return []

    refs: list[BlogReference] = []
    cards = soup.find_all("div", class_=re.compile(r"hrsu-source-card"))
    for card in cards:
        for li in card.find_all("li"):
            a = li.find("a", href=True)
            if not a:
                continue
            url = a["href"].strip()
            if not url or url.startswith("#") or url.startswith("javascript:"):
                continue
            if _is_internal(url):
                log.debug("blog_references: skipping internal %s", url)
                continue
            # Authoritative marker: the <li> begins with ★ before the <a>.
            li_text = li.get_text("", strip=False)
            authoritative = li_text.lstrip().startswith("★")
            title = a.get_text(" ", strip=True).lstrip("★ ").strip()
            if not title:
                title = urlparse(url).netloc
            refs.append(BlogReference(url=url, title=title,
                                       authoritative=authoritative))

    log.info("blog_references: parsed %d references (%d authoritative [*])",
             len(refs), sum(1 for r in refs if r.authoritative))
    return refs


# ── og:image scraper (for B-roll) ────────────────────────────────────────────

_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; HRSU-VideoBot/2.0)",
    "Accept": "text/html,application/xhtml+xml",
}


def _scrape_og_image(url: str, timeout: float = 8.0) -> tuple[str, str] | None:
    """Fetch the URL and return (og:image_url, og:title) if present."""
    try:
        r = requests.get(url, timeout=timeout, headers=_BROWSER_HEADERS,
                         allow_redirects=True)
        if r.status_code >= 400:
            return None
        # Only parse HTML — skip PDFs and other media
        ctype = r.headers.get("Content-Type", "").lower()
        if "html" not in ctype:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        og_image = soup.find("meta", property="og:image")
        if not og_image or not og_image.get("content"):
            # Twitter card fallback
            og_image = soup.find("meta", attrs={"name": "twitter:image"})
        if not og_image or not og_image.get("content"):
            return None
        img_url = og_image["content"].strip()
        og_title = soup.find("meta", property="og:title")
        title = og_title["content"].strip() if og_title and og_title.get("content") else ""
        if not title:
            t = soup.find("title")
            title = t.get_text(strip=True) if t else url
        return img_url, title
    except Exception as e:
        log.debug("blog_references: og:image scrape failed for %s: %s", url, e)
        return None


_FIGURE_KEYWORDS = frozenset({
    "figure", "fig.", "fig ", "chart", "graph", "diagram",
    "schematic", "flowchart", "process", "table", "illustration",
})


def _scrape_page_figures(url: str, max_figures: int = 3,
                         timeout: float = 10.0) -> list[tuple[str, str]]:
    """Return (img_url, caption) for figure images found on a paper/article page.

    Tries two strategies:
    1. <figure><img> elements — used by most academic publishers (ScienceDirect,
       Frontiers, MDPI, EPA, ResearchGate preview pages).
    2. <img> whose alt/class text contains figure-like keywords — fallback for
       sites that don't wrap in <figure>.

    Only images with figure-like context are returned; icons, logos, and UI
    chrome are filtered out by keyword. Returns [] on any error.
    """
    try:
        r = requests.get(url, timeout=timeout, headers=_BROWSER_HEADERS,
                         allow_redirects=True)
        if r.status_code >= 400:
            return []
        if "html" not in r.headers.get("Content-Type", "").lower():
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        results: list[tuple[str, str]] = []

        # Strategy 1: explicit <figure> wrappers
        for fig in soup.find_all("figure"):
            img = fig.find("img")
            if not img:
                continue
            src = (img.get("src") or img.get("data-src") or
                   img.get("data-lazy-src") or "").strip()
            if not src or src.startswith("data:"):
                continue
            src = urljoin(url, src)
            figcap = fig.find("figcaption")
            caption = (figcap.get_text(" ", strip=True) if figcap
                       else img.get("alt", "")).strip()
            if caption:
                results.append((src, caption))
            if len(results) >= max_figures:
                return results

        # Strategy 2: bare <img> with figure-like alt/class text
        if not results:
            for img in soup.find_all("img"):
                src = (img.get("src") or img.get("data-src") or "").strip()
                if not src or src.startswith("data:"):
                    continue
                alt = img.get("alt", "").lower()
                cls = " ".join(img.get("class", [])).lower()
                context = alt + " " + cls
                if not any(kw in context for kw in _FIGURE_KEYWORDS):
                    continue
                src = urljoin(url, src)
                caption = img.get("alt", "").strip()
                if caption:
                    results.append((src, caption))
                if len(results) >= max_figures:
                    break

        return results
    except Exception as e:
        log.debug("blog_references: figure scrape failed for %s: %s", url, e)
        return []


class BlogReferencesSource(BaseSource):
    """A search source that returns og:images from the blog's reference URLs.

    Unlike Pexels/Unsplash/etc. this source ignores the query entirely —
    it returns the same set of high-authority candidates for every scene,
    derived from the blog's own References block. Scoring will then filter
    them per-scene by context match against the narration.

    Caching: the og:image URLs are resolved once per Storyboard build and
    cached by the QueryCache like any other source.
    """
    name = "blog_references"
    authority_weight = 12     # higher than Wikimedia (10) — these are the
                              # very sources the blog itself cited.

    def __init__(self, references: list[BlogReference]):
        # Pre-resolve images at construction; failures are silently dropped.
        # Two passes per reference: (1) og:image for social preview,
        # (2) inline <figure> images for actual paper diagrams/charts.
        self._candidates: list[RawCandidate] = []
        og_count = 0
        fig_count = 0

        for ref in references:
            extra_base = {"reference_url": ref.url, "hostname": ref.hostname,
                          "authoritative": ref.authoritative,
                          "reference_title": ref.title}

            # Pass 1: og:image (social preview — usually the paper's hero image)
            scraped = _scrape_og_image(ref.url)
            if scraped:
                img_url, og_title = scraped
                caption_bits = [ref.title]
                if og_title and og_title != ref.title:
                    caption_bits.append(og_title)
                self._candidates.append(RawCandidate(
                    source=self.name,
                    url=img_url,
                    caption=" — ".join(caption_bits),
                    width=0, height=0,
                    is_clip=False,
                    extra={**extra_base, "image_type": "og_image"},
                ))
                og_count += 1

            # Pass 2: inline <figure> images — actual diagrams, charts, tables
            # from the paper. Higher information value than og:image for B2B.
            for fig_url, fig_caption in _scrape_page_figures(ref.url):
                caption = f"{ref.title}: {fig_caption}" if fig_caption else ref.title
                self._candidates.append(RawCandidate(
                    source=self.name,
                    url=fig_url,
                    caption=caption,
                    width=0, height=0,
                    is_clip=False,
                    extra={**extra_base, "image_type": "paper_figure"},
                ))
                fig_count += 1

        log.info("BlogReferencesSource: %d og:image + %d paper figure candidates "
                 "from %d references", og_count, fig_count, len(references))

    def search(self, query: str, limit: int = 5) -> list[RawCandidate]:
        # Return all cached candidates; the sourcer's context-match gate
        # will filter them down per scene. Authoritative refs first.
        ordered = sorted(self._candidates,
                         key=lambda c: 0 if c.extra.get("authoritative") else 1)
        return ordered[:max(limit, 3)]
