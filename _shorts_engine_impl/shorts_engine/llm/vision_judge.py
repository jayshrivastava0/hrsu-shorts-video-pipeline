"""Describe-then-match vision judge (spec §6.2). The model NEVER sees the
desired subject while looking at pixels: call 1 describes the image blind;
call 2 (text-only) scores that description against the wish + narration.
Attach-verification makes a failed/refused describe a hard reject — a
failure can never pass (F3)."""
from __future__ import annotations

import logging
import time
from pathlib import Path

from shorts_engine import config
from shorts_engine.llm import text_llm

logger = logging.getLogger(__name__)

DESCRIBE_PROMPT = (
    "Describe exactly what this image shows: subjects, setting, any visible "
    "text or watermarks, image quality. Respond with raw JSON only: "
    '{"description": str, "visible_text": str, "quality_notes": str}'
)

MATCH_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": 0, "maximum": 10},
        "reason": {"type": "string"},
        "focal_hint": {"enum": ["center", "left", "right", "top", "bottom"]},
    },
    "required": ["score", "reason", "focal_hint"],
    "additionalProperties": False,
}

_MATCH_SYSTEM = (
    "You score how well an image DESCRIPTION matches a desired b-roll "
    "subject for a technical B2B video. 0 = unrelated, 10 = exactly the "
    "subject, correctly framed. Penalize stocky/staged imagery. focal_hint "
    "= where the main subject sits in frame."
)

# Late-binding test seams (resolved at call time; audio.py pattern).
_describe_call = None
_match_call = None
_sleep = time.sleep


def _resolve():
    describe_fn, match_fn = _describe_call, _match_call
    if describe_fn is None:
        from video_agent.vision.ollama_vision import call_vision_auto
        describe_fn = call_vision_auto
    if match_fn is None:
        match_fn = text_llm.generate_schema_json
    return describe_fn, match_fn


def describe(image_path: Path) -> dict | None:
    from video_agent.config import VISION_MODEL, VISION_TIMEOUT_S
    describe_fn, _ = _resolve()
    for attempt in range(1, 4):
        out = describe_fn(DESCRIBE_PROMPT, Path(image_path), VISION_MODEL,
                          VISION_TIMEOUT_S)
        if isinstance(out, dict) and "description" in out:
            return out
        if attempt < 3:
            _sleep(2 ** attempt)
    return None


def verify_description(desc: dict | None, prompt: str) -> str | None:
    if not isinstance(desc, dict) or not desc.get("description"):
        return "describe_failed"
    text = str(desc["description"])
    if len(text) < config.VISION_DESCRIBE_MIN_CHARS:
        return "description_too_short"
    lowered = text.lower()
    for phrase in config.VISION_REFUSAL_PHRASES:
        if phrase in lowered:
            return "refusal_phrase"
    if len(prompt) >= 40:
        for i in range(0, max(1, len(prompt) - 40), 20):
            if prompt[i:i + 40].lower() in lowered:
                return "prompt_echo"
    visible = str(desc.get("visible_text") or "").lower()
    for term in config.WATERMARK_TERMS:
        if term in visible:
            return "watermark_text"
    return None


def match(description: str, wish: str, narration_span: str) -> dict:
    _, match_fn = _resolve()
    prompt = (
        f"DESIRED SUBJECT (broll wish): {wish}\n"
        f"NARRATION THIS SHOT COVERS: {narration_span}\n\n"
        f"IMAGE DESCRIPTION (from a separate blind viewing):\n{description}\n\n"
        f"Score the match now as JSON."
    )
    return match_fn(prompt, _MATCH_SYSTEM, MATCH_SCHEMA)


def judge(image_path: Path, wish: str, narration_span: str) -> dict:
    desc = describe(image_path)
    reason = verify_description(desc, DESCRIBE_PROMPT)
    if reason is not None:
        logger.info("judge reject (%s): %s", reason, image_path)
        return {"accepted_score": 0, "description": "", "focal_hint": "center",
                "reject_reason": reason}
    m = match(desc["description"], wish, narration_span)
    return {"accepted_score": int(m["score"]), "description": desc["description"],
            "focal_hint": m["focal_hint"], "reject_reason": None}
