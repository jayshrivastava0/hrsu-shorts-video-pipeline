from unittest.mock import patch, MagicMock
import pytest


def test_init_failure_returns_empty_results(monkeypatch):
    """If Playwright import or launch fails, source must not crash; search returns []."""
    import video_agent.sources.google_images_browser as mod
    # Patch the module-level variable so _init_browser treats playwright as absent
    # regardless of whether the module was already imported by a prior test.
    monkeypatch.setattr(mod, "sync_playwright", None)
    src = mod.GoogleImagesBrowserSource()
    assert src.search("anything") == []


def test_search_filters_small_thumbnails():
    """img elements with naturalWidth < 300 are filtered out (data-URI thumbnails)."""
    from video_agent.sources.google_images_browser import GoogleImagesBrowserSource
    src = GoogleImagesBrowserSource.__new__(GoogleImagesBrowserSource)
    src._init_failed = False
    src._page = MagicMock()
    src._page.url = "https://www.google.com/search"
    src._page.evaluate.return_value = [
        {"src": "https://big.jpg", "original": "", "alt": "big", "w": 1200, "h": 800},
        {"src": "https://tiny.jpg", "original": "", "alt": "tiny", "w": 50, "h": 50},
        {"src": "https://medium.jpg", "original": "", "alt": "medium", "w": 400, "h": 300},
    ]
    results = src._do_search("query", limit=5)
    urls = [r.url for r in results]
    assert "https://big.jpg" in urls
    assert "https://medium.jpg" in urls
    assert "https://tiny.jpg" not in urls


def test_search_handles_navigation_failure():
    from video_agent.sources.google_images_browser import GoogleImagesBrowserSource
    src = GoogleImagesBrowserSource.__new__(GoogleImagesBrowserSource)
    src._init_failed = False
    src._page = MagicMock()
    src._page.goto.side_effect = RuntimeError("navigation failed")
    results = src._do_search("query", 5)
    assert results == []
    # Page must NOT be closed — it's reused for future searches
    src._page.close.assert_not_called()


def test_search_no_context_returns_empty():
    from video_agent.sources.google_images_browser import GoogleImagesBrowserSource
    src = GoogleImagesBrowserSource.__new__(GoogleImagesBrowserSource)
    src._init_failed = True
    assert src.search("query") == []


def test_init_uses_persistent_context(monkeypatch):
    """Source must launch with launch_persistent_context so cookies + CAPTCHA
    tokens persist across runs."""
    import video_agent.sources.google_images_browser as mod
    fake_pw = MagicMock()
    fake_ctx = MagicMock()
    fake_ctx.pages = []
    fake_pw.chromium.launch_persistent_context.return_value = fake_ctx
    monkeypatch.setattr(mod, "sync_playwright",
                        lambda: MagicMock(start=lambda: fake_pw))
    monkeypatch.setattr(mod, "stealth_sync", lambda c: None)
    src = mod.GoogleImagesBrowserSource()
    assert fake_pw.chromium.launch_persistent_context.called


def test_search_reuses_single_page(monkeypatch):
    """A second .search() call should goto() on the SAME page object, not
    create a new tab and close it."""
    import video_agent.sources.google_images_browser as mod
    fake_page = MagicMock()
    fake_page.url = "https://www.google.com/search"
    fake_page.evaluate.return_value = []
    fake_ctx = MagicMock()
    fake_ctx.pages = [fake_page]
    fake_pw = MagicMock()
    fake_pw.chromium.launch_persistent_context.return_value = fake_ctx
    monkeypatch.setattr(mod, "sync_playwright",
                        lambda: MagicMock(start=lambda: fake_pw))
    monkeypatch.setattr(mod, "stealth_sync", lambda c: None)
    monkeypatch.setattr(mod, "_human_browse", lambda page: None)
    src = mod.GoogleImagesBrowserSource()
    src.search("first query", limit=2)
    src.search("second query", limit=2)
    assert fake_ctx.new_page.call_count == 0
    assert fake_page.goto.call_count == 2
    assert fake_page.close.call_count == 0
