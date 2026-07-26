"""Vision Judge — scores an actual image (pixels, not caption) against a
scene's narration using the cloud multimodal model, and returns a focal point
for framing. This is the heart of the vision-first visual engine: it replaces
trusting captions with looking at the image.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from pathlib import Path

from video_agent.config import VISION_MODEL, VISION_TIMEOUT_S
from video_agent.vision.ollama_vision import call_vision_json

log = logging.getLogger(__name__)


@dataclass
class VisionVerdict:
    score: int                 # 0-10; how well the PIXELS support the narration
    reason: str                # one-line justification
    focus_x: float = 0.5       # normalized 0..1 centre of the most important content
    focus_y: float = 0.5
    subject_fills_frame: bool = False  # True => don't crop to 9:16, letterbox instead


_SYSTEM = (
    "You are a strict B2B video producer choosing the single best visual for "
    "ONE scene of a chemistry/industrial short aimed at procurement and "
    "supply-chain decision-makers. You are shown ONE image and the narration "
    "the voice will say over it. Judge ONLY what the image actually shows "
    "(the pixels), not any caption. A generic or wrong image breaks the "
    "viewer's trust, so be harsh: a stock photo of a businessman, an unrelated "
    "landscape, a meme, a watermark-covered image, or anything that does not "
    "literally depict what the narration describes scores 0-3. An on-topic, "
    "specific, professional industrial image scores 7-10. Diagrams/charts that "
    "are readable score well when the narration explains a mechanism.\n\n"
    "Also report where the important subject sits in the frame, as normalized "
    "coordinates (0,0 = top-left, 1,1 = bottom-right), and whether cropping "
    "this image to a tall 9:16 vertical would cut off important content "
    "(true) or not (false).\n\n"
    "Respond with RAW JSON only, no prose:\n"
    '{"score": <0-10 int>, "reason": "<short>", '
    '"focus_x": <0..1 float>, "focus_y": <0..1 float>, '
    '"subject_fills_frame": <true|false>}'
)


def _build_prompt(narration: str, beat: str, hero_claim: str,
                  visual_subject: str) -> str:
    return (
        f"{_SYSTEM}\n\n"
        f"Scene beat: {beat}\n"
        f"Intended subject: {visual_subject}\n"
        f"Hero claim of the whole video: {hero_claim}\n"
        f"Narration over this image:\n  {narration}\n\n"
        "Judge the attached image. Raw JSON only."
    )


def judge_image(
    image_path: Path,
    narration: str,
    beat: str = "",
    hero_claim: str = "",
    visual_subject: str = "",
    model: str = VISION_MODEL,
    timeout_s: float = VISION_TIMEOUT_S,
) -> VisionVerdict | None:
    """Return a VisionVerdict, or None if the model could not judge the image
    (timeout / failure). None means 'no judgment' — callers must treat that as
    'do not trust this image', NOT as a pass."""
    prompt = _build_prompt(narration, beat, hero_claim, visual_subject)
    out = call_vision_json(prompt, Path(image_path), model, timeout_s)
    if not isinstance(out, dict) or "score" not in out:
        log.warning("judge_image: unparseable/empty verdict for %s", image_path)
        return None
    try:
        score = max(0, min(10, int(out["score"])))
    except (TypeError, ValueError):
        return None

    def _clamp01(v, default=0.5):
        try:
            return max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            return default

    return VisionVerdict(
        score=score,
        reason=str(out.get("reason", ""))[:200],
        focus_x=_clamp01(out.get("focus_x", 0.5)),
        focus_y=_clamp01(out.get("focus_y", 0.5)),
        subject_fills_frame=bool(out.get("subject_fills_frame", False)),
    )
