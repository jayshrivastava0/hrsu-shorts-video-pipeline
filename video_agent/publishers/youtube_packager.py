"""
YouTube metadata packager for video harness.

Builds PublishPackage from Storyboard + blog_record with:
- SEO-optimized title (≤100 chars)
- Engaging description with CTA (≤4900 chars)
- Regional/use-case tags
- Thumbnail extraction from video or color-fill fallback
- Caption SRT path verification
- Privacy status management
"""
import logging
import re
import subprocess
from pathlib import Path
from typing import Optional

from video_agent.config import (
    SCRIPT_BANNED_PHRASES,
    YOUTUBE_CATEGORY_ID,
    YOUTUBE_DEFAULT_PRIVACY,
    YOUTUBE_TITLE_MAX,
    YOUTUBE_DESC_MAX,
    MAIN_WEBSITE,
    BRAND_LOGO_PATH,
)
from video_agent.harness.manifest import PublishPackage
from video_agent.ollama_client import OllamaClient, OllamaError

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from video_agent.storyboard import Storyboard

log = logging.getLogger(__name__)

# Standard thumbnail dimensions for YouTube
THUMBNAIL_WIDTH = 1280
THUMBNAIL_HEIGHT = 720


def _remove_banned_phrases(text: str) -> str:
    """
    Remove banned AI-artifact phrases from text (case-insensitive).
    Collapses extra whitespace after removal.
    """
    result = text
    for phrase in SCRIPT_BANNED_PHRASES:
        # Case-insensitive replacement
        pattern = re.compile(re.escape(phrase), re.IGNORECASE)
        result = pattern.sub(" ", result)
    # Collapse multiple spaces
    result = re.sub(r"\s+", " ", result).strip()
    return result


def _generate_title(
    region: str,
    category: str,
    hero_claim: Optional[str],
    use_ollama: bool = True,
) -> str:
    """
    Generate YouTube Short title (≤100 chars).
    Format: [region + category keyword] - [hero claim hook]

    Args:
        region: Blog region (e.g., "australia")
        category: Use-case category (e.g., "wastewater_treatment")
        hero_claim: Hero claim text (optional)
        use_ollama: Whether to attempt LLM generation

    Returns:
        Title meeting all constraints (≤100 chars, no banned phrases)
    """
    # Build template-based fallback first
    region_word = region.capitalize() if region else ""
    category_words = category.replace("_", " ").title() if category else ""

    # Hero claim hook: first substantive phrase from hero_claim
    hero_hook = ""
    if hero_claim:
        # Extract first 10 words or until punctuation
        words = hero_claim.split()[:10]
        hero_hook = " ".join(words).strip(".,!?")

    if region_word and category_words and hero_hook:
        template = f"{region_word} {category_words}: {hero_hook}"
    elif category_words and hero_hook:
        template = f"{category_words}: {hero_hook}"
    elif hero_hook:
        template = hero_hook
    else:
        template = "Technical Deep Dive"

    # Try LLM if available
    if use_ollama:
        try:
            client = OllamaClient()
            prompt = (
                f"Suggest a compelling YouTube Short title (≤100 chars) for a "
                f"technical video about {hero_claim or 'chemistry'}. "
                f"Format: [keyword] - [hook phrase]. "
                f"Region: {region}. Category: {category}. "
                f"Respond with ONLY the title, no explanation."
            )
            title = client.generate(prompt, system="You are a technical copywriter.")
            title = title.strip('"\'')  # Remove quotes if present
        except (OllamaError, Exception) as e:
            log.warning("Ollama title generation failed: %s, using template", e)
            title = template
    else:
        title = template

    # Enforce constraints
    # 1. Truncate to max length
    title = title[:YOUTUBE_TITLE_MAX]

    # 2. Remove banned phrases
    title = _remove_banned_phrases(title)

    # 3. Ensure it's not empty
    if not title:
        title = "Technical Deep Dive"

    return title


