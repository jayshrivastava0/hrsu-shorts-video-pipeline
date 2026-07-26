"""
TDD tests for YouTube metadata packager.

Tests verify:
1. Title generation and length constraints
2. Description generation with CTA and hashtags
3. Tag generation
4. Thumbnail extraction and fallback
5. SRT path verification
6. Banned phrase removal
7. Graceful Ollama fallback
"""
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from video_agent.publishers.youtube_packager import (
    package_for_youtube,
    _remove_banned_phrases,
    _generate_title,
    _generate_description,
    _generate_tags,
    _extract_thumbnail_from_video,
    _create_color_fill_thumbnail,
    _get_video_path_from_workspace,
    THUMBNAIL_WIDTH,
    THUMBNAIL_HEIGHT,
)
from video_agent.storyboard import (
    Storyboard,
    HeroClaim,
    Beat,
    Scene,
    VisualConcept,
    Cinematography,
)
from video_agent.harness.manifest import PublishPackage
from video_agent.config import (
    YOUTUBE_CATEGORY_ID,
    YOUTUBE_DEFAULT_PRIVACY,
    YOUTUBE_TITLE_MAX,
    YOUTUBE_DESC_MAX,
    MAIN_WEBSITE,
    SCRIPT_BANNED_PHRASES,
)


# ─── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def make_storyboard():
    """Factory for creating test storyboards."""
    def _make(
        hero_stat="90%",
        hero_claim="Calcium nitrate removes 90% H2S",
        region="australia",
        category="wastewater_treatment",
    ):
        return Storyboard(
            version="2.2",
            blog={
                "id": "test-blog-1",
                "url": "https://example.com/test",
                "title": "Test Blog",
                "region": region,
                "category": category,
                "subcategory": f"{category}_advanced",
                "persona": "procurement",
            },
            hero_claim=HeroClaim(
                stat=hero_stat,
                claim_text=hero_claim,
                source_quote="Test source",
            ),
            arc=[
                Beat(index=0, beat="hook", purpose="hook", duration_target_s=3.5),
            ],
            scenes=[
                Scene(
                    index=0,
                    beat="hook",
                    narration="Are wastewater costs rising?",
                    on_screen_text="90% H2S CUT",
                    visual_concept=VisualConcept(
                        subject="wastewater plant",
                        modifier="aerial",
                        type="photo",
                        mood="problem",
                        style_hint="documentary",
                    ),
                    duration_target_s=3.5,
                    transition_in="cut",
                ),
            ],
        )
    return _make


@pytest.fixture
def blog_record_australia():
    """Typical blog record for Australia region."""
    return {
        "id": "blog-1",
        "region": "australia",
        "category": "wastewater_treatment",
        "subcategory": "h2s_control",
        "title": "H2S Control in Wastewater Treatment",
        "persona": "procurement",
    }


@pytest.fixture
def tmp_workspace(tmp_path):
    """Create a workspace directory with required subdirectories."""
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


# ─── Test: Basic Packaging ─────────────────────────────────────────────────

def test_basic_packaging(make_storyboard, blog_record_australia, tmp_workspace):
    """Good storyboard + blog record → valid PublishPackage."""
    sb = make_storyboard()

    # Create dummy SRT
    srt_path = tmp_workspace / "subtitles.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:05,000\nTest subtitle\n")

    # Create dummy video
    video_path = tmp_workspace / "video.mp4"
    video_path.write_bytes(b"fake video data")

    pkg = package_for_youtube(sb, blog_record_australia, str(tmp_workspace))

    assert isinstance(pkg, PublishPackage)
    assert pkg.title is not None
    assert len(pkg.title) > 0
    assert pkg.description is not None
    assert len(pkg.description) > 0
    assert isinstance(pkg.tags, list)
    assert len(pkg.tags) > 0
    assert pkg.category_id == YOUTUBE_CATEGORY_ID
    assert pkg.privacy_status == YOUTUBE_DEFAULT_PRIVACY
    assert pkg.caption_srt_path is not None


# ─── Test: Title Length Constraint ─────────────────────────────────────────

def test_title_length_constraint(make_storyboard, blog_record_australia, tmp_workspace):
    """Title must always be ≤100 chars."""
    sb = make_storyboard(
        hero_claim="This is an extremely long and detailed hero claim that contains "
                   "many words and should definitely be truncated by the title generator "
                   "to ensure it fits within YouTube's strict 100-character limit"
    )

    srt_path = tmp_workspace / "subtitles.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:05,000\nTest\n")

    pkg = package_for_youtube(sb, blog_record_australia, str(tmp_workspace))

    assert len(pkg.title) <= YOUTUBE_TITLE_MAX
    assert pkg.title.strip()  # Non-empty after constraints


