"""
Ingest stage — post isolation and canonical text extraction.

This module provides:
- Citation: Dataclass for citations with marker, url, kind, optional title
- IsolatedPost: Dataclass for isolated post with title, body_html, canonical_text, citations, images
- isolate_post(): Extract target post from Blogger HTML and isolate sibling posts
- classify_citation(): Classify URL as "paper", "standard", or "web"
- run(): Stage function for orchestrator (NOT implemented in Task 6; Task 7 adds tests)
"""
from __future__ import annotations

import json
import logging
import re
import requests
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from shorts_engine import config

logger = logging.getLogger(__name__)


# ── Data Structures ────────────────────────────────────────────────────────
@dataclass
class Citation:
    """A citation extracted from the post with marker number, URL, and kind."""

    marker: int
    """Superscript marker number in the post (1, 2, 3, ...)."""

    url: str
    """The citation URL."""

    kind: str
    """Citation kind: 'paper', 'standard', or 'web'."""

    title: str = ""
    """Optional citation title."""


@dataclass
class IsolatedPost:
    """An isolated blog post with clean HTML and canonical text."""

    title: str
    """The post title."""

    body_html: str
    """Clean post body HTML (stripped of scripts, styles, comments, etc.)."""

    canonical_text: str
    """Plain text version of body_html (for NLP processing)."""

    citations: list[Citation] = field(default_factory=list)
    """List of Citation objects extracted from the post."""

    images: list[dict] = field(default_factory=list)
    """List of image dictionaries with 'src' and 'alt' keys."""


# ── Citation Classification ────────────────────────────────────────────────
def classify_citation(url: str) -> str:
    """
    Classify a URL as 'paper', 'standard', or 'web'.

    Checks if URL is in config.PAPER_DOMAINS or config.STANDARD_DOMAINS.

    Args:
        url: The URL to classify

    Returns:
        'paper' if URL is from an academic domain
        'standard' if URL is from a trusted journalism/reference domain
        'web' otherwise
    """
    url_lower = url.lower()

    # Check PAPER_DOMAINS
    for domain in config.PAPER_DOMAINS:
        if domain in url_lower:
            return "paper"

    # Check STANDARD_DOMAINS
    for domain in config.STANDARD_DOMAINS:
        if domain in url_lower:
            return "standard"

    # Default to web
    return "web"


# ── Post Isolation ────────────────────────────────────────────────────────
def isolate_post(page_html: str, url: str) -> IsolatedPost:
    """
    Isolate the target blog post from a Blogger HTML page.

    Uses a 3-strategy ladder to find the correct post:
    1. Match the post whose title anchor href == the input URL
    2. Otherwise pick the post whose title has NO anchor (current post = plain text)
    3. Otherwise pick the longest post body

    Then strips Blogger-specific elements (style, script, comments, etc.)
    and extracts citations and images.

    Args:
        page_html: Full Blogger page HTML
        url: The target post URL (used for matching)

    Returns:
        IsolatedPost with clean title, body_html, canonical_text, citations, images

    Raises:
        ValueError: If no post container can be found
    """
    soup = BeautifulSoup(page_html, "html.parser")

    # Find all post containers
    # Blogger uses <div class='post-body-container'> for main view
    # and <div class='post-outer'> for sidebar/feed view
    post_containers = soup.find_all("div", class_="post-body-container")
    post_containers.extend(soup.find_all("div", class_="post-outer"))

    if not post_containers:
        raise ValueError("No post containers found in HTML")

    # ── Strategy Ladder ──────────────────────────────────────────────────
    target_post = None

    # Strategy 1: Match by URL in anchor href
    for container in post_containers:
        # Look for an anchor with href matching the URL
        anchor = container.find("a", href=url)
        if anchor:
            target_post = container
            logger.debug(f"Found post by URL match in anchor: {url}")
            break

    # Strategy 2: Pick post with title but no anchor (plain text title)
    if not target_post:
        for container in post_containers:
            # Look for h3 with class 'post-title entry-title'
            title_h3 = container.find("h3", class_="post-title entry-title")
            if title_h3:
                # If the h3 has no anchor child, it's the current post
                if not title_h3.find("a"):
                    target_post = container
                    logger.debug("Found post by plain-text title (no anchor)")
                    break

    # Strategy 3: Pick the longest post body
    if not target_post:
        longest_container = None
        longest_length = 0
        for container in post_containers:
            body_text = container.get_text()
            if len(body_text) > longest_length:
                longest_length = len(body_text)
                longest_container = container
        target_post = longest_container
        logger.debug("Found post by longest body length")

    if not target_post:
        raise ValueError("Could not find target post using any strategy")

    # ── Extract Title ───────────────────────────────────────────────────
    title = _extract_title(target_post)

    # ── Extract and Clean Body HTML ─────────────────────────────────────
    body_html = _extract_and_clean_body(target_post)

    # ── Extract Canonical Text ─────────────────────────────────────────
    canonical_text = _extract_canonical_text(body_html)

    # ── Extract Citations ───────────────────────────────────────────────
    citations = _extract_citations(body_html)

    # ── Extract Images ─────────────────────────────────────────────────
    images = _extract_images(body_html)

    return IsolatedPost(
        title=title,
        body_html=body_html,
        canonical_text=canonical_text,
        citations=citations,
        images=images,
    )