def _generate_description(
    hero_claim_stat: Optional[str],
    hero_claim_text: Optional[str],
    region: str,
    category: str,
    use_ollama: bool = True,
) -> str:
    """
    Generate YouTube Short description (≤4900 chars).
    Format:
    - Line 1: Hero stat + short hook (20-30 words)
    - Line 2: Video summary (1-2 lines)
    - Line 3: CTA to https://hrsuindore.com/
    - Line 4: Hashtags (#Shorts, region, use-case)

    Args:
        hero_claim_stat: Numeric stat from hero claim
        hero_claim_text: Text of hero claim
        region: Blog region
        category: Use-case category
        use_ollama: Whether to attempt LLM generation

    Returns:
        Description meeting all constraints (≤4900 chars, contains CTA + hashtags)
    """
    # Build template-based fallback first
    stat_line = ""
    if hero_claim_stat:
        stat_line = f"Key insight: {hero_claim_stat}\n"

    claim_line = ""
    if hero_claim_text:
        # Truncate to ~50 chars for 1-line summary
        claim_line = hero_claim_text[:80].strip() + "\n"

    cta_line = f"\nLearn more: {MAIN_WEBSITE}/\n"

    # Build hashtags
    region_tag = f"#{region.lower().replace('_', '')}"
    category_tag = f"#{category.lower().replace('_', '')}"
    hashtag_line = f"#Shorts #ChemicalEngineering {region_tag} {category_tag}"

    template = stat_line + claim_line + cta_line + hashtag_line

    # Try LLM if available for better summary
    if use_ollama:
        try:
            client = OllamaClient()
            prompt = (
                f"Write a 2-line YouTube Short description for a technical video "
                f"about: {hero_claim_text or 'chemistry'}. "
                f"Include a key insight and call-to-action. "
                f"Keep it under 200 chars. "
                f"Respond with ONLY the description, no explanation."
            )
            summary = client.generate(prompt, system="You are a technical copywriter.")
            summary = summary.strip('"\'')
            # Use LLM summary if it's reasonable length
            if len(summary) < 200:
                template = (
                    f"{stat_line}{summary}\n{cta_line}{hashtag_line}"
                )
        except (OllamaError, Exception) as e:
            log.warning("Ollama description generation failed: %s, using template", e)

    description = template

    # Enforce constraints
    # 1. Truncate to max length
    description = description[:YOUTUBE_DESC_MAX]

    # 2. Remove banned phrases
    description = _remove_banned_phrases(description)

    # 3. Verify CTA link is present (re-add if removed by banned-phrase cleanup)
    if MAIN_WEBSITE not in description:
        # Append CTA to the end
        cta = f"\nLearn more: {MAIN_WEBSITE}/"
        if len(description) + len(cta) <= YOUTUBE_DESC_MAX:
            description = description + cta

    # 4. Verify hashtags
    if "#Shorts" not in description:
        hashtags = "\n\n#Shorts #ChemicalEngineering"
        if len(description) + len(hashtags) <= YOUTUBE_DESC_MAX:
            description = description + hashtags

    return description


def _generate_tags(region: str, category: str, subcategory: Optional[str] = None) -> list[str]:
    """
    Generate YouTube tags (max 5).
    Regional + use-case tags + ingredient tag.

    Args:
        region: Blog region
        category: Use-case category
        subcategory: Optional subcategory for specificity

    Returns:
        List of tags (max 5)
    """
    tags = []

    # Regional tag
    if region:
        tags.append(region.lower())

    # Category tag (primary use-case)
    if category:
        cat = category.replace("_", " ").lower()
        tags.append(cat)

    # Subcategory tag (if different from category)
    if subcategory and subcategory != category:
        subcat = subcategory.replace("_", " ").lower()
        tags.append(subcat)

    # Ingredient tag
    tags.append("calcium nitrate")

    # Chemistry/industrial tag
    tags.append("chemistry")

    # Truncate to max 5
    tags = tags[:5]

    # Sanitize: lowercase, collapse whitespace
    tags = [re.sub(r"\s+", " ", tag.strip().lower()) for tag in tags]

    return tags


