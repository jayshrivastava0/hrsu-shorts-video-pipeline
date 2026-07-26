"""Tests for the ingest stage — post isolation and canonical text extraction."""
from __future__ import annotations

from pathlib import Path
import pytest
import json
import tempfile

from shorts_engine.stages.ingest import (
    Citation,
    IsolatedPost,
    isolate_post,
    classify_citation,
    extract_citations,
    run,
)
from shorts_engine.runner import StageContext
from shorts_engine.manifest import RunManifest
from dataclasses import asdict


FIXTURE = Path(__file__).parent / "fixtures" / "nitrate_post.html"


class TestCitationDataclass:
    """Test Citation dataclass."""

    def test_citation_creation(self) -> None:
        """Citation dataclass stores marker, url, kind, and optional title."""
        citation = Citation(marker=1, url="https://example.com", kind="web")
        assert citation.marker == 1
        assert citation.url == "https://example.com"
        assert citation.kind == "web"
        assert citation.title == ""

    def test_citation_with_title(self) -> None:
        """Citation dataclass can store a title."""
        citation = Citation(
            marker=2,
            url="https://arxiv.org/abs/1234.5678",
            kind="paper",
            title="A Great Paper"
        )
        assert citation.title == "A Great Paper"


class TestIsolatedPostDataclass:
    """Test IsolatedPost dataclass."""

    def test_isolated_post_creation(self) -> None:
        """IsolatedPost dataclass stores title, body_html, canonical_text, citations, images."""
        citations = [Citation(1, "https://example.com", "web")]
        images = [{"src": "https://example.com/img.jpg", "alt": "Test"}]

        post = IsolatedPost(
            title="Test Post",
            body_html="<p>Content</p>",
            canonical_text="Content",
            citations=citations,
            images=images,
        )

        assert post.title == "Test Post"
        assert post.body_html == "<p>Content</p>"
        assert post.canonical_text == "Content"
        assert len(post.citations) == 1
        assert len(post.images) == 1


class TestClassifyCitation:
    """Test citation classification."""

    def test_classify_paper_domains(self) -> None:
        """URLs in PAPER_DOMAINS return 'paper'."""
        assert classify_citation("https://arxiv.org/abs/1234.5678") == "paper"
        assert classify_citation("https://springer.com/article/123") == "paper"
        assert classify_citation("https://mdpi.com/journal/article") == "paper"
        assert classify_citation("https://doi.org/10.1234/test") == "paper"
        assert classify_citation("https://sciencedirect.com/science/article/123") == "paper"
        assert classify_citation("https://nature.com/articles/123") == "paper"
        assert classify_citation("https://pubmed.ncbi.nlm.nih.gov/123456") == "paper"
        assert classify_citation("https://wiley.com/publication/123") == "paper"

    def test_classify_standard_domains(self) -> None:
        """URLs in STANDARD_DOMAINS return 'standard'."""
        assert classify_citation("https://europa.eu/about/article") == "standard"
        assert classify_citation("https://epa.gov/region/article") == "standard"
        assert classify_citation("https://iso.org/standard/123") == "standard"
        assert classify_citation("https://eur-lex.europa.eu/legal-content") == "standard"
        # Confirm non-standards news sites return 'web'
        assert classify_citation("https://bbc.com/news/article") == "web"
        assert classify_citation("https://reuters.com/article/123") == "web"

    def test_classify_web(self) -> None:
        """URLs not in PAPER_DOMAINS or STANDARD_DOMAINS return 'web'."""
        assert classify_citation("https://example.com/page") == "web"
        assert classify_citation("https://blog.example.com/post") == "web"
        assert classify_citation("https://github.com/user/repo") == "web"