# ── Helper Functions ──────────────────────────────────────────────────────
def _extract_title(container: Any) -> str:
    """Extract post title from container."""
    # Look for the title in the post-title-container (Blogger structure)
    title_container = container.find("div", class_="post-title-container")
    if title_container:
        title_heading = title_container.find("h3", class_="post-title entry-title")
        if title_heading:
            text = title_heading.get_text(strip=True)
            if text:
                return text

    # Fallback: look for any heading tags
    for heading in container.find_all(["h1", "h2", "h3"]):
        text = heading.get_text(strip=True)
        if text:
            return text
    return ""


def _extract_and_clean_body(container: Any) -> str:
    """Extract and clean body HTML, removing Blogger-specific elements."""
    # Look for content within the container
    # In post-body-container, look for post-body entry-content
    # In post-outer, look for post-snippet or entire content

    body_elem = container.find("div", class_="post-body entry-content")
    if body_elem:
        content_elem = body_elem
    else:
        # For post-outer, the content is usually in the post-body or entire div
        content_elem = container

    # Create a deep copy so we don't modify the original
    body_copy = BeautifulSoup(str(content_elem), "html.parser")

    # Remove elements to strip
    elements_to_remove = [
        ("style", {}),
        ("script", {}),
        ("link", {}),
        ("meta", {}),
        ("div", {"class": re.compile("post-share-buttons")}),
        ("div", {"id": "comments"}),
        ("div", {"class": re.compile("comment-form")}),
        ("a", {"class": re.compile("jump-link")}),
        ("div", {"class": re.compile("post-footer")}),
        ("div", {"class": re.compile("breadcrumbs")}),
        ("div", {"class": re.compile("singleton-element")}),
    ]

    for tag_name, attrs in elements_to_remove:
        for elem in body_copy.find_all(tag_name, **attrs):
            elem.decompose()

    # Return the inner HTML
    # Get the contents of the root element
    root = body_copy
    inner_html = "".join(str(child) for child in root.children)
    return inner_html


def _extract_canonical_text(body_html: str) -> str:
    """Extract plain text from body HTML."""
    soup = BeautifulSoup(body_html, "html.parser")

    # Remove script and style elements
    for elem in soup(["script", "style"]):
        elem.decompose()

    # Get text
    text = soup.get_text()

    # Clean up whitespace
    lines = (line.strip() for line in text.splitlines())
    chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
    text = " ".join(chunk for chunk in chunks if chunk)

    # Remove any remaining HTML tag artifacts (shouldn't happen, but be safe)
    # Just in case there are < or > characters left, we'll replace them
    # Actually, don't - these might be legitimate in the text (like mathematical notations)
    # The test is checking for HTML tags, not just any < character
    # So we should be OK

    return text


def extract_citations(body_html: str) -> list[Citation]:
    """
    Extract citations from body HTML.

    Looks for a "References" or "Sources" heading, then extracts citations from
    the following <ol> or <ul> list. Falls back to searching for <sup> markers
    if no References section is found.

    Args:
        body_html: Clean body HTML (from isolate_post)

    Returns:
        List of Citation objects sorted by marker
    """
    citations = []
    soup = BeautifulSoup(body_html, "html.parser")

    # ── Strategy 1: Look for References/Sources heading + list ──────────────
    heading = None
    for tag in soup.find_all(["h2", "h3", "h4", "h5", "h6"]):
        text = tag.get_text(strip=True).lower()
        if "reference" in text or "source" in text:
            heading = tag
            break

    if heading:
        # Find the next <ol> or <ul> after the heading
        list_elem = heading.find_next(["ol", "ul"])
        if list_elem:
            marker = 1
            for li in list_elem.find_all("li", recursive=False):
                # Find the first anchor in the list item
                anchor = li.find("a", href=True)
                if anchor:
                    url = anchor.get("href", "")
                    if url.startswith("http"):
                        kind = classify_citation(url)
                        citation = Citation(marker=marker, url=url, kind=kind)
                        citations.append(citation)
                        marker += 1

    # ── Strategy 2: Fall back to <sup> markers if no References section ─────
    if not citations:
        for sup in soup.find_all("sup", class_="hrsu-citation"):
            anchor = sup.find("a")
            if anchor:
                url = anchor.get("href", "")
                marker_text = anchor.get_text(strip=True)

                # Try to extract marker number
                try:
                    marker = int(marker_text)
                except ValueError:
                    continue

                # Classify the URL
                kind = classify_citation(url)

                # Create citation
                citation = Citation(marker=marker, url=url, kind=kind)
                citations.append(citation)

    return citations