# ─── Test: Banned Phrases Removed ──────────────────────────────────────────

def test_banned_phrases_removed():
    """SCRIPT_BANNED_PHRASES are stripped from output (case-insensitive)."""
    text_with_banned = "As an AI, in this video I'll explain. Thanks for watching!"

    cleaned = _remove_banned_phrases(text_with_banned)

    # None of the banned phrases should be present
    for phrase in SCRIPT_BANNED_PHRASES:
        assert phrase.lower() not in cleaned.lower()

    # Should still have substantive content
    assert len(cleaned) > 0
    assert "explain" in cleaned


# ─── Test: Description Length Constraint ───────────────────────────────────

def test_description_length_constraint(make_storyboard, blog_record_australia, tmp_workspace):
    """Description must always be ≤4900 chars."""
    sb = make_storyboard()

    srt_path = tmp_workspace / "subtitles.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:05,000\nTest\n")

    pkg = package_for_youtube(sb, blog_record_australia, str(tmp_workspace))

    assert len(pkg.description) <= YOUTUBE_DESC_MAX
    assert pkg.description.strip()  # Non-empty


# ─── Test: CTA Link Present ────────────────────────────────────────────────

def test_cta_link_present(make_storyboard, blog_record_australia, tmp_workspace):
    """Description must contain hrsuindore.com CTA link."""
    sb = make_storyboard()

    srt_path = tmp_workspace / "subtitles.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:05,000\nTest\n")

    pkg = package_for_youtube(sb, blog_record_australia, str(tmp_workspace))

    # Check for main website link
    assert MAIN_WEBSITE in pkg.description or "hrsuindore.com" in pkg.description


# ─── Test: Hashtags Present ────────────────────────────────────────────────

def test_hashtags_present(make_storyboard, blog_record_australia, tmp_workspace):
    """Description must contain #Shorts and region/use-case tags."""
    sb = make_storyboard()

    srt_path = tmp_workspace / "subtitles.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:05,000\nTest\n")

    pkg = package_for_youtube(sb, blog_record_australia, str(tmp_workspace))

    # Must have #Shorts
    assert "#Shorts" in pkg.description

    # Should have region or category hashtags
    desc_lower = pkg.description.lower()
    assert "#" in pkg.description  # At least some hashtags


# ─── Test: Tags Count ──────────────────────────────────────────────────────

def test_tags_count(make_storyboard, blog_record_australia, tmp_workspace):
    """Maximum 5 tags."""
    sb = make_storyboard()

    srt_path = tmp_workspace / "subtitles.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:05,000\nTest\n")

    pkg = package_for_youtube(sb, blog_record_australia, str(tmp_workspace))

    assert isinstance(pkg.tags, list)
    assert len(pkg.tags) <= 5
    assert len(pkg.tags) > 0


# ─── Test: Thumbnail Creation ──────────────────────────────────────────────

def test_thumbnail_creation_color_fill(tmp_workspace):
    """Color-fill thumbnail created at correct dimensions."""
    thumbnail_path = _create_color_fill_thumbnail(tmp_workspace / "thumbnail.jpg")

    assert thumbnail_path is not None
    assert thumbnail_path.exists()

    # Verify dimensions using PIL if available
    try:
        from PIL import Image
        img = Image.open(thumbnail_path)
        assert img.size == (THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT)
    except ImportError:
        # PIL not available, just verify file exists
        assert thumbnail_path.stat().st_size > 0


# ─── Test: SRT Path Verification ───────────────────────────────────────────

def test_srt_path_verification(make_storyboard, blog_record_australia, tmp_workspace):
    """Caption SRT path is recorded (exists or not)."""
    sb = make_storyboard()

    # Create valid SRT
    srt_path = tmp_workspace / "subtitles.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:05,000\nTest subtitle\n")

    pkg = package_for_youtube(sb, blog_record_australia, str(tmp_workspace))

    assert pkg.caption_srt_path is not None
    assert "subtitles.srt" in pkg.caption_srt_path


# ─── Test: Privacy Status Unlisted ─────────────────────────────────────────

def test_privacy_status_unlisted(make_storyboard, blog_record_australia, tmp_workspace):
    """Privacy status is 'unlisted' for Phase 1."""
    sb = make_storyboard()

    srt_path = tmp_workspace / "subtitles.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:05,000\nTest\n")

    pkg = package_for_youtube(sb, blog_record_australia, str(tmp_workspace))

    assert pkg.privacy_status == YOUTUBE_DEFAULT_PRIVACY
    assert pkg.privacy_status == "unlisted"


