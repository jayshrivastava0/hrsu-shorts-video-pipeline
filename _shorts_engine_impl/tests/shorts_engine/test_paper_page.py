from __future__ import annotations
from pathlib import Path
import pytest
from PIL import Image


def _tiny_pdf_bytes() -> bytes:
    """One-page PDF built with pypdfium2's raw API is overkill — write a
    minimal hand-rolled valid PDF (blank A4 page)."""
    return (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]>>endobj\n"
            b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n"
            b"0000000052 00000 n \n0000000101 00000 n \n"
            b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n164\n%%EOF")


class TestUrlClassification:
    def test_is_pdf_url(self):
        from shorts_engine.sourcing import paper_page as pp
        assert pp.is_pdf_url("https://arxiv.org/pdf/2602.21290")
        assert pp.is_pdf_url("https://www.mdpi.com/2073-4441/12/5/1234/pdf")
        assert pp.is_pdf_url("https://example.com/paper.pdf")
        assert not pp.is_pdf_url("https://pubmed.ncbi.nlm.nih.gov/18462937/")

    def test_cache_key_stable(self):
        from shorts_engine.sourcing import paper_page as pp
        assert pp.cache_key("https://a.com/x") == pp.cache_key("https://a.com/x")
        assert len(pp.cache_key("https://a.com/x")) == 16


class TestPdfRender:
    def test_renders_page1_to_png(self, tmp_path):
        from shorts_engine.sourcing import paper_page as pp
        out = pp.render_pdf_page1(_tiny_pdf_bytes(), tmp_path / "p.png")
        assert out is not None and out.exists()
        with Image.open(out) as img:
            assert img.width >= 1200  # rendered at readable scale

    def test_garbage_bytes_return_none(self, tmp_path):
        from shorts_engine.sourcing import paper_page as pp
        assert pp.render_pdf_page1(b"not a pdf", tmp_path / "p.png") is None


class TestFetchFrontPage:
    def test_cache_hit_short_circuits(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import paper_page as pp
        from shorts_engine import config
        monkeypatch.setattr(config, "PAPER_CACHE_DIR", tmp_path)
        url = "https://arxiv.org/pdf/2602.21290"
        cached = tmp_path / (pp.cache_key(url) + ".png")
        Image.new("RGB", (1600, 2000), (255, 255, 255)).save(cached)
        called = []
        monkeypatch.setattr(pp, "_fetch_bytes", lambda u: called.append(u))
        assert pp.fetch_front_page(url) == cached
        assert called == []

    def test_torture_mode_never_fetches(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import paper_page as pp
        from shorts_engine import config
        monkeypatch.setattr(config, "PAPER_CACHE_DIR", tmp_path)
        called = []
        monkeypatch.setattr(pp, "_fetch_bytes", lambda u: called.append(u))
        assert pp.fetch_front_page("https://arxiv.org/pdf/1", torture=True) is None
        assert called == []

    def test_pdf_path_fetches_renders_and_caches(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import paper_page as pp
        from shorts_engine import config
        monkeypatch.setattr(config, "PAPER_CACHE_DIR", tmp_path)
        monkeypatch.setattr(pp, "_fetch_bytes", lambda u: _tiny_pdf_bytes())
        url = "https://arxiv.org/pdf/2602.21290"
        out = pp.fetch_front_page(url)
        assert out is not None and out.exists()
        assert out.name == pp.cache_key(url) + ".png"

    def test_landing_page_uses_screenshot_seam(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import paper_page as pp
        from shorts_engine import config
        monkeypatch.setattr(config, "PAPER_CACHE_DIR", tmp_path)
        def fake_shot(url, out_png):
            Image.new("RGB", (1200, 900), (250, 250, 250)).save(out_png)
            return out_png
        monkeypatch.setattr(pp, "_screenshot", fake_shot)
        out = pp.fetch_front_page("https://pubmed.ncbi.nlm.nih.gov/18462937/")
        assert out is not None and out.exists()

    def test_both_paths_failing_returns_none(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import paper_page as pp
        from shorts_engine import config
        monkeypatch.setattr(config, "PAPER_CACHE_DIR", tmp_path)
        monkeypatch.setattr(pp, "_fetch_bytes", lambda u: None)
        monkeypatch.setattr(pp, "_screenshot", lambda u, o: None)
        assert pp.fetch_front_page("https://x.example/paper.pdf") is None
        assert pp.fetch_front_page("https://x.example/landing") is None