def _extract_citations(body_html: str) -> list[Citation]:
    """Extract citations from body HTML (internal helper, delegates to extract_citations)."""
    return extract_citations(body_html)


def _extract_images(body_html: str) -> list[dict]:
    """Extract images from body HTML."""
    images = []
    soup = BeautifulSoup(body_html, "html.parser")

    for img in soup.find_all("img"):
        src = img.get("src", "")
        alt = img.get("alt", "")

        if src:  # Only include if src is present
            images.append({"src": src, "alt": alt})

    return images


# ── Region and Category Lookup ────────────────────────────────────────────
def _region_category_from_history(url: str) -> tuple[str | None, str | None]:
    """
    Lookup region and category for a URL from blog_history.json.

    Searches blog_history.json at the project root for a post with matching URL.
    If found, returns (region, category). Otherwise, returns (None, None).

    Args:
        url: The blog post URL

    Returns:
        Tuple of (region, category) or (None, None) if not found
    """
    try:
        blog_history_path = Path(config.PROJECT_ROOT) / "blog_history.json"
        if not blog_history_path.exists():
            logger.debug(f"blog_history.json not found at {blog_history_path}")
            return None, None

        data = json.loads(blog_history_path.read_text(encoding="utf-8"))
        posts = data.get("posts", [])

        for post in posts:
            if post.get("url") == url:
                region = post.get("region")
                category = post.get("category")
                logger.debug(f"Found post in history: region={region}, category={category}")
                return region, category

        logger.debug(f"URL {url} not found in blog_history.json")
        return None, None

    except Exception as e:
        logger.warning(f"Error reading blog_history.json: {e}")
        return None, None


# ── Stage Runner ──────────────────────────────────────────────────────────
def run(ctx: Any) -> dict[str, str]:
    """
    Run the ingest stage.

    Fetches the blog post (via HTML override or requests.get), isolates it,
    extracts citations, looks up region/category, and saves post.json + canonical.txt.

    Args:
        ctx: StageContext with manifest, workspace, flags

    Returns:
        dict with 'post' and 'canonical' keys pointing to output files

    Raises:
        ValueError: If post isolation fails
        requests.RequestException: If URL fetch fails (and no html_override)
    """
    # ── Get HTML content ───────────────────────────────────────────────────
    html_override = ctx.flags.get("html_override")
    if html_override:
        page_html = html_override
        logger.debug("Using html_override from flags")
    else:
        # Fetch the URL
        url = ctx.manifest.blog_url
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/120.0.0.0 Safari/537.36"
        }
        logger.debug(f"Fetching URL: {url}")
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        page_html = response.text

    # ── Isolate the post ───────────────────────────────────────────────────
    isolated = isolate_post(page_html, ctx.manifest.blog_url)
    logger.debug(f"Isolated post: title={isolated.title}")

    # ── Extract citations ─────────────────────────────────────────────────
    citations = extract_citations(isolated.body_html)
    logger.debug(f"Extracted {len(citations)} citations")

    # ── Lookup region and category from blog_history ─────────────────────
    region, category = _region_category_from_history(ctx.manifest.blog_url)
    logger.debug(f"Region/category from history: region={region}, category={category}")

    # ── Build post.json payload ───────────────────────────────────────────
    post_data = {
        "url": ctx.manifest.blog_url,
        "title": isolated.title,
        "region": region,
        "category": category,
        "citations": [asdict(c) for c in citations],
        "images": isolated.images,
    }

    # ── Write post.json ────────────────────────────────────────────────────
    workspace = Path(ctx.workspace)
    post_file = workspace / "post.json"
    post_file.write_text(json.dumps(post_data, indent=2), encoding="utf-8")
    logger.debug(f"Wrote post.json to {post_file}")

    # ── Write canonical.txt ───────────────────────────────────────────────
    canonical_file = workspace / "canonical.txt"
    canonical_file.write_text(isolated.canonical_text, encoding="utf-8")
    logger.debug(f"Wrote canonical.txt to {canonical_file}")

    # ── Return artifact paths ─────────────────────────────────────────────
    return {
        "post": "post.json",
        "canonical": "canonical.txt",
    }
