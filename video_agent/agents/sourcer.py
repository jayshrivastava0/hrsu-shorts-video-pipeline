"""Sourcer — fan out to all sources, score, deduplicate, download."""
from __future__ import annotations
import dataclasses
import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from PIL import Image

from video_agent.sources.base import BaseSource, RawCandidate
from video_agent.sources.cache import QueryCache
from video_agent.sources.scoring import score_candidate, context_match_score
from video_agent.config import BRAND_SEARCH_CONTEXT
from video_agent.storyboard import (
    Storyboard, Scene, AssetCandidate, VisualConcept,
)
from video_agent.ollama_client import OllamaClient, OllamaError
from video_agent.vision.footage_index import FootageClip, discover_clips, best_footage_for_scene

# Permissive pre-filter only — the vision judge makes the real decision.
# 0  = caption hit a hard-reject pattern (Bollywood/news/etc.)
# 15 = very loose; the vision judge decides actual fit
_MIN_CONTEXT_SCORE = 15

log = logging.getLogger(__name__)

_TOPIC_DEFAULTS = {
    "wastewater_treatment": "industrial water treatment plant",
    "concrete_construction": "concrete pour construction site",
    "mining": "mining operation",
    "agriculture": "fertiliser application field",
    "oil_gas": "oil rig industrial",
    "water_treatment": "water treatment facility",
}

# Tokens that already imply industrial context — avoid double-stacking.
_INDUSTRIAL_TOKENS = ("industrial", "facility", "plant", "factory",
                      "manufacturing", "infrastructure", "refinery")


def _apply_context(q: str) -> str:
    """Bias the query toward B2B industrial imagery using positive semantic
    relevance — search engines rank by all terms together, so a context
    qualifier ("industrial") naturally outranks unrelated war/news footage
    for ambiguous queries like 'Saudi Arabia oil refinery'.

    No hardcoded blacklists: we trust relevance ranking once enough
    positive context is provided. Configurable via BRAND_SEARCH_CONTEXT."""
    if not q or not BRAND_SEARCH_CONTEXT:
        return q
    low = q.lower()
    if any(t in low for t in _INDUSTRIAL_TOKENS):
        return q
    return f"{q} {BRAND_SEARCH_CONTEXT}"


_STOP_WORDS = frozenset({
    "a","an","the","is","are","was","were","be","been","being","have","has",
    "had","do","does","did","will","would","could","should","may","might",
    "must","shall","and","or","but","for","nor","so","yet","in","on","at",
    "to","of","as","by","with","from","that","this","it","its","they",
    "their","we","our","you","your","he","she","his","her","not","no",
    "into","can","over","just","than","then","also","about","which","when",
})


def _narration_query(narration: str) -> str:
    """Extract 4 domain-specific content words from narration for image search."""
    words = [
        w.strip(".,;:!?\"'()") for w in narration.split()
        if len(w.strip(".,;:!?\"'()")) > 2
        and w.strip(".,;:!?\"'()").lower() not in _STOP_WORDS
    ]
    return " ".join(words[:4]) if words else ""


def _build_queries(vc: VisualConcept, blog_category: str,
                   narration: str = "") -> list[str]:
    specific = f"{vc.subject} {vc.modifier}".strip()
    abstract = vc.subject
    generic = _TOPIC_DEFAULTS.get(blog_category, "industrial process")
    # Narration-derived query as priority fallback when visual_concept is generic
    narr_q = _narration_query(narration) if narration else ""
    queries = [q for q in (specific, narr_q, abstract, generic) if q]
    # Deduplicate preserving order
    seen: set[str] = set()
    deduped = [q for q in queries if not (q in seen or seen.add(q))]  # type: ignore[func-returns-value]
    return [_apply_context(q) for q in deduped]