def _extract_thumbnail_from_video(video_path: Path, workspace: Path) -> Optional[Path]:
    """
    Extract thumbnail from video at 25% mark.
    Falls back to color-filled image if extraction fails.

    Args:
        video_path: Path to video file
        workspace: Workspace directory for output

    Returns:
        Path to thumbnail file (1280x720), or None if all methods fail
    """
    thumbnail_path = workspace / "thumbnail.jpg"

    # If video doesn't exist, skip to fallback
    if not video_path.exists():
        log.warning("Video not found at %s, using color-fill fallback", video_path)
        return _create_color_fill_thumbnail(thumbnail_path)

    # Try to extract frame at 25% mark
    try:
        # Probe video duration first
        probe_cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1:nokey=1",
            str(video_path),
        ]
        result = subprocess.run(
            probe_cmd, capture_output=True, text=True, timeout=5, check=True
        )
        duration = float(result.stdout.strip())
        seek_time = duration * 0.25  # 25% mark

        # Extract frame
        ffmpeg_cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-ss", str(seek_time),
            "-i", str(video_path),
            "-vframes", "1",
            "-s", f"{THUMBNAIL_WIDTH}x{THUMBNAIL_HEIGHT}",
            str(thumbnail_path),
        ]
        subprocess.run(ffmpeg_cmd, check=True, timeout=10)

        log.info("Extracted thumbnail from video at %.2fs", seek_time)
        return thumbnail_path

    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            ValueError, FileNotFoundError) as e:
        log.warning("Thumbnail extraction failed: %s, trying brand card fallback", e)
        # Try brand card first
        brand_result = _create_brand_card_thumbnail(thumbnail_path)
        if brand_result:
            return brand_result
        # Fall back to solid color if brand card fails
        return _create_color_fill_thumbnail(thumbnail_path)


def _create_brand_card_thumbnail(thumbnail_path: Path) -> Optional[Path]:
    """
    Create a branded thumbnail (1280x720) from the brand outro card.
    Falls back to None if brand card composition fails.

    Process:
    1. Compose brand still (1080x1920 PNG with logo + URL wordmark)
    2. Convert to RGB (JPEG format)
    3. Letterbox into 1280x720 landscape (center-aligned on brand navy background)
    4. Save as JPEG

    Args:
        thumbnail_path: Output path for thumbnail (will save as JPEG)

    Returns:
        Path to created thumbnail, or None if composition fails
    """
    try:
        from video_agent.visual_engine.brand_outro_card import _compose_brand_still
        from PIL import Image

        # Verify logo exists
        logo_path = Path(BRAND_LOGO_PATH)
        if not logo_path.exists():
            log.warning("Brand logo not found at %s, skipping brand card thumbnail", logo_path)
            return None

        # Compose brand still (1080x1920 PNG)
        temp_png = thumbnail_path.parent / "temp_brand_still.png"
        _compose_brand_still(logo_path, "hrsuindore.com", temp_png)

        # Convert from 1080x1920 portrait to 1280x720 landscape
        # Letterbox onto navy background (center the portrait content)
        brand_navy = "#0a1428"
        bg = Image.new("RGB", (THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT), brand_navy)

        # Open portrait image and convert to RGB
        portrait = Image.open(temp_png).convert("RGB")

        # Scale portrait to fit width-wise (preserving aspect ratio)
        # Portrait is 1080×1920 (9:16), target is 1280×720 (16:9)
        # Scale to width: portrait_w / THUMBNAIL_WIDTH * (9/16) / (16/9) = 0.39375
        # So scaled width ≈ 500px, height ≈ 889px (within 720px height)
        scale_factor = THUMBNAIL_HEIGHT / portrait.height  # ≈ 0.375
        new_w = int(portrait.width * scale_factor)
        new_h = THUMBNAIL_HEIGHT
        portrait_scaled = portrait.resize((new_w, new_h), Image.LANCZOS)

        # Center horizontally on the landscape canvas
        x_offset = (THUMBNAIL_WIDTH - new_w) // 2
        y_offset = 0
        bg.paste(portrait_scaled, (x_offset, y_offset))

        # Save as JPEG
        bg.save(thumbnail_path, "JPEG")
        log.info("Created brand card thumbnail at %s (1280x720 JPEG)", thumbnail_path)

        # Clean up temp PNG
        temp_png.unlink(missing_ok=True)
        return thumbnail_path

    except Exception as e:
        log.warning("Brand card thumbnail composition failed: %s, will try color fallback", e)
        return None