# ─── Test: Ollama Unavailable Fallback ─────────────────────────────────────

@patch("video_agent.publishers.youtube_packager.OllamaClient")
def test_ollama_unavailable_fallback(mock_ollama, make_storyboard, blog_record_australia, tmp_workspace):
    """Description generated even if Ollama unavailable."""
    sb = make_storyboard()

    srt_path = tmp_workspace / "subtitles.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:05,000\nTest\n")

    # Mock Ollama to raise error
    mock_client = MagicMock()
    mock_client.generate.side_effect = Exception("Ollama unavailable")
    mock_ollama.return_value = mock_client

    # Should not raise; should use template fallback
    pkg = package_for_youtube(sb, blog_record_australia, str(tmp_workspace))

    assert pkg.description is not None
    assert len(pkg.description) > 0
    assert MAIN_WEBSITE in pkg.description or "hrsuindore.com" in pkg.description


# ─── Test: Missing Blog Metadata ───────────────────────────────────────────

def test_missing_blog_metadata(make_storyboard, tmp_workspace):
    """Uses defaults for missing region/category."""
    sb = make_storyboard()

    # Minimal blog record
    blog_record = {}

    srt_path = tmp_workspace / "subtitles.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:05,000\nTest\n")

    pkg = package_for_youtube(sb, blog_record, str(tmp_workspace))

    # Should still produce valid package
    assert pkg.title is not None
    assert len(pkg.title) <= YOUTUBE_TITLE_MAX
    assert pkg.description is not None
    assert len(pkg.description) <= YOUTUBE_DESC_MAX


# ─── Test: Unicode in Tags ─────────────────────────────────────────────────

def test_unicode_in_tags():
    """Tags with unicode handled safely."""
    tags = _generate_tags(
        region="australia",
        category="wastewater_treatment",
        subcategory="h₂s_control",
    )

    assert isinstance(tags, list)
    assert len(tags) <= 5
    # All tags should be strings
    for tag in tags:
        assert isinstance(tag, str)
        assert len(tag) > 0


# ─── Test: Empty Hero Claim ────────────────────────────────────────────────

def test_empty_hero_claim(make_storyboard, blog_record_australia, tmp_workspace):
    """Graceful fallback when hero_claim is None."""
    sb = make_storyboard()
    sb.hero_claim = None

    srt_path = tmp_workspace / "subtitles.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:05,000\nTest\n")

    # Should not raise; should use template fallback
    pkg = package_for_youtube(sb, blog_record_australia, str(tmp_workspace))

    assert pkg.title is not None
    assert len(pkg.title) <= YOUTUBE_TITLE_MAX
    assert pkg.description is not None


# ─── Test: Category ID Set Correctly ────────────────────────────────────────

def test_category_id_set_correctly(make_storyboard, blog_record_australia, tmp_workspace):
    """Category ID is always 28 (Science & Technology)."""
    sb = make_storyboard()

    srt_path = tmp_workspace / "subtitles.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:05,000\nTest\n")

    pkg = package_for_youtube(sb, blog_record_australia, str(tmp_workspace))

    assert pkg.category_id == "28"
    assert pkg.category_id == YOUTUBE_CATEGORY_ID


# ─── Test: Title Format (keyword-front-loaded) ─────────────────────────────

@patch("video_agent.publishers.youtube_packager.OllamaClient")
def test_title_keyword_front_loaded(mock_ollama, make_storyboard, blog_record_australia, tmp_workspace):
    """Title has region keyword and category keyword at front (template version without Ollama)."""
    # Mock Ollama to use fallback
    mock_client = MagicMock()
    mock_client.generate.side_effect = Exception("Ollama unavailable")
    mock_ollama.return_value = mock_client

    sb = make_storyboard(category="wastewater_treatment", region="australia")

    srt_path = tmp_workspace / "subtitles.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:05,000\nTest\n")

    pkg = package_for_youtube(sb, blog_record_australia, str(tmp_workspace))

    # Title should reference region keyword at front
    title_lower = pkg.title.lower()
    assert "australia" in title_lower
    # And should include category or use-case keywords
    assert "wastewater" in title_lower or "treatment" in title_lower or "h2s" in title_lower


# ─── Test: Missing SRT File ────────────────────────────────────────────────

