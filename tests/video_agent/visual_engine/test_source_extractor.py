"""TDD tests for source_extractor — blog image and citation-PDF mining."""
import io
import json
import struct
import zlib
from pathlib import Path

import pytest
import responses as resp_lib
from responses import GET

from video_agent.visual_engine.source_extractor import (
    extract_blog_sources,
    find_source_for_scene,
    _parse_html_images,
    _extract_authority_links,
    _should_skip_image,
)


# ─── helpers ────────────────────────────────────────────────────────────────

def _make_jpeg(w=800, h=600) -> bytes:
    """Minimal valid JPEG bytes (enough to pass PIL size check)."""
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color=(100, 149, 237)).save(buf, format="JPEG")
    return buf.getvalue()


def _make_tiny_jpeg() -> bytes:
    """Tiny JPEG that should be skipped (too small)."""
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (50, 50), color=(0, 0, 0)).save(buf, format="JPEG")
    return buf.getvalue()


def _make_pdf(text: str = "Calcium nitrate dosage") -> bytes:
    """Minimal single-page PDF with text."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 72), text, fontsize=12)
    return doc.tobytes()


# ─── _parse_html_images ─────────────────────────────────────────────────────

def test_parse_html_images_finds_img_tags():
    html = '<p>Text <img src="/fig1.jpg" alt="H2S chart"> more text.</p>'
    results = _parse_html_images(html, "https://blog.example.com/post")
    assert len(results) == 1
    src, alt, ctx = results[0]
    assert src == "https://blog.example.com/fig1.jpg"
    assert "H2S" in alt


def test_parse_html_images_resolves_relative_urls():
    html = '<img src="../images/process.png" alt="process">'
    results = _parse_html_images(html, "https://example.com/posts/2024/")
    assert results[0][0].startswith("https://example.com")


def test_parse_html_images_skips_logos_and_icons():
    html = '<img src="/logo.png" alt="logo"><img src="/icon-32.png" alt="icon">'
    assert _parse_html_images(html, "https://x.com") == []


def test_parse_html_images_skips_svgs():
    html = '<img src="/chart.svg" alt="chart">'
    assert _parse_html_images(html, "https://x.com") == []


# ─── _extract_authority_links ───────────────────────────────────────────────

def test_extract_authority_links_finds_pdf():
    html = 'See <a href="https://example.gov/study.pdf">study</a>.'
    links = _extract_authority_links(html)
    assert any("study.pdf" in url for url, _ in links)


def test_extract_authority_links_finds_doi():
    html = 'Cited in <a href="https://doi.org/10.1016/j.water.2020.01">paper</a>.'
    links = _extract_authority_links(html)
    assert any("doi.org" in url for url, _ in links)


def test_extract_authority_links_ignores_regular_links():
    html = '<a href="https://twitter.com/hrsu">Twitter</a>'
    assert _extract_authority_links(html) == []


# ─── _should_skip_image ─────────────────────────────────────────────────────

def test_should_skip_small_image(tmp_path):
    p = tmp_path / "tiny.jpg"
    p.write_bytes(_make_tiny_jpeg())
    assert _should_skip_image(p) is True


def test_should_not_skip_normal_image(tmp_path):
    p = tmp_path / "ok.jpg"
    p.write_bytes(_make_jpeg(800, 600))
    assert _should_skip_image(p) is False


# ─── extract_blog_sources ───────────────────────────────────────────────────

@resp_lib.activate
def test_extracts_inline_images_with_captions(tmp_path):
    html = '<p>Studies show <img src="/fig1.jpg" alt="H2S reduction chart"> a 90% reduction.</p>'
    resp_lib.add(GET, "https://blog.example.com/fig1.jpg",
                 body=_make_jpeg(), content_type="image/jpeg")
    blog = {"blog_id": "b1", "url": "https://blog.example.com/post",
            "content_html": html}
    out = extract_blog_sources(blog, tmp_path)
    assert any(s["source_type"] == "inline_image" for s in out)
    assert any("H2S" in (s.get("caption") or "") or "h2s" in s.get("tokens", set())
               for s in out)


@resp_lib.activate
def test_renders_authority_pdf_first_page(tmp_path):
    pdf_bytes = _make_pdf("Calcium nitrate dosage in WWTPs")
    resp_lib.add(GET, "https://example.gov/paper.pdf",
                 body=pdf_bytes, content_type="application/pdf")
    html = 'See <a href="https://example.gov/paper.pdf">study</a>.'
    blog = {"blog_id": "b2", "url": "https://example.gov",
            "content_html": html}
    out = extract_blog_sources(blog, tmp_path)
    pdf_pages = [s for s in out if s["source_type"] == "pdf_page"]
    assert pdf_pages, "expected at least one pdf_page entry"
    assert pdf_pages[0]["is_authority"] is True
    assert pdf_pages[0]["path"].exists()


@resp_lib.activate
def test_caches_so_second_call_skips_download(tmp_path):
    resp_lib.add(GET, "https://blog.example.com/fig2.jpg",
                 body=_make_jpeg(), content_type="image/jpeg")
    html = '<img src="/fig2.jpg" alt="chart">'
    blog = {"blog_id": "b3", "url": "https://blog.example.com/post",
            "content_html": html}
    extract_blog_sources(blog, tmp_path)         # first call — downloads
    first_count = len(resp_lib.calls)
    extract_blog_sources(blog, tmp_path)         # second call — uses cache
    assert len(resp_lib.calls) == first_count    # no new HTTP requests


@resp_lib.activate
def test_skips_logos_and_icons(tmp_path):
    html = '<img src="/logo.png" alt="logo">'
    blog = {"blog_id": "b4", "url": "https://x.com", "content_html": html}
    out = extract_blog_sources(blog, tmp_path)
    assert out == []


# ─── find_source_for_scene ──────────────────────────────────────────────────

def test_find_source_for_scene_scores_by_token_overlap(tmp_path):
    sources = [
        {"id": "a", "tokens": {"h2s", "reduction", "wastewater"},
         "is_authority": True, "path": tmp_path / "a.jpg",
         "source_type": "inline_image", "caption": "H2S reduction",
         "source_url": "https://x.com/a.jpg"},
        {"id": "b", "tokens": {"mining", "australia"},
         "is_authority": False, "path": tmp_path / "b.jpg",
         "source_type": "inline_image", "caption": "Mining",
         "source_url": "https://x.com/b.jpg"},
    ]
    scene = {"narration": "H2S reduction in wastewater plants",
             "on_screen_text": "", "visual_spec": {}}
    match = find_source_for_scene(scene, sources)
    assert match is not None
    assert match["id"] == "a"


def test_find_source_returns_none_when_no_sources():
    assert find_source_for_scene({"narration": "test"}, []) is None


def test_find_source_returns_none_below_min_score(tmp_path):
    sources = [
        {"id": "z", "tokens": {"completely", "unrelated"},
         "is_authority": False, "path": tmp_path / "z.jpg",
         "source_type": "inline_image", "caption": "",
         "source_url": "https://x.com/z.jpg"},
    ]
    scene = {"narration": "H2S reduction wastewater", "on_screen_text": "", "visual_spec": {}}
    # Only 0 token overlap → score 0 → below MIN_SCORE
    assert find_source_for_scene(scene, sources) is None