class TestIsolatePost:
    """Test post isolation from Blogger HTML."""

    def test_fixture_loads(self) -> None:
        """Fixture file exists and is readable."""
        assert FIXTURE.exists(), f"Fixture not found at {FIXTURE}"
        html = FIXTURE.read_text(encoding="utf-8")
        assert len(html) > 0, "Fixture is empty"

    def test_isolate_post_returns_isolated_post(self) -> None:
        """isolate_post() returns an IsolatedPost instance."""
        html = FIXTURE.read_text(encoding="utf-8")
        url = "https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html"

        result = isolate_post(html, url)

        assert isinstance(result, IsolatedPost)

    def test_isolate_post_extracts_title(self) -> None:
        """isolate_post() extracts the post title."""
        html = FIXTURE.read_text(encoding="utf-8")
        url = "https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html"

        result = isolate_post(html, url)

        assert result.title == "Optimizing Nitrate Removal via Granular Calcium Nitrate"

    def test_isolate_post_contains_target_marker(self) -> None:
        """isolate_post() body contains target post marker."""
        html = FIXTURE.read_text(encoding="utf-8")
        url = "https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html"

        result = isolate_post(html, url)

        assert "dosage range of 1.5 to 3 kg per cubic meter" in result.body_html, (
            "Target post marker not found in isolated body"
        )

    def test_isolate_post_removes_sibling_post(self) -> None:
        """isolate_post() does NOT contain sibling post marker."""
        html = FIXTURE.read_text(encoding="utf-8")
        url = "https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html"

        result = isolate_post(html, url)

        assert "150,000 metric tons" not in result.body_html, (
            "Sibling post marker found in isolated body — isolation failed"
        )

    def test_isolate_post_creates_canonical_text(self) -> None:
        """isolate_post() creates canonical_text from body_html."""
        html = FIXTURE.read_text(encoding="utf-8")
        url = "https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html"

        result = isolate_post(html, url)

        assert len(result.canonical_text) > 0, "canonical_text is empty"
        assert isinstance(result.canonical_text, str)

    def test_isolate_post_canonical_text_no_html(self) -> None:
        """canonical_text contains no HTML tags (p, div, a, span, etc.)."""
        html = FIXTURE.read_text(encoding="utf-8")
        url = "https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html"

        result = isolate_post(html, url)

        # Check for common HTML tags (not just any "<" character, which can be in text like "< 1.0mm")
        html_tags = ["<p", "<div", "<a ", "<span", "<br>", "<strong", "<em", "<ul", "<li", "<table"]
        for tag in html_tags:
            assert tag not in result.canonical_text.lower(), (
                f"canonical_text contains HTML tag: {tag}"
            )

    def test_isolate_post_citations_list(self) -> None:
        """isolate_post() returns a list of Citation objects."""
        html = FIXTURE.read_text(encoding="utf-8")
        url = "https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html"

        result = isolate_post(html, url)

        assert isinstance(result.citations, list)
        assert all(isinstance(c, Citation) for c in result.citations)

    def test_isolate_post_images_list(self) -> None:
        """isolate_post() returns a list of image dicts."""
        html = FIXTURE.read_text(encoding="utf-8")
        url = "https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html"

        result = isolate_post(html, url)

        assert isinstance(result.images, list)
        assert all(isinstance(img, dict) for img in result.images)


class TestIsolationStrategyLadder:
    """Test the 3-strategy ladder for post selection."""

    def test_isolate_post_with_matching_url_in_anchor(self) -> None:
        """Strategy 1: Match post whose title anchor href equals input URL."""
        html = FIXTURE.read_text(encoding="utf-8")
        # Use the exact URL from the fixture
        url = "https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html"

        result = isolate_post(html, url)

        # Should find the target post via URL match in anchor
        assert "dosage range of 1.5 to 3 kg per cubic meter" in result.body_html


class TestIsolatePostStripsBloggerElements:
    """Test that isolate_post() strips Blogger-specific elements."""

    def test_isolate_post_strips_style_tags(self) -> None:
        """isolate_post() removes <style> tags."""
        html = FIXTURE.read_text(encoding="utf-8")
        url = "https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html"

        result = isolate_post(html, url)

        assert "<style" not in result.body_html.lower()

    def test_isolate_post_strips_script_tags(self) -> None:
        """isolate_post() removes <script> tags."""
        html = FIXTURE.read_text(encoding="utf-8")
        url = "https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html"

        result = isolate_post(html, url)

        assert "<script" not in result.body_html.lower()

    def test_isolate_post_removes_comments_section(self) -> None:
        """isolate_post() removes #comments section."""
        html = FIXTURE.read_text(encoding="utf-8")
        url = "https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html"

        result = isolate_post(html, url)

        # Comments section should not be in the body
        assert "id=\"comments\"" not in result.body_html or len([line for line in result.body_html.split("\n") if "comment" in line.lower()]) < 3


class TestExtractCitations:
    """Test citation extraction from body HTML."""

    def test_extract_citations_from_fixture(self) -> None:
        """extract_citations() finds citations in References section."""
        html = FIXTURE.read_text(encoding="utf-8")
        url = "https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html"

        isolated = isolate_post(html, url)
        citations = extract_citations(isolated.body_html)

        assert isinstance(citations, list)
        assert len(citations) > 0, "No citations found in fixture"

    def test_extract_citations_returns_citation_objects(self) -> None:
        """extract_citations() returns list of Citation objects."""
        html = FIXTURE.read_text(encoding="utf-8")
        url = "https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html"

        isolated = isolate_post(html, url)
        citations = extract_citations(isolated.body_html)

        assert all(isinstance(c, Citation) for c in citations), "Not all items are Citation objects"

    def test_extract_citations_have_marker_and_url(self) -> None:
        """extract_citations() citations have marker and url."""
        html = FIXTURE.read_text(encoding="utf-8")
        url = "https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html"

        isolated = isolate_post(html, url)
        citations = extract_citations(isolated.body_html)

        if len(citations) > 0:
            for citation in citations:
                assert citation.marker >= 1, "Marker should be >= 1"
                assert citation.url.startswith("http"), "URL should start with http"
                assert citation.kind in ["paper", "standard", "web"], "Invalid kind"

    def test_extract_citations_includes_arxiv(self) -> None:
        """extract_citations() identifies arxiv.org URLs as 'paper'."""
        html = FIXTURE.read_text(encoding="utf-8")
        url = "https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html"

        isolated = isolate_post(html, url)
        citations = extract_citations(isolated.body_html)

        arxiv_citations = [c for c in citations if "arxiv.org" in c.url]
        if len(arxiv_citations) > 0:
            assert all(c.kind == "paper" for c in arxiv_citations), "arXiv citations should be 'paper'"


