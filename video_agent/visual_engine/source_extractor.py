"""Per-blog image and citation-PDF source extractor.

For each blog, parses content_html for inline <img> tags and authority
citation links (PDFs, doi.org, arxiv, .gov, .edu, etc.), downloads them,
renders PDF first pages to PNG via PyMuPDF, and returns a manifest of
source images suitable for use in video scenes.

Results are cached under cache_dir/<blog_id>/ so subsequent pipeline runs
skip all HTTP requests.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.parse
from pathlib import Path

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

# Minimum score for find_source_for_scene to return a match.
MIN_SOURCE_SCORE = 1

# Filenames containing these strings are decorative — skip them.
_SKIP_NAME_PATTERNS = re.compile(
    r"logo|icon|favicon|sprite|avatar|badge|button|banner|arrow|divider|"
    r"separator|pixel|spacer|placeholder|social|share|print",
    re.IGNORECASE,
)

# Authority domains / patterns — PDFs from these get rendered.
_AUTHORITY_RE = re.compile(
    r"\.pdf$|doi\.org|arxiv\.org|pubmed\.ncbi|\.gov/|\.edu/|"
    r"sciencedirect\.com|nature\.com|springer\.com|wiley\.com|"
    r"tandfonline\.com|acs\.org|rsc\.org",
    re.IGNORECASE,
)

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]+")
_STOPWORDS = {
    "the", "a", "an", "of", "and", "or", "to", "for", "in", "on", "at",
    "with", "is", "are", "was", "were", "this", "that", "it", "we", "our",
    "you", "they", "from", "by", "be", "has", "have",
}


def _tokenize(text: str) -> set[str]:
    if not text:
        return set()
    return {m.group(0).lower() for m in _TOKEN_RE.finditer(text)
            if m.group(0).lower() not in _STOPWORDS and len(m.group(0)) > 2}


# ─── HTML parsers ────────────────────────────────────────────────────────────

def _parse_html_images(html: str, base_url: str) -> list[tuple[str, str, str]]:
    """Return list of (absolute_src_url, alt_text, surrounding_text).
    Skips SVGs, icons, logos, and other decorative filenames."""
    soup = BeautifulSoup(html or "", "html.parser")
    results = []
    for img in soup.find_all("img"):
        src = img.get("src", "").strip()
        if not src:
            continue
        # Skip SVGs
        if src.lower().endswith(".svg") or "image/svg" in src.lower():
            continue
        # Skip by filename heuristics
        filename = src.split("?")[0].split("/")[-1]
        if _SKIP_NAME_PATTERNS.search(filename):
            continue
        alt = img.get("alt", "") or ""
        if _SKIP_NAME_PATTERNS.search(alt):
            continue
        # Resolve relative URL
        try:
            absolute = urllib.parse.urljoin(base_url, src)
        except Exception:
            continue
        # Grab surrounding text (parent paragraph)
        parent = img.parent
        ctx = parent.get_text(" ", strip=True)[:200] if parent else ""
        results.append((absolute, alt, ctx))
    return results


def _extract_authority_links(html: str) -> list[tuple[str, str]]:
    """Return list of (href, link_text) for authority/citation links."""
    soup = BeautifulSoup(html or "", "html.parser")
    results = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if _AUTHORITY_RE.search(href):
            results.append((href, a.get_text(strip=True)))
    return results


# ─── Download / render helpers ───────────────────────────────────────────────

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": ("text/html,application/xhtml+xml,application/xml,"
               "application/pdf,image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}


def _download(url: str, dest: Path, timeout: int = 30) -> bool:
    """Download url to dest. Returns True on success.

    Uses a real-browser User-Agent and full Accept headers because authority
    sources (EPA, NIH, journals) routinely 403 bot UAs at the CDN edge.
    Failures are logged at WARNING — not DEBUG — so missing research-paper
    images surface in the normal pipeline output instead of hiding silently.
    """
    try:
        r = requests.get(url, timeout=timeout, stream=True,
                         headers=_BROWSER_HEADERS, allow_redirects=True)
        r.raise_for_status()
        dest.write_bytes(r.content)
        return True
    except Exception as e:
        log.warning("Authority-source download failed for %s: %s", url, e)
        return False


def _render_pdf_first_pages(pdf_path: Path, out_dir: Path,
                             max_pages: int = 2) -> list[Path]:
    """Render first max_pages of a PDF to PNG files via PyMuPDF."""
    try:
        import fitz
    except ImportError:
        log.warning("PyMuPDF not installed; skipping PDF render")
        return []
    try:
        doc = fitz.open(str(pdf_path))
    except Exception as e:
        log.warning("Could not open PDF %s: %s", pdf_path, e)
        return []
    pages = []
    for i in range(min(max_pages, len(doc))):
        page = doc[i]
        pix = page.get_pixmap(dpi=150)
        out = out_dir / f"{pdf_path.stem}_page{i}.png"
        pix.save(str(out))
        pages.append(out)
    doc.close()
    return pages


def _should_skip_image(path: Path) -> bool:
    """True if the image is too small or has an extreme aspect ratio."""
    try:
        from PIL import Image
        with Image.open(path) as img:
            w, h = img.size
        if w < 300 or h < 300:
            return True
        ratio = max(w, h) / max(1, min(w, h))
        if ratio > 5:          # extremely wide/tall banners
            return True
        if path.stat().st_size < 5_000:
            return True
        return False
    except Exception:
        return True            # unreadable → skip


# ─── Public API ──────────────────────────────────────────────────────────────

def extract_blog_sources(blog_record: dict, cache_dir: Path) -> list[dict]:
    """Mine inline images and authority PDF pages from a blog record.

    Each returned entry:
        {
          "id":          str,
          "path":        Path,
          "source_type": "inline_image" | "pdf_page",
          "source_url":  str,
          "caption":     str,
          "tokens":      set[str],
          "is_authority": bool,
        }

    Results cached at cache_dir/<blog_id>/index.json.
    Re-uses cache on subsequent runs.
    """
    cache_dir = Path(cache_dir)
    blog_id = str(blog_record.get("blog_id") or "unknown")
    blog_cache = cache_dir / blog_id
    index_path = blog_cache / "index.json"

    # Load from cache if it exists
    if index_path.exists():
        try:
            raw = json.loads(index_path.read_text(encoding="utf-8"))
            # Re-hydrate paths and token sets
            for entry in raw:
                entry["path"] = Path(entry["path"])
                entry["tokens"] = set(entry.get("tokens", []))
            return raw
        except Exception as e:
            log.warning("Corrupt source cache for %s (%s); rebuilding", blog_id, e)

    blog_cache.mkdir(parents=True, exist_ok=True)
    base_url = blog_record.get("url", "")
    html = blog_record.get("content_html", "")
    sources: list[dict] = []

    # ── Inline images ────────────────────────────────────────────────────────
    for i, (src_url, alt, ctx) in enumerate(_parse_html_images(html, base_url)):
        ext = Path(src_url.split("?")[0]).suffix.lower() or ".jpg"
        dest = blog_cache / f"img_{i:02d}{ext}"
        if not dest.exists():
            if not _download(src_url, dest):
                continue
        if _should_skip_image(dest):
            dest.unlink(missing_ok=True)
            continue
        caption = alt or ctx[:80]
        tokens = _tokenize(alt + " " + ctx)
        is_auth = bool(_AUTHORITY_RE.search(src_url))
        sources.append({
            "id": f"{blog_id}_img{i:02d}",
            "path": dest,
            "source_type": "inline_image",
            "source_url": src_url,
            "caption": caption,
            "tokens": tokens,
            "is_authority": is_auth,
        })

    # ── Authority PDFs ───────────────────────────────────────────────────────
    for j, (href, link_text) in enumerate(_extract_authority_links(html)):
        if not href.lower().endswith(".pdf"):
            # Skip non-PDF authority links (doi, arxiv, etc.) — no renderable asset
            continue
        pdf_dest = blog_cache / f"ref_{j:02d}.pdf"
        if not pdf_dest.exists():
            if not _download(href, pdf_dest):
                continue
        pages = _render_pdf_first_pages(pdf_dest, blog_cache)
        for k, page_path in enumerate(pages):
            if _should_skip_image(page_path):
                continue
            caption = link_text or f"Reference {j + 1}"
            tokens = _tokenize(caption)
            is_auth = bool(_AUTHORITY_RE.search(href))
            sources.append({
                "id": f"{blog_id}_ref{j:02d}_p{k}",
                "path": page_path,
                "source_type": "pdf_page",
                "source_url": href,
                "caption": caption,
                "tokens": tokens,
                "is_authority": is_auth,
            })

    # If the blog had authority links but every download failed, surface this
    # loudly — otherwise it manifests as "the video has no research-paper
    # image" much later in the pipeline with no obvious cause.
    auth_links = _extract_authority_links(html)
    pdf_pages_found = sum(1 for s in sources if s["source_type"] == "pdf_page")
    if auth_links and pdf_pages_found == 0:
        log.warning(
            "Blog %s had %d authority citation link(s) but no PDF pages were "
            "rendered. The video will not show a research-paper image. "
            "Most likely the source CDN blocks programmatic downloads (e.g. "
            "EPA / journal sites) — check earlier WARNING lines for the "
            "specific URL(s) that 403'd.",
            blog_id, len(auth_links),
        )

    # ── Persist cache ────────────────────────────────────────────────────────
    serialisable = []
    for s in sources:
        entry = dict(s)
        entry["path"] = str(entry["path"])
        entry["tokens"] = list(entry["tokens"])
        serialisable.append(entry)
    index_path.write_text(json.dumps(serialisable, indent=2, ensure_ascii=False),
                          encoding="utf-8")

    return sources


def find_source_for_scene(scene: dict, sources: list[dict]) -> dict | None:
    """Return the highest-scoring source for a scene, or None if below MIN_SOURCE_SCORE."""
    if not sources:
        return None

    narration = scene.get("narration", "")
    on_screen = scene.get("on_screen_text", "")
    query = (scene.get("visual_spec") or {}).get("query", "")
    scene_tokens = _tokenize(f"{narration} {on_screen} {query}")

    best_score = 0
    best = None
    for src in sources:
        src_tokens = src.get("tokens", set())
        if isinstance(src_tokens, list):
            src_tokens = set(src_tokens)
        score = len(scene_tokens & src_tokens)
        if src.get("is_authority"):
            score += 1
        if score > best_score:
            best_score = score
            best = src

    if best_score < MIN_SOURCE_SCORE:
        return None
    return best
