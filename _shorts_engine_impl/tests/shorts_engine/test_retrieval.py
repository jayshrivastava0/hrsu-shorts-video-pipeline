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

    def test_search_hits_get_fetch_budget_despite_many_citations(self):
        from shorts_engine import config
        cites = [f"https://cite{i}.org/page" for i in range(33)]
        hits = [SearchHit("https://www.epa.gov/b", "", ""),
                SearchHit("https://randomblog.com/a", "", "")]
        fetched = []
        r = Retriever(cites, search_fn=lambda q: hits,
                      fetch_fn=lambda u: fetched.append(u) or self.PAGE)
        out = r.retrieve("calcium nitrate dissolves in water")
        assert "https://www.epa.gov/b" in fetched
        assert len(fetched) <= config.RETRIEVAL_MAX_FETCHES
        assert len(out) <= config.RETRIEVAL_MAX_PASSAGES

    def test_citations_chosen_by_overlap_with_claim(self):
        from shorts_engine import config
        cites = [f"https://cite{i}.org/misc-page" for i in range(10)]
        cites.append("https://cite-x.org/calcium-nitrate-solubility")
        fetched = []
        r = Retriever(cites, search_fn=lambda q: [],
                      fetch_fn=lambda u: fetched.append(u) or "")
        r.retrieve("calcium nitrate solubility in water")
        assert "https://cite-x.org/calcium-nitrate-solubility" in fetched
        assert len(fetched) <= config.RETRIEVAL_MAX_FETCHES

    def test_url_fetched_at_most_once_per_retriever(self):
        fetched = []
        hits = [SearchHit("https://www.epa.gov/b", "", "")]
        r = Retriever(["https://blog-cite.org/ref"], search_fn=lambda q: hits,
                      fetch_fn=lambda u: fetched.append(u) or self.PAGE)
        r.retrieve("calcium nitrate dissolves in water")
        r.retrieve("calcium nitrate is used in fertigation")
        assert sorted(fetched) == ["https://blog-cite.org/ref", "https://www.epa.gov/b"]

    def test_empty_fetch_results_are_cached_too(self):
        fetched = []
        r = Retriever(["https://blog-cite.org/ref"], search_fn=lambda q: [],
                      fetch_fn=lambda u: fetched.append(u) or "")
        r.retrieve("first claim words")
        r.retrieve("second claim words")
        assert fetched == ["https://blog-cite.org/ref"]

    def test_one_url_raising_does_not_lose_the_others(self):
        hits = [SearchHit("https://www.epa.gov/b", "", "")]

        def fetch(u):
            if "blog-cite" in u:
                raise RuntimeError("boom")
            return self.PAGE
        r = Retriever(["https://blog-cite.org/ref"], search_fn=lambda q: hits, fetch_fn=fetch)
        out = r.retrieve("calcium nitrate dissolves in water")
        assert out and all(p.url == "https://www.epa.gov/b" for p in out)

    def test_trailing_slash_duplicates_fetched_once(self):
        fetched = []
        hits = [SearchHit("https://www.epa.gov/b/", "", ""),
                SearchHit("https://www.epa.gov/b", "", ""),
                SearchHit("https://blog-cite.org/ref/", "", "")]
        r = Retriever(["https://blog-cite.org/ref"], search_fn=lambda q: hits,
                      fetch_fn=lambda u: fetched.append(u) or self.PAGE)
        r.retrieve("calcium nitrate dissolves in water")
        norm = [u.rstrip("/") for u in fetched]
        assert sorted(norm) == ["https://blog-cite.org/ref", "https://www.epa.gov/b"]

    def test_never_raises_when_search_fn_blows_up(self):
        def bad(q):
            raise RuntimeError("boom")
        r = Retriever([], search_fn=bad, fetch_fn=lambda u: "")
        assert r.retrieve("x y z") == []


class TestMalformedUrls:
    PAGE = "Calcium nitrate dissolves readily in water. It is used in fertigation."

    def test_malformed_citation_does_not_lose_the_good_one(self):
        fetched = []
        r = Retriever(["http://[bad", "https://blog-cite.org/ref"], search_fn=lambda q: [],
                      fetch_fn=lambda u: fetched.append(u) or self.PAGE)
        out = r.retrieve("calcium nitrate dissolves in water")
        assert "https://blog-cite.org/ref" in fetched
        assert out and any(p.url == "https://blog-cite.org/ref" for p in out)

    def test_source_kind_never_raises(self):
        assert source_kind("http://[bad") == "web"


class TestKeyTermsQuery:
    def test_keeps_up_to_six_informative_words_in_claim_order(self):
        from shorts_engine.retrieval.passages import key_terms_query
        q = key_terms_query("Chloride ions break down the protective oxide film on steel, "
                            "which starts pitting corrosion in the mining fleet")
        words = q.split()
        assert len(words) == 6
        assert "the" not in words and "on" not in words
        assert "chloride" in words and "protective" in words and "corrosion" in words
        text = ("chloride ions break down the protective oxide film on steel which starts "
                "pitting corrosion in the mining fleet").split()
        assert words == sorted(words, key=text.index)   # original order kept

    def test_is_deterministic_and_short_claims_pass_through(self):
        from shorts_engine.retrieval.passages import key_terms_query
        claim = "Calcium nitrate retains moisture without the corrosive profile of chlorides"
        assert len({key_terms_query(claim) for _ in range(5)}) == 1
        assert key_terms_query("Calcium nitrate dissolves") == "calcium nitrate dissolves"
        assert key_terms_query("a an the") == ""


