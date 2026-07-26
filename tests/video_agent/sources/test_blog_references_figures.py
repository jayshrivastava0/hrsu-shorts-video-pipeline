"""Tests for the new paper-figure scraping in BlogReferencesSource."""
import responses
from video_agent.sources.blog_references import (
    BlogReference, BlogReferencesSource, _scrape_page_figures,
)

_REF_URL = "https://example-journal.com/paper/calcium-nitrate-wastewater"


def _ref(url=_REF_URL, authoritative=True):
    return BlogReference(url=url, title="Calcium Nitrate in Wastewater Treatment",
                         authoritative=authoritative)


@responses.activate
def test_scrape_page_figures_finds_figure_elements():
    html = """<html><body>
    <figure>
      <img src="/images/fig1.png" alt="Figure 1: Denitrification rates">
      <figcaption>Figure 1: Denitrification rates at varying CN dosages.</figcaption>
    </figure>
    <figure>
      <img src="/images/fig2.png" alt="Figure 2: pH curve">
      <figcaption>Figure 2: pH response over time.</figcaption>
    </figure>
    </body></html>"""
    responses.add(responses.GET, _REF_URL, body=html,
                  content_type="text/html")
    results = _scrape_page_figures(_REF_URL, max_figures=3)
    assert len(results) == 2
    assert "fig1.png" in results[0][0]
    assert "Denitrification rates" in results[0][1]


@responses.activate
def test_scrape_page_figures_fallback_to_alt_keywords():
    html = """<html><body>
    <img src="/static/logo.png" alt="Company Logo">
    <img src="/charts/diagram.png" alt="Schematic diagram of process">
    </body></html>"""
    responses.add(responses.GET, _REF_URL, body=html,
                  content_type="text/html")
    results = _scrape_page_figures(_REF_URL, max_figures=3)
    assert len(results) == 1
    assert "diagram.png" in results[0][0]
    assert "diagram" in results[0][1].lower()


@responses.activate
def test_blog_references_source_includes_paper_figures():
    og_html = """<html><head>
    <meta property="og:image" content="https://example-journal.com/og.jpg">
    <meta property="og:title" content="Calcium Nitrate Study">
    </head><body>
    <figure>
      <img src="/fig1.png" alt="Figure 1: Removal efficiency">
      <figcaption>Figure 1: BOD removal efficiency.</figcaption>
    </figure>
    </body></html>"""
    responses.add(responses.GET, _REF_URL, body=og_html, content_type="text/html")
    responses.add(responses.GET, _REF_URL, body=og_html, content_type="text/html")

    src = BlogReferencesSource([_ref()])
    types = [c.extra.get("image_type") for c in src._candidates]
    assert "og_image" in types
    assert "paper_figure" in types
    # Figure candidate caption includes paper title for context
    fig_cands = [c for c in src._candidates if c.extra.get("image_type") == "paper_figure"]
    assert any("Figure 1" in c.caption or "BOD removal" in c.caption
               for c in fig_cands)


@responses.activate
def test_figure_scrape_error_doesnt_break_og_image():
    og_html = """<html><head>
    <meta property="og:image" content="https://example-journal.com/og.jpg">
    </head><body></body></html>"""
    responses.add(responses.GET, _REF_URL, body=og_html, content_type="text/html")
    # Second call (figure scrape) throws
    responses.add(responses.GET, _REF_URL, body=Exception("timeout"))

    src = BlogReferencesSource([_ref()])
    og_cands = [c for c in src._candidates if c.extra.get("image_type") == "og_image"]
    assert len(og_cands) == 1