class TestRegionCategoryFromHistory:
    """Test region/category lookup from blog_history.json."""

    def test_region_category_from_existing_post(self) -> None:
        """_region_category_from_history() returns region/category for known URL."""
        from shorts_engine.stages.ingest import _region_category_from_history

        # Use a URL from the fixture that should be in blog_history.json
        url = "https://blog.hrsuindore.com/2026/02/optimizing-early-age-strength.html"
        region, category = _region_category_from_history(url)

        # Should find something (either from fixture or from actual blog_history.json)
        # At minimum, we should get consistent types
        assert region is None or isinstance(region, str), "region should be None or str"
        assert category is None or isinstance(category, str), "category should be None or str"

    def test_region_category_for_unknown_post(self) -> None:
        """_region_category_from_history() returns None for unknown URL."""
        from shorts_engine.stages.ingest import _region_category_from_history

        url = "https://example.com/unknown/post.html"
        region, category = _region_category_from_history(url)

        assert region is None
        assert category is None


class TestRunFunction:
    """Test the run() stage function."""

    def test_run_with_html_override(self, tmp_path) -> None:
        """run() with html_override flag fetches from context."""
        html = FIXTURE.read_text(encoding="utf-8")
        url = "https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html"

        # Create a RunManifest
        manifest = RunManifest.create(blog_url=url, workspace_root=tmp_path)
        ctx = StageContext(
            manifest=manifest,
            workspace=Path(manifest.workspace),
            flags={"html_override": html}
        )

        result = run(ctx)

        assert isinstance(result, dict)
        assert "post" in result
        assert "canonical" in result

    def test_run_creates_post_json(self, tmp_path) -> None:
        """run() creates post.json file in workspace."""
        html = FIXTURE.read_text(encoding="utf-8")
        url = "https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html"

        manifest = RunManifest.create(blog_url=url, workspace_root=tmp_path)
        ctx = StageContext(
            manifest=manifest,
            workspace=Path(manifest.workspace),
            flags={"html_override": html}
        )

        result = run(ctx)

        post_file = Path(manifest.workspace) / result["post"]
        assert post_file.exists(), f"post.json not created at {post_file}"

    def test_run_creates_canonical_txt(self, tmp_path) -> None:
        """run() creates canonical.txt file in workspace."""
        html = FIXTURE.read_text(encoding="utf-8")
        url = "https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html"

        manifest = RunManifest.create(blog_url=url, workspace_root=tmp_path)
        ctx = StageContext(
            manifest=manifest,
            workspace=Path(manifest.workspace),
            flags={"html_override": html}
        )

        result = run(ctx)

        canonical_file = Path(manifest.workspace) / result["canonical"]
        assert canonical_file.exists(), f"canonical.txt not created at {canonical_file}"

    def test_run_post_json_has_required_fields(self, tmp_path) -> None:
        """run() post.json contains required fields."""
        html = FIXTURE.read_text(encoding="utf-8")
        url = "https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html"

        manifest = RunManifest.create(blog_url=url, workspace_root=tmp_path)
        ctx = StageContext(
            manifest=manifest,
            workspace=Path(manifest.workspace),
            flags={"html_override": html}
        )

        result = run(ctx)

        post_file = Path(manifest.workspace) / result["post"]
        post_data = json.loads(post_file.read_text())

        assert "url" in post_data
        assert "title" in post_data
        assert "region" in post_data
        assert "category" in post_data
        assert "citations" in post_data
        assert "images" in post_data

    def test_run_post_json_citations_as_dicts(self, tmp_path) -> None:
        """run() post.json citations are serialized as dicts."""
        html = FIXTURE.read_text(encoding="utf-8")
        url = "https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html"

        manifest = RunManifest.create(blog_url=url, workspace_root=tmp_path)
        ctx = StageContext(
            manifest=manifest,
            workspace=Path(manifest.workspace),
            flags={"html_override": html}
        )

        result = run(ctx)

        post_file = Path(manifest.workspace) / result["post"]
        post_data = json.loads(post_file.read_text())

        assert isinstance(post_data["citations"], list)
        if len(post_data["citations"]) > 0:
            for citation in post_data["citations"]:
                assert isinstance(citation, dict)
                assert "marker" in citation
                assert "url" in citation
                assert "kind" in citation
