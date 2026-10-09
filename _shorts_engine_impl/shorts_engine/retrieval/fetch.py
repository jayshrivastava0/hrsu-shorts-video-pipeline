"""Fetch a web page and reduce it to readable paragraph text (never raises)."""
from __future__ import annotations

import logging

import requests
from bs4 import BeautifulSoup

from shorts_engine import config

logger = logging.getLogger(__name__)


def fetch_text(url: str, *, get=None, timeout: float = config.RETRIEVAL_FETCH_TIMEOUT_S) -> str:
    """Return the page's paragraph text, or "" on any failure or non-HTML content."""
    get = get or (lambda u, timeout: requests.get(
        u, timeout=timeout, headers={"User-Agent": "Mozilla/5.0 (HRSU explainer fact-check)"}))
    try:
        resp = get(url, timeout=timeout)
        if resp.status_code != 200:
            return ""
        if "html" not in resp.headers.get("content-type", "").lower():
            return ""
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        return " ".join(p.get_text(" ", strip=True) for p in soup.find_all("p")
                        if p.get_text(strip=True))
    except Exception as exc:
        logger.warning(f"fetch_text failed for {url}: {exc}")
        return ""
