from __future__ import annotations

from types import SimpleNamespace

from shorts_engine.retrieval import Passage, Retriever, source_kind
from shorts_engine.retrieval.fetch import fetch_text
from shorts_engine.retrieval.passages import best_passages
from shorts_engine.retrieval.search import SearchHit, web_search


class FakeDDGS:
    def __init__(self, results=None, fail_times=0):
        self.results, self.fail_times, self.calls = results or [], fail_times, 0

    def text(self, query, max_results=5):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("rate limited")
        return self.results


class TestWebSearch:
    def test_maps_results_to_hits(self):
        ddgs = FakeDDGS([{"href": "https://a.gov/x", "title": "T", "body": "B"}])
        hits = web_search("q", ddgs_factory=lambda: ddgs, sleep=lambda s: None)
        assert hits == [SearchHit("https://a.gov/x", "T", "B")]

    def test_retries_then_succeeds(self):
        ddgs = FakeDDGS([{"href": "https://a.gov/x", "title": "T", "body": "B"}], fail_times=1)
        hits = web_search("q", ddgs_factory=lambda: ddgs, sleep=lambda s: None)
        assert len(hits) == 1 and ddgs.calls == 2

    def test_persistent_failure_returns_empty_not_raise(self):
        ddgs = FakeDDGS(fail_times=99)
        assert web_search("q", ddgs_factory=lambda: ddgs, sleep=lambda s: None) == []

    def test_missing_package_returns_empty(self):
        assert web_search("q", ddgs_factory=lambda: None, sleep=lambda s: None) == []


class FakeResp:
    def __init__(self, text, status=200, ctype="text/html; charset=utf-8"):
        self.text, self.status_code, self.headers = text, status, {"content-type": ctype}


class TestFetchText:
    def test_extracts_paragraph_text_and_drops_scripts(self):
        html = "<html><script>bad()</script><p>Calcium nitrate dissolves.</p><p>Second.</p></html>"
        assert fetch_text("https://x", get=lambda u, timeout: FakeResp(html)) == \
            "Calcium nitrate dissolves. Second."

    def test_non_200_non_html_and_exceptions_return_empty(self):
        assert fetch_text("https://x", get=lambda u, timeout: FakeResp("x", status=404)) == ""
        assert fetch_text("https://x", get=lambda u, timeout: FakeResp("x", ctype="application/pdf")) == ""

        def boom(u, timeout):
            raise TimeoutError("slow")
        assert fetch_text("https://x", get=boom) == ""


class TestBestPassages:
    TEXT = ("The weather was nice. Calcium nitrate dissolves readily in water at room "
            "temperature. Sand drains quickly. Foliar sprays need soluble calcium nitrate.")

    def test_returns_most_relevant_sentences_first(self):
        out = best_passages(self.TEXT, "calcium nitrate dissolves in water", k=2)
        assert "dissolves readily in water" in out[0]
        assert not any("weather" in p for p in out)

    def test_no_overlap_returns_empty(self):
        assert best_passages(self.TEXT, "quantum chromodynamics", k=3) == []


class TestSourceKind:
    def test_kinds(self):
        assert source_kind("https://www.epa.gov/a") == "authoritative"
        assert source_kind("https://pubmed.ncbi.nlm.nih.gov/1") == "authoritative"
        assert source_kind("https://some.edu/p") == "authoritative"
        assert source_kind("https://randomblog.com/p") == "web"


class TestRetriever:
    def _make(self, hits, pages, calls=None):
        def search_fn(q):
            if calls is not None:
                calls.append(q)
            return hits

        return Retriever(["https://blog-cite.org/ref"], search_fn=search_fn,
                         fetch_fn=lambda u: pages.get(u, ""))

    PAGE = "Calcium nitrate dissolves readily in water. It is used in fertigation."

    def test_ranks_blog_citation_then_authoritative_then_web(self):
        hits = [SearchHit("https://randomblog.com/a", "", ""),
                SearchHit("https://www.epa.gov/b", "", "")]
        pages = {u: self.PAGE for u in ("https://blog-cite.org/ref", "https://randomblog.com/a",
                                        "https://www.epa.gov/b")}
        out = self._make(hits, pages).retrieve("calcium nitrate dissolves in water")
        assert [p.kind for p in out][:3] == ["blog_citation", "authoritative", "web"]

    def test_empty_search_and_failed_fetch_return_empty_list(self):
        assert self._make([], {}).retrieve("anything at all") == []

    def test_results_are_cached_per_claim(self):
        calls = []
        r = self._make([SearchHit("https://www.epa.gov/b", "", "")],
                       {"https://www.epa.gov/b": self.PAGE}, calls)
        r.retrieve("calcium nitrate dissolves in water")
        r.retrieve("Calcium nitrate dissolves in water")  # same after normalisation
        assert len(calls) == 1

    def test_never_raises_when_search_fn_blows_up(self):
        def bad(q):
            raise RuntimeError("boom")
        r = Retriever([], search_fn=bad, fetch_fn=lambda u: "")
        assert r.retrieve("x y z") == []
