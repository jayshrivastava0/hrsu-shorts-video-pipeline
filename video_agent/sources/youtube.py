"""YouTube search via yt-dlp. Returns metadata only — actual download
happens in the Sourcer's download phase to keep this fast.

Filters out talking-head / music-video / movie / news / vlog content at
search time so it never enters the candidate pool. Industrial B-roll only.
"""
from __future__ import annotations
import logging
import re
from video_agent.sources.base import BaseSource, RawCandidate

log = logging.getLogger(__name__)

try:
    import yt_dlp                    # noqa: F401
except ImportError:                  # pragma: no cover
    yt_dlp = None


_MIN_DURATION_S = 30
_MAX_DURATION_S = 1200          # >20min is almost certainly not B-roll
_MIN_VIEWS = 10_000

# Title-level junk patterns (case-insensitive). One match ⇒ reject.
# Covers: movies, songs, interviews, vlogs, news, reaction videos,
# tutorials/lectures, music videos, and any non-English regional content.
_REJECT_TITLE = re.compile(
    r"\b("
    # Movies / shows / songs
    r"movie|film|trailer|episode|season|series|drama|comedy|"
    r"song|songs|music\s*video|lyric|lyrics|cover|remix|dance|mashup|"
    # Talking head / personality content
    r"interview|podcast|vlog|reaction|reacts?|review|"
    r"talks?|talking|q\s*&\s*a|q\s*and\s*a|"
    # Tutorials / lectures (person on camera explaining)
    r"tutorial|how\s*to|lesson|class|lecture|crash\s*course|"
    r"explained\s+by|live\s+stream|livestream|"
    # News / current affairs
    r"news|breaking|bulletin|press\s*conference|debate|"
    # Regional / language indicators (when a video is in a non-English
    # cinematic/musical context, the language name is usually in the title)
    r"hindi|bollywood|nepali|garhwali|kumaoni|bhojpuri|punjabi|tamil|"
    r"telugu|malayalam|kannada|marathi|gujarati|bengali|urdu|"
    # Channel signatures common on irrelevant uploads
    r"shorts?|prank|funny|whatsapp\s*status|status\s*video"
    r")\b",
    re.IGNORECASE,
)

# Devanagari / Tamil / other non-Latin script detection: if >15% of the
# title's letter chars are non-Latin, this is almost certainly content
# in another language → reject.
_NON_LATIN = re.compile(r"[ऀ-ॿঀ-৿਀-੿"
                        r"஀-௿ఀ-౿ഀ-ൿ]")
_LATIN_LETTER = re.compile(r"[A-Za-z]")


def _is_junk_title(title: str) -> tuple[bool, str]:
    """Return (rejected, reason). Empty reason ⇒ not rejected."""
    if not title:
        return False, ""
    if _REJECT_TITLE.search(title):
        m = _REJECT_TITLE.search(title)
        return True, f"junk_keyword:{m.group(1).lower()}"
    non_latin = len(_NON_LATIN.findall(title))
    latin = len(_LATIN_LETTER.findall(title))
    if non_latin > 0 and non_latin > latin * 0.15:
        return True, "non_latin_script"
    return False, ""


class YouTubeSource(BaseSource):
    name = "youtube"
    authority_weight = 5

    def search(self, query: str, limit: int = 3) -> list[RawCandidate]:
        if yt_dlp is None:
            log.warning("yt-dlp not installed; YouTube source disabled")
            return []
        # Pull more than `limit` because we will filter aggressively below.
        search_count = max(limit * 4, 12)
        opts = {
            "quiet": True, "skip_download": True, "extract_flat": False,
            "no_warnings": True, "default_search": f"ytsearch{search_count}",
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(query, download=False)
        except Exception as e:
            log.warning("YouTube search failed for %r: %s", query, e)
            return []

        entries = info.get("entries") or [info]
        out = []
        rejected = 0
        for e in entries:
            if not e:
                continue
            duration = e.get("duration") or 0
            views = e.get("view_count") or 0
            if duration < _MIN_DURATION_S or duration > _MAX_DURATION_S:
                rejected += 1
                continue
            if views < _MIN_VIEWS:
                rejected += 1
                continue
            title = e.get("title", "")
            description = (e.get("description", "") or "")[:300]
            channel = e.get("channel", "") or e.get("uploader", "")

            # Reject by title + channel patterns
            bad, reason = _is_junk_title(title)
            if not bad:
                # Also test channel name — channels named after languages or
                # film/song genres tend to ONLY publish that kind of content.
                bad, reason = _is_junk_title(channel)
            if bad:
                log.debug("YouTube reject %r (channel=%r, reason=%s)",
                          title[:60], channel[:30], reason)
                rejected += 1
                continue

            vid = e.get("id", "")
            # Caption = title + short description so the downstream
            # context-matcher has more to work with.
            caption = f"{title} — {description}".strip(" —")
            out.append(RawCandidate(
                source=self.name,
                url=f"https://www.youtube.com/watch?v={vid}",
                caption=caption,
                width=1920, height=1080,
                is_clip=True, duration_s=float(duration),
                extra={"video_id": vid, "view_count": views,
                       "title": title, "channel": channel,
                       "thumbnail": e.get("thumbnail", "")},
            ))
            if len(out) >= limit:
                break

        if rejected:
            log.info("YouTube: kept %d, rejected %d junk/non-English results for %r",
                     len(out), rejected, query[:60])
        return out