def _create_color_fill_thumbnail(thumbnail_path: Path) -> Path:
    """
    Create a solid-color thumbnail (1280x720) using ffmpeg.
    Uses a neutral dark blue (brand color-inspired).

    Args:
        thumbnail_path: Output path for thumbnail

    Returns:
        Path to created thumbnail
    """
    try:
        # Use ffmpeg's lavfi color source to create a solid color image
        # Brand dark navy: #0a192f
        ffmpeg_cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=0x0a192f:s={THUMBNAIL_WIDTH}x{THUMBNAIL_HEIGHT}:d=1",
            "-frames:v", "1",
            str(thumbnail_path),
        ]
        subprocess.run(ffmpeg_cmd, check=True, timeout=10)
        log.info("Created solid-color thumbnail at %s", thumbnail_path)
        return thumbnail_path
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.error("Failed to create color-fill thumbnail: %s", e)
        # Last resort: try PIL
        try:
            from PIL import Image
            img = Image.new("RGB", (THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT), color=(10, 25, 47))
            img.save(thumbnail_path, "JPEG")
            log.info("Created PIL-based placeholder thumbnail at %s", thumbnail_path)
            return thumbnail_path
        except ImportError:
            log.error("Pillow not available for fallback thumbnail creation")
            return None


def _get_video_path_from_workspace(workspace: Path) -> Optional[Path]:
    """
    Infer video path from workspace.
    Looks for standard output names from compose_short_v2.
    """
    workspace = Path(workspace)
    # Common video output names from composer
    candidates = [
        workspace / "video.mp4",
        workspace / "short.mp4",
        workspace / "_with_subs.mp4",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def package_for_youtube(
    storyboard: "Storyboard",
    blog_record: dict,
    workspace: str,
) -> PublishPackage:
    """
    Build PublishPackage from storyboard for YouTube upload.

    Returns PublishPackage with:
    - title (≤100 chars, keyword-front-loaded)
    - description (hero claim + summary + CTA + hashtags)
    - tags (regional/use-case)
    - category_id (28 = Science & Technology)
    - thumbnail_path (hero frame from video or color-fill)
    - caption_srt_path (subtitles.srt)
    - privacy_status ("unlisted" in Phase 1)

    Args:
        storyboard: Storyboard with hero_claim and scenes
        blog_record: Blog record with region, category, subcategory
        workspace: Workspace directory path

    Returns:
        PublishPackage ready for YouTube upload

    Raises:
        ValueError: If critical fields are missing or invalid
    """
    workspace = Path(workspace)

    # Extract metadata from storyboard and blog record
    hero_claim = storyboard.hero_claim
    hero_stat = hero_claim.stat if hero_claim else None
    hero_text = hero_claim.claim_text if hero_claim else None

    region = blog_record.get("region", "default")
    category = blog_record.get("category", "specialty_applications")
    subcategory = blog_record.get("subcategory")

    # Generate title
    title = _generate_title(
        region=region,
        category=category,
        hero_claim=hero_text,
        use_ollama=True,
    )

    # Generate description
    description = _generate_description(
        hero_claim_stat=hero_stat,
        hero_claim_text=hero_text,
        region=region,
        category=category,
        use_ollama=True,
    )

    # Generate tags
    tags = _generate_tags(
        region=region,
        category=category,
        subcategory=subcategory,
    )

    # Extract or create thumbnail
    video_path = _get_video_path_from_workspace(workspace)
    if video_path:
        thumbnail_path = _extract_thumbnail_from_video(video_path, workspace)
    else:
        log.warning("No video found in workspace, trying brand card thumbnail")
        # Try brand card first
        thumbnail_path = _create_brand_card_thumbnail(workspace / "thumbnail.jpg")
        if not thumbnail_path:
            # Fall back to solid color if brand card fails
            thumbnail_path = _create_color_fill_thumbnail(workspace / "thumbnail.jpg")

    # Verify SRT exists
    srt_path = workspace / "subtitles.srt"
    if not srt_path.exists():
        log.warning("Caption SRT not found at %s", srt_path)
        caption_srt_path = str(srt_path)  # Still record path; YouTube can use auto-captions
    else:
        caption_srt_path = str(srt_path)

    # Build package
    package = PublishPackage(
        title=title,
        description=description,
        tags=tags,
        category_id=YOUTUBE_CATEGORY_ID,
        thumbnail_path=str(thumbnail_path) if thumbnail_path else None,
        caption_srt_path=caption_srt_path,
        privacy_status=YOUTUBE_DEFAULT_PRIVACY,
    )

    log.info(
        "Packaged for YouTube: title=%s, tags=%s, privacy=%s",
        package.title[:50], package.tags, package.privacy_status
    )

    return package