class TestRetrieverVariants:
    """Spec section 12: retry with query variants; never cache a failure-caused empty."""
    PAGE = "Calcium nitrate dissolves readily in water. It is used in fertigation."
    CLAIM = "Calcium nitrate dissolves readily in water at room temperature"

    def test_second_variant_is_tried_when_the_first_finds_nothing(self):
        queries = []

        def search(q):
            queries.append(q)
            return [] if len(queries) == 1 else [SearchHit("https://www.epa.gov/b", "", "")]

        r = Retriever([], search_fn=search, fetch_fn=lambda u: self.PAGE)
        out = r.retrieve(self.CLAIM)
        assert out and out[0].url == "https://www.epa.gov/b"
        assert len(queries) == 2 and queries[0] != queries[1]
        assert queries[0] == self.CLAIM

    def test_variants_in_order_and_stop_at_first_with_passages(self):
        from shorts_engine.retrieval.passages import key_terms_query
        queries = []
        r = Retriever([], search_fn=lambda q: queries.append(q) or [],
                      fetch_fn=lambda u: self.PAGE)
        assert r.retrieve(self.CLAIM) == []
        terms = key_terms_query(self.CLAIM)
        assert queries == [self.CLAIM, terms, f"{terms} why how"]

    def test_all_variants_empty_after_successful_searches_is_cached(self):
        queries = []
        r = Retriever([], search_fn=lambda q: queries.append(q) or [],
                      fetch_fn=lambda u: self.PAGE)
        assert r.retrieve(self.CLAIM) == []
        n = len(queries)
        assert n == 3
        assert r.retrieve(self.CLAIM) == []
        assert len(queries) == n            # cached: no new searches
        assert r.last_search_failed is False

    def test_search_failure_is_not_cached(self):
        calls = []

        def bad(q):
            calls.append(q)
            raise RuntimeError("rate limited")

        r = Retriever([], search_fn=bad, fetch_fn=lambda u: self.PAGE)
        assert r.retrieve(self.CLAIM) == []
        assert r.last_search_failed is True
        n = len(calls)
        assert n >= 1
        assert r.retrieve(self.CLAIM) == []
        assert len(calls) > n               # searched again: the failure was not cached

    def test_recovered_search_after_failure_finds_passages(self):
        state = {"fail": True}

        def flaky(q):
            if state["fail"]:
                raise RuntimeError("down")
            return [SearchHit("https://www.epa.gov/b", "", "")]

        r = Retriever([], search_fn=flaky, fetch_fn=lambda u: self.PAGE)
        assert r.retrieve(self.CLAIM) == []
        state["fail"] = False
        assert r.retrieve(self.CLAIM)
        assert r.last_search_failed is False

    def test_fetch_failure_empty_is_not_cached(self):
        calls = []

        def fetch(u):
            raise RuntimeError("timeout")

        r = Retriever([], search_fn=lambda q: calls.append(q) or
                      [SearchHit("https://www.epa.gov/b", "", "")], fetch_fn=fetch)
        assert r.retrieve(self.CLAIM) == []
        n = len(calls)
        r.retrieve(self.CLAIM)
        assert len(calls) > n

    def test_variants_share_the_per_url_fetch_cache(self):
        fetched = []
        hits = [SearchHit("https://www.epa.gov/b", "", "")]
        r = Retriever([], search_fn=lambda q: hits,
                      fetch_fn=lambda u: fetched.append(u) or "unrelated text only")
        assert r.retrieve(self.CLAIM) == []
        assert fetched == ["https://www.epa.gov/b"]

    def test_default_search_failure_counts_as_failure(self, monkeypatch):
        import shorts_engine.retrieval as retrieval_pkg

        def failing_web_search(query, *a, raise_on_failure=False, **kw):
            if raise_on_failure:
                raise RuntimeError("all retries failed")
            return []

        monkeypatch.setattr(retrieval_pkg, "web_search", failing_web_search)
        r = Retriever([], fetch_fn=lambda u: self.PAGE)
        assert r.retrieve(self.CLAIM) == []
        assert r.last_search_failed is True

    def test_never_raises(self):
        def boom(q):
            raise ValueError("x")

        r = Retriever(["http://[bad"], search_fn=boom, fetch_fn=lambda u: 1 / 0)
        assert r.retrieve("anything") == []


class TestWebSearchFailureSignal:
    def test_raise_on_failure_raises_after_retries(self):
        import pytest
        ddgs = FakeDDGS(fail_times=99)
        with pytest.raises(Exception):
            web_search("q", ddgs_factory=lambda: ddgs, sleep=lambda s: None,
                       raise_on_failure=True)

    def test_raise_on_failure_still_returns_hits_on_success(self):
        ddgs = FakeDDGS([{"href": "https://a.gov/x", "title": "T", "body": "B"}])
        assert web_search("q", ddgs_factory=lambda: ddgs, sleep=lambda s: None,
                          raise_on_failure=True) == [SearchHit("https://a.gov/x", "T", "B")]

    def test_empty_success_does_not_raise(self):
        ddgs = FakeDDGS([])
        assert web_search("q", ddgs_factory=lambda: ddgs, sleep=lambda s: None,
                          raise_on_failure=True) == []