def test_missing_srt_file(make_storyboard, blog_record_australia, tmp_workspace):
    """Video still uploads even if SRT missing (path still recorded)."""
    sb = make_storyboard()

    # Don't create SRT file

    pkg = package_for_youtube(sb, blog_record_australia, str(tmp_workspace))

    # SRT path should still be recorded (even if file doesn't exist)
    assert pkg.caption_srt_path is not None
    assert "subtitles.srt" in pkg.caption_srt_path


# ─── Test: Banned Phrases Case Insensitive ─────────────────────────────────

def test_banned_phrases_case_insensitive():
    """Banned phrase removal is case-insensitive."""
    text = "As An AI, this video will explain things. Thanks FOR WATCHING!"

    cleaned = _remove_banned_phrases(text)

    # Should remove "As An AI" (mixed case)
    assert "as an ai" not in cleaned.lower()
    # Should remove "Thanks FOR WATCHING" (uppercase)
    assert "thanks for watching" not in cleaned.lower()
    assert "explain" in cleaned.lower()


# ─── Test: Get Video Path From Workspace ────────────────────────────────────

def test_get_video_path_from_workspace(tmp_workspace):
    """Finds video in workspace by standard names."""
    # Create video with standard name
    video_path = tmp_workspace / "_with_subs.mp4"
    video_path.write_bytes(b"fake")

    found_path = _get_video_path_from_workspace(tmp_workspace)

    assert found_path is not None
    assert found_path.name == "_with_subs.mp4"


def test_get_video_path_missing(tmp_workspace):
    """Returns None if no video found."""
    found_path = _get_video_path_from_workspace(tmp_workspace)

    assert found_path is None


# ─── Test: Generate Title Template ────────────────────────────────────────

def test_generate_title_template():
    """Title generation without Ollama uses template."""
    title = _generate_title(
        region="australia",
        category="wastewater_treatment",
        hero_claim="Calcium nitrate removes 90% H2S",
        use_ollama=False,
    )

    assert len(title) <= YOUTUBE_TITLE_MAX
    assert len(title) > 0
    # Should contain category or region keywords
    title_lower = title.lower()
    assert "wastewater" in title_lower or "treatment" in title_lower or "chemistry" in title_lower


# ─── Test: Generate Description Template ────────────────────────────────────

def test_generate_description_template():
    """Description generation without Ollama uses template."""
    desc = _generate_description(
        hero_claim_stat="90%",
        hero_claim_text="Calcium nitrate removes 90% H2S",
        region="australia",
        category="wastewater_treatment",
        use_ollama=False,
    )

    assert len(desc) <= YOUTUBE_DESC_MAX
    assert MAIN_WEBSITE in desc
    assert "#Shorts" in desc
    assert "90%" in desc  # Should include stat


# ─── Test: Generate Tags Comprehensive ─────────────────────────────────────

def test_generate_tags_comprehensive():
    """Tags include region, category, subcategory, ingredient, chemistry."""
    tags = _generate_tags(
        region="australia",
        category="wastewater_treatment",
        subcategory="h2s_control",
    )

    assert len(tags) <= 5
    # Should have multiple tag types
    assert "australia" in tags
    assert "calcium nitrate" in tags
    assert "chemistry" in tags


# ─── Test: Brand Card Thumbnail Fallback ─────────────────────────────────────

@patch("video_agent.publishers.youtube_packager._create_brand_card_thumbnail")
def test_brand_card_fallback_attempted(mock_brand_card, make_storyboard, blog_record_australia, tmp_workspace):
    """When no video found, brand card thumbnail composition is attempted."""
    sb = make_storyboard()

    srt_path = tmp_workspace / "subtitles.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:05,000\nTest\n")

    # Mock brand card to return None (graceful fallback)
    mock_brand_card.return_value = None

    pkg = package_for_youtube(sb, blog_record_australia, str(tmp_workspace))

    # Brand card should have been attempted
    assert mock_brand_card.called
    # Thumbnail should still be set (fell back to color-fill)
    assert pkg.thumbnail_path is not None
    assert pkg.thumbnail_path.endswith(".jpg")


# ─── Test: Package Thumbnail Path Set ───────────────────────────────────────

def test_package_thumbnail_path_set(make_storyboard, blog_record_australia, tmp_workspace):
    """PublishPackage has thumbnail_path set."""
    sb = make_storyboard()

    srt_path = tmp_workspace / "subtitles.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:05,000\nTest\n")

    pkg = package_for_youtube(sb, blog_record_australia, str(tmp_workspace))

    # Thumbnail should be set (either from video or color-fill)
    assert pkg.thumbnail_path is not None
    # Should be absolute path to JPEG
    assert pkg.thumbnail_path.endswith(".jpg")
