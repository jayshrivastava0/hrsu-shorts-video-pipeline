"""Stage 9 — PACKAGE: YouTube metadata via the reused packager + the
linkedin caption file + an SRT for caption upload."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

from shorts_engine import config
from shorts_engine.cards.encoder import probe_duration
from shorts_engine.errors import EngineError

logger = logging.getLogger(__name__)


def format_label(duration_s: float | None) -> str:
    """'short' (<= config.SHORT_FORM_MAX_S), 'long', or 'unknown' when duration is not known."""
    if duration_s is None:
        return "unknown"
    return "short" if duration_s <= config.SHORT_FORM_MAX_S else "long"


def _package_for_youtube(sb, blog_record: dict, workspace: str):
    from video_agent.publishers.youtube_packager import package_for_youtube
    return package_for_youtube(sb, blog_record, workspace)


def _key_points(ws: Path, factsheet: dict) -> list[str]:
    """Up to 3 lines for the published "Key insight" stat and LinkedIn bullets.

    With explanation_plan.json present, ONLY a verified plan's supported, non-illustrative
    claim texts (in chain order) are used -- never the factsheet's unverified
    claim_summary paraphrases; a plan that is not verified yields nothing. The factsheet
    fallback is kept only for legacy runs that have no plan file at all."""
    plan_path = ws / "explanation_plan.json"
    if plan_path.exists():
        from shorts_engine.explanation import supported_claims
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        if plan.get("status") != "verified":
            return []
        return [c["text"] for s in plan.get("steps") or [] for c in supported_claims(s)
                if c.get("kind") != "illustrative" and c.get("text")][:3]
    top = sorted(factsheet.get("facts", []),
                 key=lambda f: -int(f.get("procurement_significance", 0)))[:3]
    return [f["claim_summary"] for f in top]


def _words_to_srt(words: list[dict], out_path: Path) -> Path:
    from shorts_engine.stages.assemble import group_words_into_cues
    def ts(s: float) -> str:
        ms = int(round(s * 1000))
        hh, rem = divmod(ms, 3_600_000)
        mm, rem = divmod(rem, 60_000)
        ss, mss = divmod(rem, 1000)
        return f"{hh:02d}:{mm:02d}:{ss:02d},{mss:03d}"
    lines = []
    for i, cue in enumerate(group_words_into_cues(words), start=1):
        lines.append(f"{i}\n{ts(cue['start'])} --> {ts(cue['end'])}\n{cue['text']}\n")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def run(ctx) -> dict[str, str]:
    ws = Path(ctx.workspace)
    post = json.loads((ws / "post.json").read_text(encoding="utf-8"))
    script = json.loads((ws / "script.json").read_text(encoding="utf-8"))
    factsheet = json.loads((ws / "factsheet.json").read_text(encoding="utf-8"))
    words = json.loads((ws / "word_timings.json").read_text(encoding="utf-8"))

    hook = next(b for b in script["beats"] if b["beat"] == "hook")
    hero_claim_text = hook.get("card_text") or hook["narration"]
    blog_url = getattr(ctx.manifest, "blog_url", "")
    blog_record = {"region": post.get("region"), "category": post.get("category"),
                   "subcategory": post.get("subcategory"),
                   "title": post.get("title"), "url": blog_url}

    key_points = _key_points(ws, factsheet)
    from video_agent.storyboard import HeroClaim
    hero_claim = HeroClaim(stat=key_points[0] if key_points else "",
                          claim_text=hero_claim_text)

    _words_to_srt(words, ws / "subtitles.srt")
    pkg = _package_for_youtube(SimpleNamespace(hero_claim=hero_claim),
                               blog_record, str(ws))
    video = ws / "video_short.mp4"
    try:
        raw_duration_s = probe_duration(video) if video.exists() else None
    except (EngineError, OSError, ValueError) as exc:
        logger.warning("package: could not probe duration: %s", exc)
        raw_duration_s = None
    duration_s = round(raw_duration_s, 2) if raw_duration_s is not None else None
    (ws / "publish_package.json").write_text(json.dumps({
        "title": pkg.title, "description": pkg.description, "tags": pkg.tags,
        "category_id": pkg.category_id, "privacy_status": pkg.privacy_status,
        "thumbnail_path": str(pkg.thumbnail_path) if pkg.thumbnail_path else None,
        "caption_srt_path": str(pkg.caption_srt_path) if pkg.caption_srt_path else None,
        "format": format_label(raw_duration_s), "duration_s": duration_s,
    }, indent=2), encoding="utf-8")

    caption = "\n".join(
        [hero_claim_text, ""]
        + [f"- {p}" for p in key_points]
        + ["", f"Full technical guide: {blog_url}"])
    (ws / "linkedin_caption.txt").write_text(caption, encoding="utf-8")
    logger.info("package: metadata + linkedin caption written")
    return {"publish_package": "publish_package.json",
            "linkedin_caption": "linkedin_caption.txt",
            "captions_srt": "subtitles.srt"}