class Sourcer:
    def __init__(self, sources: list[BaseSource],
                 cache_root: Path, download_root: Path,
                 candidates_per_source: int = 5,
                 min_score: int = 40,
                 max_workers: int = 6,
                 client: OllamaClient | None = None):
        self.sources = sources
        self.cache = QueryCache(cache_root)
        self.download_root = Path(download_root)
        self.download_root.mkdir(parents=True, exist_ok=True)
        self.candidates_per_source = candidates_per_source
        self.min_score = min_score
        self.max_workers = max_workers
        self._used_phash_objects: list = []  # imagehash objects for hamming comparison
        self.client = client or OllamaClient()

    def run(self, sb: Storyboard) -> Storyboard:
        sb.narrative_thread = self._build_narrative_thread(sb)
        hero_text = sb.hero_claim.claim_text if sb.hero_claim else ""
        # Cache own-footage clips once for the whole video — avoid redundant ffprobe calls.
        self._footage_clips: list[FootageClip] = discover_clips()
        if self._footage_clips:
            log.info("Own footage: %d clips discovered", len(self._footage_clips))
        for scene in sb.scenes:
            self._source_scene(
                scene, sb.blog.get("category", ""),
                narrative_thread=sb.narrative_thread,
                hero_claim=hero_text,
            )
        self._flag_visual_jumps(sb)
        return sb

    def _build_narrative_thread(self, sb: Storyboard) -> list[list[str]]:
        if not sb.scenes:
            return []
        scene_lines = "\n".join(
            f"  scene {s.index}: {s.narration[:200]}" for s in sb.scenes
        )
        system = (
            "You extract cumulative topical anchor keywords for video scenes. "
            "For each scene, list 4-6 keywords representing what the audience "
            "is holding in their head at that moment — accumulating concepts "
            "across previous scenes, not just keywords for that line in "
            "isolation. Use 1-2 word noun phrases (e.g. 'pipe scaling', "
            "'industrial water', 'calcium ions'). Respond as JSON: "
            '{"thread": [["kw1","kw2","kw3","kw4"], ...]} '
            "with exactly len(scenes) entries."
        )
        prompt = (
            f"Hero claim: {sb.hero_claim.claim_text if sb.hero_claim else ''}\n"
            f"Scenes:\n{scene_lines}\n\n"
            "Emit one anchor-keyword list per scene, cumulative."
        )
        try:
            result = self.client.generate_json(prompt, system=system)
        except OllamaError as e:
            log.warning("NarrativeThread: gemma failed (%s); "
                        "falling back to per-scene local extraction", e)
            return self._local_thread_fallback(sb)
        thread = result.get("thread") if isinstance(result, dict) else None
        if not isinstance(thread, list) or len(thread) != len(sb.scenes):
            log.warning("NarrativeThread: malformed (got %r); using fallback",
                        type(thread))
            return self._local_thread_fallback(sb)
        out: list[list[str]] = []
        for entry in thread:
            if not isinstance(entry, list):
                out.append([])
            else:
                out.append([str(k) for k in entry if isinstance(k, str)][:6])
        return out

    def _local_thread_fallback(self, sb: Storyboard) -> list[list[str]]:
        from video_agent.sources.scoring import _tokens
        accum: set[str] = set()
        out: list[list[str]] = []
        for s in sb.scenes:
            accum |= _tokens(s.narration)
            out.append(sorted(accum)[:6])
        return out

    # Minimum LLM-judged relevance score (0-10) for a candidate to survive
    # the semantic rerank. Below this, the visual is more harmful than no
    # image at all (showing a truck for a chemistry narration breaks trust).
    _MIN_SEMANTIC_SCORE = 4

    def _semantic_rerank(
        self,
        gated: list[tuple[int, int, RawCandidate]],
        scene: Scene,
        narration: str,
        hero_claim: str,
    ) -> list[tuple[int, int, RawCandidate]]:
        # Superseded by vision judge in _source_scene; retained for reference.
        """LLM-based re-ranker. Judges each candidate's visual fit to the
        narration using gemma. Returns the gated list re-ordered by
        LLM score, with sub-_MIN_SEMANTIC_SCORE candidates dropped.

        On LLM failure, returns the original list unchanged (graceful
        degradation — keyword scoring is the safety net)."""
        if not gated:
            return gated
        top = gated[:12]
        rest = gated[12:]
        lines = []
        for i, (_, _, c) in enumerate(top):
            kind = "video clip" if c.is_clip else "image"
            cap = (c.caption or "<no caption>").replace("\n", " ").strip()[:160]
            tag = " [BLOG-CITED]" if c.source == "blog_references" else ""
            lines.append(f"  [{i}] ({c.source}, {kind}){tag}: {cap}")
        system = (
            "You are a B2B video producer selecting the strongest visual for "
            "ONE scene of a chemistry/industrial short. You will be given the "
            "narration the voice will say, and a numbered list of candidate "
            "image/clip captions. For each candidate, judge how well its "
            "actual visual content (inferred from the caption + source type) "
            "supports the narration. Be strict: a caption that overlaps "
            "topically but would show wrong imagery (e.g. a 'wastewater "
            "treatment plant' YouTube tutorial for a narration about mining "
            "AMD — the screen would show indoor pipes or trucks, not a mine) "
            "scores 2-4, not 7. Prefer specific, on-topic, on-region visuals. "
            "Penalise YouTube clips whose titles are tutorials or explainers "
            "when the narration calls for a concrete scene.\n\n"
            "STRONG PREFERENCES:\n"
            "  - [BLOG-CITED] candidates are pulled from the very research "
            "    paper / source the narration is paraphrasing. When their "
            "    caption is on-topic, boost to 8-10 — these are the most "
            "    credible visuals available.\n"
            "  - Diagrams or charts that look readable (axis labels visible, "
            "    legend implied by caption) score higher than a cropped or "
            "    zoomed fragment. Penalise captions that suggest the image "
            "    is a tiny detail of a larger figure ('inset', 'detail', "
            "    'zoom') unless the narration explicitly needs that detail.\n\n"
            "Output JSON: "
            '{"scores": [n0, n1, ...]} with one integer 0-10 per candidate, '
            "in the same order."
        )
        prompt = (
            f"Scene beat: {scene.beat}\n"
            f"Scene visual concept: {scene.visual_concept.subject} "
            f"{scene.visual_concept.modifier} (mood={scene.visual_concept.mood})\n"
            f"Hero claim: {hero_claim}\n"
            f"Narration: {narration}\n\n"
            f"Candidates ({len(top)}):\n" + "\n".join(lines) + "\n\n"
            f"Score each 0-10."
        )
        try:
            result = self.client.generate_json(prompt, system=system)
        except OllamaError as e:
            log.warning("Scene %d: semantic rerank LLM call failed (%s); "
                        "keeping keyword order", scene.index, e)
            return gated
        scores = result.get("scores") if isinstance(result, dict) else None
        if not isinstance(scores, list) or len(scores) != len(top):
            log.warning("Scene %d: semantic rerank returned malformed scores "
                        "(%r); keeping keyword order", scene.index, type(scores))
            return gated
        annotated: list[tuple[int, int, int, RawCandidate]] = []
        for (quality, ctx, c), s in zip(top, scores):
            try:
                llm = max(0, min(10, int(s)))
            except (TypeError, ValueError):
                llm = 0
            annotated.append((llm, ctx, quality, c))
        annotated.sort(key=lambda t: (-t[0], -t[1], -t[2]))
        log.info("Scene %d: semantic rerank top-3 LLM scores: %s",
                 scene.index, [t[0] for t in annotated[:3]])
        kept = [(q, ctx, c) for llm, ctx, q, c in annotated
                if llm >= self._MIN_SEMANTIC_SCORE]
        if not kept:
            log.warning("Scene %d: semantic rerank — best score %d below "
                        "threshold %d; rejecting all candidates",
                        scene.index, annotated[0][0] if annotated else 0,
                        self._MIN_SEMANTIC_SCORE)
            return []
        return kept + rest

    def _vision_select(
        self,
        scene: Scene,
        gated: list[tuple[int, int, RawCandidate]],
        narration: str,
        hero_claim: str,
        exclude_urls: set[str] | None = None,
    ) -> AssetCandidate | None:
        """Download shortlist, judge pixels via vision model, return the best
        AssetCandidate (or None). Also populates scene.asset_candidates.
        Respects VISION_FIRST_ENABLED safety flag."""
        from video_agent.vision.judge import judge_image
        from video_agent.config import (
            VISION_SELECT_MIN, VISION_JUDGE_SHORTLIST, VISION_JUDGE_WORKERS,
        )

        downloaded: list[tuple[int, RawCandidate, Path]] = []
        for quality, ctx, c in gated[:VISION_JUDGE_SHORTLIST]:
            if exclude_urls and c.url in exclude_urls:
                continue
            local = self._download_candidate(c, scene.index)
            if local is None or self._is_dup(local):
                continue
            downloaded.append((quality, c, local))

        if not downloaded:
            log.warning("Scene %d: no downloadable candidates", scene.index)
            return None

        def _judge(item):
            quality, c, local = item
            v = judge_image(
                local, narration,
                beat=scene.beat,
                hero_claim=hero_claim,
                visual_subject=f"{scene.visual_concept.subject} "
                               f"{scene.visual_concept.modifier}".strip(),
            )
            return (quality, c, local, v)

        judged = []
        with ThreadPoolExecutor(max_workers=VISION_JUDGE_WORKERS) as ex:
            for fut in as_completed([ex.submit(_judge, it) for it in downloaded]):
                judged.append(fut.result())

        scored = [(q, c, p, v) for (q, c, p, v) in judged if v is not None]
        scored.sort(key=lambda t: (-t[3].score, -t[0]))

        log.info("Scene %d: vision scores (top 3): %s", scene.index,
                 [v.score for (_, _, _, v) in scored[:3]])

        scene.asset_candidates = []
        chosen = None
        for quality, c, local, v in scored[:3]:
            ac = AssetCandidate(
                source=c.source, url=c.url, score=quality,
                local_path=str(local), caption=c.caption,
                width=c.width, height=c.height,
                is_clip=c.is_clip, duration_s=c.duration_s,
                vision_score=v.score, vision_reason=v.reason,
                focus_x=v.focus_x, focus_y=v.focus_y,
                subject_fills_frame=v.subject_fills_frame,
            )
            scene.asset_candidates.append(ac)
            if chosen is None and v.score >= VISION_SELECT_MIN:
                chosen = ac
                log.info("Scene %d chose %s (vision=%d kw=%d) reason=%r",
                         scene.index, c.source, v.score, quality, v.reason)

        if chosen is None:
            best = scored[0][3].score if scored else -1
            log.warning("Scene %d: best vision score %d < %d — no faithful "
                        "image; falling back to designed card.",
                        scene.index, best, VISION_SELECT_MIN)
        return chosen

    def _flag_visual_jumps(self, sb: Storyboard,
                           phash_threshold: int = 30) -> None:
        try:
            from PIL import Image as PImage
            import imagehash
        except Exception:
            return
        prev_hash = None
        for s in sb.scenes:
            if not s.chosen_asset or s.chosen_asset.is_clip:
                prev_hash = None
                continue
            try:
                with PImage.open(s.chosen_asset.local_path) as img:
                    h = imagehash.phash(img)
            except Exception:
                prev_hash = None
                continue
            if prev_hash is not None:
                dist = h - prev_hash
                if dist > phash_threshold:
                    s.critic_notes.flags.append("visual_jump")
                    log.info("Scene %d visual_jump (phash dist=%d)",
                             s.index, dist)
            prev_hash = h

    def _source_scene(self, scene: Scene, blog_category: str,
                      narrative_thread: list[list[str]] | None = None,
                      hero_claim: str = "") -> None:
        # ── Own-footage first: real HRSU footage beats stock on trust. ──────
        # Prefer own footage when its vision score meets the lower FOOTAGE_PREFER_MIN
        # bar — even a slightly weaker match from real footage is more credible.
        from video_agent.config import FOOTAGE_PREFER_MIN, VISION_FIRST_ENABLED
        footage_clips = getattr(self, "_footage_clips", None)
        if VISION_FIRST_ENABLED and footage_clips:
            narration_text = scene.narration or ""
            result = best_footage_for_scene(
                narration_text, scene.beat, hero_claim,
                f"{scene.visual_concept.subject} {scene.visual_concept.modifier}".strip(),
                clips=footage_clips,
            )
            if result is not None:
                clip, verdict = result
                if verdict.score >= FOOTAGE_PREFER_MIN:
                    ac = AssetCandidate(
                        source="own_footage", url=clip.path.as_uri(),
                        score=100,
                        local_path=str(clip.path), caption="",
                        width=0, height=0, is_clip=True,
                        duration_s=clip.duration_s,
                        vision_score=verdict.score, vision_reason=verdict.reason,
                        focus_x=verdict.focus_x, focus_y=verdict.focus_y,
                        subject_fills_frame=verdict.subject_fills_frame,
                    )
                    scene.chosen_asset = ac
                    scene.asset_candidates = [ac]
                    log.info("Scene %d: own footage selected (vision=%d) %s",
                             scene.index, verdict.score, clip.path.name)
                    return

        queries = _build_queries(scene.visual_concept, blog_category,
                                 narration=scene.narration or "")
        narration = scene.narration or ""
        thread_for_scene: list[str] = []
        if narrative_thread and scene.index < len(narrative_thread):
            thread_for_scene = narrative_thread[scene.index]
        all_raw: list[RawCandidate] = []
        for q in queries:
            raws = self._search_all_sources(q)
            all_raw.extend(raws)
            scored = [(score_candidate(c, q), c) for c in raws]
            scored = [t for t in scored if t[0] >= self.min_score]
            if len(scored) >= 3:
                break

        if not all_raw:
            scene.degraded = True
            log.warning("Scene %d: no candidates from any source", scene.index)
            return

        before = len(all_raw)
        gated: list[tuple[int, int, RawCandidate]] = []
        for c in all_raw:
            ctx = context_match_score(
                c.caption, narration,
                thread_keywords=thread_for_scene,
                hero_claim=hero_claim,
            )
            if ctx < _MIN_CONTEXT_SCORE:
                log.debug("Scene %d context-reject %s caption=%r (ctx=%d)",
                          scene.index, c.source, (c.caption or "")[:60], ctx)
                continue
            gated.append((score_candidate(c, queries[0]), ctx, c))
        log.info("Scene %d: %d candidates -> %d after cumulative context match",
                 scene.index, before, len(gated))

        if not gated:
            scene.degraded = True
            log.warning("Scene %d: every candidate failed context match -- "
                        "no visual is faithful to the narration; scene will "
                        "fall back to brand card.", scene.index)
            return

        # First sort: by context-match keyword score (cheap heuristic)
        gated.sort(key=lambda t: (-t[1], -t[0]))

        from video_agent.config import VISION_FIRST_ENABLED
        if VISION_FIRST_ENABLED:
            # ── Vision-first selection: judge the actual pixels, not captions. ──
            chosen = self._vision_select(scene, gated, narration, hero_claim)
            if chosen is None:
                scene.degraded = True
            else:
                scene.chosen_asset = chosen
        else:
            # Legacy caption-rerank path (fallback when VISION_FIRST_ENABLED=False).
            gated = self._semantic_rerank(gated, scene, narration, hero_claim)
            if not gated:
                scene.degraded = True
                log.warning("Scene %d: semantic re-rank rejected all candidates; "
                            "falling back to brand card.", scene.index)
                return

            scene.asset_candidates = []
            chosen = None
            for quality, ctx, c in gated[:5]:
                local = self._download_candidate(c, scene.index)
                if local is None or self._is_dup(local):
                    continue
                ac = AssetCandidate(
                    source=c.source, url=c.url, score=quality,
                    local_path=str(local), caption=c.caption,
                    width=c.width, height=c.height,
                    is_clip=c.is_clip, duration_s=c.duration_s,
                )
                scene.asset_candidates.append(ac)
                if chosen is None:
                    chosen = ac
                    log.info("Scene %d chose %s (q=%d, ctx=%d) %r",
                             scene.index, c.source, quality, ctx,
                             (c.caption or "")[:80])
                if len(scene.asset_candidates) >= 3:
                    break
            if chosen is None:
                scene.degraded = True
                log.warning("Scene %d: all candidates rejected after download/dedup",
                            scene.index)
            else:
                scene.chosen_asset = chosen

    def _search_all_sources(self, query: str) -> list[RawCandidate]:
        """Fan out to every source in parallel, hitting cache first."""
        out = []

        def _one(src: BaseSource) -> list[RawCandidate]:
            cached = self.cache.get(query, src.name)
            if cached is not None:
                return cached
            try:
                cands = src.search(query, limit=self.candidates_per_source)
            except Exception as e:                      # source failure → skip
                log.warning("Source %s failed for %r: %s", src.name, query, e)
                return []
            self.cache.put(query, src.name, cands)
            return cands

        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futures = [ex.submit(_one, s) for s in self.sources]
            for f in as_completed(futures):
                out.extend(f.result())
        return out

    def _download_candidate(self, c: RawCandidate, scene_idx: int) -> Path | None:
        ext = ".mp4" if c.is_clip else ".jpg"
        fname = f"scene_{scene_idx:02d}_{c.source}_{hashlib.md5(c.url.encode()).hexdigest()[:8]}{ext}"
        dest = self.download_root / fname
        if dest.exists():
            return dest
        if c.is_clip:
            return self._download_youtube_clip(c, dest)
        try:
            r = requests.get(c.url, timeout=20, headers={
                "User-Agent": "Mozilla/5.0 HRSU-VideoBot/2.0",
            })
            r.raise_for_status()
            dest.write_bytes(r.content)
            # Verify it's a valid image
            with Image.open(dest) as img:
                img.verify()
        except Exception as e:
            log.debug("Download/verify failed for %s: %s", c.url, e)
            dest.unlink(missing_ok=True)
            return None
        # Watermark check (graceful skip if Tesseract not installed)
        from video_agent.sources.watermark import is_watermarked
        watermarked, reason = is_watermarked(dest, self.cache.root)
        if watermarked:
            log.info("Scene %d candidate %s rejected: watermark (%s)",
                     scene_idx, c.url, reason)
            dest.unlink(missing_ok=True)
            return None
        return dest

    def _download_youtube_clip(self, c: RawCandidate, dest: Path) -> Path | None:
        """Download a 6–10s VIDEO-ONLY clip from the 25% mark of the source.

        Note: format='bestvideo[height<=1080]' explicitly excludes audio
        streams so even if a candidate slips past filtering, no music or
        speech from the source can ever leak into the final composition.
        """
        try:
            import yt_dlp
        except ImportError:
            log.warning("yt-dlp not installed; cannot download YouTube clip")
            return None
        start_s = max(0, int((c.duration_s or 60) * 0.25))
        opts = {
            "quiet": True, "no_warnings": True,
            # Video-only stream; falls back to 'best' if no separate video
            # stream is available, in which case we'll still strip with -an
            # downstream in the compositor.
            "format": "bestvideo[height<=1080]/best[height<=1080]",
            "outtmpl": str(dest),
            "download_ranges": lambda info, ydl:
                [{"start_time": start_s, "end_time": start_s + 10}],
            "force_keyframes_at_cuts": True,
            # Explicit: never write a separate audio file
            "writethumbnail": False,
            "postprocessors": [],
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([c.url])
            if not dest.exists():
                return None
            # Re-encode with explicit -an so even a muxed file becomes silent.
            silent = dest.with_name(dest.stem + "_silent.mp4")
            import subprocess as _sp
            try:
                _sp.run(
                    ["ffmpeg", "-y", "-loglevel", "error", "-i", str(dest),
                     "-c:v", "copy", "-an", str(silent)],
                    check=True,
                )
                dest.unlink()
                silent.rename(dest)
            except Exception as e:
                log.debug("Audio strip failed for %s (%s); compositor will -an at render", dest, e)
            return dest
        except Exception as e:
            log.warning("YouTube clip download failed for %s: %s", c.url, e)
            return None

    # Hamming-distance threshold: images with fewer differing bits than this
    # are considered perceptually identical (same photo from different CDN URLs,
    # re-compressed copies, etc.).
    _PHASH_DUP_THRESHOLD = 8

    def _is_dup(self, path: Path) -> bool:
        """Near-duplicate dedupe across scenes within the same video.

        Uses perceptual-hash hamming distance so slightly re-encoded or
        re-scaled versions of the same image are still caught."""
        try:
            from PIL import Image as PImage
            import imagehash
            with PImage.open(path) as img:
                h = imagehash.phash(img)
        except Exception:
            return False
        for existing_h in self._used_phash_objects:
            if (h - existing_h) <= self._PHASH_DUP_THRESHOLD:
                return True
        self._used_phash_objects.append(h)
        return False

    def re_source_scene(self, scene: Scene, blog_category: str,
                        exclude_urls: set[str],
                        thread_keywords: list[str] | None = None,
                        hero_claim: str = "") -> None:
        """Re-runs source-and-pick for a single scene, skipping previously-used URLs.
        Uses vision-judge selection (same as _source_scene) when VISION_FIRST_ENABLED."""
        narration = scene.narration or ""
        queries = _build_queries(scene.visual_concept, blog_category,
                                 narration=narration)
        all_raw: list[RawCandidate] = []
        for q in queries:
            raws = self._search_all_sources(q)
            all_raw.extend(r for r in raws if r.url not in exclude_urls)
            if len(all_raw) >= 5:
                break
        if not all_raw:
            return
        primary_q = queries[0]
        gated = []
        for c in all_raw:
            ctx = context_match_score(
                c.caption, narration,
                thread_keywords=thread_keywords or [],
                hero_claim=hero_claim or "",
            )
            if ctx < _MIN_CONTEXT_SCORE:
                continue
            gated.append((score_candidate(c, primary_q), ctx, c))
        if not gated:
            log.info("Scene %d re-source: 0 candidates passed context match", scene.index)
            return
        gated.sort(key=lambda t: (-t[1], -t[0]))

        from video_agent.config import VISION_FIRST_ENABLED
        if VISION_FIRST_ENABLED:
            chosen = self._vision_select(
                scene, gated, narration, hero_claim,
                exclude_urls=exclude_urls,
            )
            if chosen is not None:
                scene.chosen_asset = chosen
                scene.degraded = False
                scene._re_sourced = True
        else:
            for quality, ctx, c in gated[:5]:
                if quality < self.min_score:
                    continue
                local = self._download_candidate(c, scene.index)
                if local is None or self._is_dup(local):
                    continue
                scene.chosen_asset = AssetCandidate(
                    source=c.source, url=c.url, score=quality,
                    local_path=str(local), caption=c.caption,
                    width=c.width, height=c.height,
                    is_clip=c.is_clip, duration_s=c.duration_s,
                )
                scene.degraded = False
                scene._re_sourced = True
                return
