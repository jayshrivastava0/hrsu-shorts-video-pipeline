import os
import time

import pytest

from scheduler import discover
from scheduler.lock import LockBusy, RunLock
from scheduler.state import State

INDEX = """
<a href="/blog/">Blog</a><a href="/blog/page/2/">Older</a>
<a href="https://hrsuindore.com/blog/first-post/">One</a>
<a href="https://hrsuindore.com/blog/second-post/">Two</a>
<a href="/blog/second-post/">dup</a><a href="/blog/Third-Post">Three</a>
<a href="https://hrsuindore.com/blog/tag/news/">tag</a>
"""
SITEMAP = ("<urlset><url><loc>https://hrsuindore.com/blog/</loc></url>"
           "<url><loc>https://hrsuindore.com/blog/map-post/</loc></url></urlset>")


def test_parse_post_urls_canonical_dedup_and_reserved():
    assert discover.parse_post_urls(INDEX) == [
        "https://hrsuindore.com/blog/first-post/",
        "https://hrsuindore.com/blog/second-post/",
        "https://hrsuindore.com/blog/third-post/",
    ]


def test_fetch_follows_page_two_until_no_new():
    pages = {discover.BLOG_BASE: INDEX,
             discover.BLOG_BASE + "page/2/": '<a href="/blog/old-post/">x</a>',
             discover.BLOG_BASE + "page/3/": '<a href="/blog/old-post/">x</a>'}
    urls = discover.fetch_post_urls(fetch=lambda u: pages[u])
    assert urls[-1] == "https://hrsuindore.com/blog/old-post/" and len(urls) == 4


def test_sitemap_fallback_when_index_fails():
    def fetch(u):
        if u == discover.SITEMAP_URL:
            return SITEMAP
        raise OSError("down")
    assert discover.fetch_post_urls(fetch=fetch) == ["https://hrsuindore.com/blog/map-post/"]


def test_total_failure_returns_empty():
    def fetch(u):
        raise OSError("down")
    assert discover.fetch_post_urls(fetch=fetch) == []


def test_state_seed_does_not_override_and_persists(tmp_path):
    p = tmp_path / "d" / "state.json"
    s = State(p)
    s.update("u1", status="published")
    assert s.seed(["u1", "u2"]) == 1
    s2 = State(p)
    assert s2.get("u1")["status"] == "published" and s2.get("u2")["status"] == "seeded"
    assert not list(p.parent.glob("*.tmp"))


def test_state_rejects_unknown_status(tmp_path):
    with pytest.raises(ValueError):
        State(tmp_path / "s.json").update("u", status="bogus")


def test_state_readonly_never_writes(tmp_path):
    p = tmp_path / "s.json"
    s = State(p, readonly=True)
    s.update("u", status="queued")
    assert not p.exists()


def test_count_published_on(tmp_path):
    s = State(tmp_path / "s.json")
    s.update("a", status="published", published_at="2026-10-12T10:00:00+05:30")
    s.update("b", status="published", published_at="2026-10-11T10:00:00+05:30")
    assert s.count_published_on("2026-10-12") == 1


def test_lock_blocks_second_and_releases(tmp_path):
    p = tmp_path / "x.lock"
    with RunLock(p):
        with pytest.raises(LockBusy):
            RunLock(p).acquire()
    RunLock(p).acquire()


def test_lock_stale_is_taken_over(tmp_path):
    p = tmp_path / "x.lock"
    p.write_text("1")
    old = time.time() - 7 * 3600
    os.utime(p, (old, old))
    RunLock(p, stale_after_s=6 * 3600).acquire()


def test_fetch_all_unions_sitemap():
    pages = {discover.BLOG_BASE: INDEX, discover.SITEMAP_URL: SITEMAP}

    def fetch(u):
        if u in pages:
            return pages[u]
        raise OSError("404")
    urls = discover.fetch_all_post_urls(fetch=fetch)
    assert urls[-1] == "https://hrsuindore.com/blog/map-post/" and len(urls) == 4


def test_awaiting_review_and_rejected_are_valid_statuses(tmp_path):
    s = State(tmp_path / "state.json")
    s.update("u1", status="awaiting_review")
    s.update("u2", status="rejected")
    assert s.get("u1")["status"] == "awaiting_review"
    assert s.get("u2")["status"] == "rejected"


def test_count_awaiting_review(tmp_path):
    s = State(tmp_path / "state.json")
    s.update("u1", status="awaiting_review")
    s.update("u2", status="awaiting_review")
    s.update("u3", status="published")
    assert s.count_awaiting_review() == 2
