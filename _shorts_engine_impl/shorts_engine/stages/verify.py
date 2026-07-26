"""Stage 8 — VERIFY: heuristic gate (reused verify_heuristic) + a vision
gate that judges ONE frame per shot from the FINAL video against that shot's
narration span and expected on-screen text. Ungradeable after retries ⇒ the
run FAILS — never skipped (F8). The revise loop (Task 12) fixes what it can
deterministically; everything converges to designed cards."""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from shorts_engine import config
from shorts_engine.errors import EngineError
from shorts_engine.llm import text_llm

logger = logging.getLogger(__name__)

SHOT_VERDICT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "match_score": {"type": "integer", "minimum": 0, "maximum": 10},
        "legible": {"type": "boolean"},
        "issues": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["match_score", "legible", "issues"],
    "additionalProperties": False,
}

_VERDICT_SYSTEM = (
    "You verify a single frame of a technical B2B short against what should "
    "be on screen. match_score: does the frame's content match the narration "
    "and expected text (10 = clearly yes)? legible: is every piece of "
    "on-screen text comfortably readable at phone size (contrast + size)?"
)

# Seams
_heuristic = None
_describe = None
_verdict_call = None


def _resolve():
    heuristic, describe_fn, verdict = _heuristic, _describe, _verdict_call
    if heuristic is None:
        from video_agent.harness.verify_heuristic import verify_heuristic
        heuristic = verify_heuristic
    if describe_fn is None:
        from shorts_engine.llm.vision_judge import describe
        describe_fn = describe
    if verdict is None:
        verdict = text_llm.generate_schema_json
    return heuristic, describe_fn, verdict


def shot_timeline(assemble_report: dict) -> list[dict]:
    out, cursor = [], 0.0
    for s in assemble_report["shots"]:
        d = float(s["final_duration_s"])
        out.append({"id": s["id"], "start_s": round(cursor, 3),
                    "mid_s": round(cursor + d / 2, 3), "duration_s": d})
        cursor += d
    return out


def sample_shot_frames(video: Path, timeline: list[dict],
                       out_dir: Path) -> dict[str, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    frames: dict[str, Path] = {}
    for t in timeline:
        png = out_dir / f"frame_{t['id']}.png"
        res = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t['mid_s']:.3f}",
             "-i", str(video), "-frames:v", "1", str(png)],
            capture_output=True, text=True)
        if res.returncode != 0 or not png.exists():
            raise EngineError(f"VERIFY: frame sample failed for {t['id']}")
        frames[t["id"]] = png
    return frames


def _expected_text(payload: dict) -> str:
    """Join every text field a card renderer might put on screen. Covers all
    six designed renderers plus DIAGRAM's four templates (flow uses `labels`;
    before_after uses `before`/`after`; comparison uses `left`/`right` dicts
    with their own `title`/`items`; dosing_scale uses `lo`/`hi`) and BROLL's
    `caption` -- an incomplete list here silently starves the vision judge of
    context for whichever shot type it omits."""
    parts = [str(payload.get(k, "")) for k in
             ("text", "value", "unit", "label", "quote", "highlight",
              "differentiator", "cta_line", "domain", "caption", "lo", "hi")]
    parts += [str(x) for x in (payload.get("labels") or [])]
    parts += [str(x) for x in (payload.get("before") or [])]
    parts += [str(x) for x in (payload.get("after") or [])]
    for side in (payload.get("left") or {}), (payload.get("right") or {}):
        parts.append(str(side.get("title", "")))
        parts += [str(x) for x in (side.get("items") or [])]
    return " | ".join(p for p in parts if p)


def judge_shot_frame(frame: Path, narration_span: str, rendered_type: str,
                     payload: dict) -> dict:
    from shorts_engine.llm.vision_judge import verify_description, DESCRIBE_PROMPT
    _, describe_fn, verdict = _resolve()
    desc = describe_fn(frame)
    if verify_description(desc, DESCRIBE_PROMPT) is not None:
        return {"ungradeable": True}
    prompt = (
        f"SHOT TYPE: {rendered_type}\n"
        f"NARRATION FOR THIS SHOT: {narration_span}\n"
        f"EXPECTED ON-SCREEN TEXT: {_expected_text(payload)}\n\n"
        f"FRAME DESCRIPTION (from a blind viewing):\n{desc['description']}\n"
        f"VISIBLE TEXT SEEN: {desc.get('visible_text', '')}\n\n"
        f"Verdict as JSON."
    )
    return verdict(prompt, _VERDICT_SYSTEM, SHOT_VERDICT_SCHEMA)


def run_gates(ctx) -> dict:
    heuristic, _, _ = _resolve()
    ws = Path(ctx.workspace)
    video = ws / "video_short.mp4"
    hrep = heuristic(str(video), str(ws))
    assemble_report = json.loads((ws / "assemble_report.json").read_text(encoding="utf-8"))
    shotlist = {s["id"]: s for s in json.loads(
        (ws / "shotlist.json").read_text(encoding="utf-8"))["shots"]}
    vis = {s["id"]: s for s in json.loads(
        (ws / "visuals_report.json").read_text(encoding="utf-8"))["shots"]}

    timeline = shot_timeline(assemble_report)
    frames = sample_shot_frames(video, timeline, ws / "verify")

    failures: list[dict] = []
    if not getattr(hrep, "passed", True):
        checks = getattr(hrep, "checks", {}) or {}
        # video_agent.harness.verify_heuristic.verify_heuristic stores
        # MEASUREMENTS in `checks` (duration_s, audio_rms, resolution tuples,
        # ...) -- never booleans -- so `v is False` here can never be true;
        # the actual pass/fail signal per failure lives in `defects`, a list
        # of human-readable strings ("Audio RMS too low (...)", "No audio
        # stream found", ...). Scan those instead so a real audio failure
        # actually routes to the heuristic_audio fix (re-run AUDIO) rather
        # than always falling through to heuristic_safezone (caption-margin
        # bump, which cannot fix a silent/broken audio track).
        defects = getattr(hrep, "defects", []) or []
        lowered = [str(d).lower() for d in defects]
        if any("audio" in d for d in lowered):
            kind = "heuristic_audio"
        elif any("dark ribbon" in d for d in lowered):
            # Distinct from heuristic_safezone: no caption-margin bump can
            # raise background luma, so this needs its own real fix (see
            # apply_fixes and config.DARK_RIBBON_FIX_BAR_PX).
            kind = "heuristic_dark_ribbon"
        else:
            kind = "heuristic_safezone"
        failures.append({"id": "_global", "kind": kind, "checks": str(checks)})

    shots_out = []
    for t in timeline:
        sid = t["id"]
        verdict = judge_shot_frame(
            frames[sid], shotlist[sid].get("narration_span", ""),
            vis[sid]["rendered_type"], vis[sid].get("payload", {}))
        if verdict.get("ungradeable"):
            raise EngineError(
                f"VERIFY: shot {sid} ungradeable after retries — failing the "
                f"run, not skipping (F8)")
        entry = {"id": sid, "frame": str(frames[sid]), **verdict}
        shots_out.append(entry)
        acquired_broll = (vis[sid]["rendered_type"] == "BROLL"
                          and vis[sid]["provenance"].get("resolved") == "acquired")
        if acquired_broll and verdict["match_score"] < 5:
            failures.append({"id": sid, "kind": "broll_mismatch",
                             "score": verdict["match_score"]})
        if not verdict["legible"]:
            failures.append({"id": sid, "kind": "legibility",
                             "issues": verdict["issues"]})
    return {"heuristic": {"passed": getattr(hrep, "passed", True)},
            "shots": shots_out, "failures": failures}


def _render_shot(ctx, shot_id: str, rtype: str, payload: dict,
                 duration: float) -> None:
    from shorts_engine.stages.visuals import RENDERERS
    out = Path(ctx.workspace) / "shots" / f"shot_{shot_id}.mp4"
    RENDERERS[rtype](payload, duration, out)


def _reassemble(ctx) -> None:
    from shorts_engine.stages import assemble
    assemble.run(ctx)


# Flat string fields the shrink-in-place fix can act on directly. Originally
# only covered HEADLINE/STAT/QUOTE/PAPER's payload shape -- a legibility
# failure on LOGO_CTA or BROLL matched none of these, so the fix silently
# re-rendered an IDENTICAL payload while the fix log claimed "shortened text
# for legibility", and the run would exhaust its 2 revise cycles and raise on
# a fixable-looking failure the "fix" never touched. differentiator/cta_line
# (LOGO_CTA) and caption (BROLL) are the same flat-string shape, so the
# existing shrink logic applies unchanged.
_TEXT_FIELDS = ("text", "label", "quote", "highlight", "differentiator",
                "cta_line", "caption")


def apply_fixes(ctx, failures: list[dict]) -> list[str]:
    ws = Path(ctx.workspace)
    vis_path = ws / "visuals_report.json"
    vis = json.loads(vis_path.read_text(encoding="utf-8"))
    by_id = {s["id"]: s for s in vis["shots"]}
    shotlist = {s["id"]: s for s in json.loads(
        (ws / "shotlist.json").read_text(encoding="utf-8"))["shots"]}
    applied: list[str] = []

    for f in failures:
        kind, sid = f["kind"], f.get("id")
        if kind == "broll_mismatch":
            fb = shotlist[sid].get("fallback") or {}
            entry = by_id[sid]
            entry["rendered_type"] = fb["type"]
            entry["payload"] = fb["payload"]
            entry["provenance"] = {"resolved": "fallback",
                                   "reason": "verify_rejected"}
            _render_shot(ctx, sid, fb["type"], fb["payload"], entry["duration_s"])
            applied.append(f"{sid}: swapped to fallback {fb['type']}")
        elif kind == "legibility":
            entry = by_id[sid]
            payload = dict(entry["payload"])
            shrunk_field = None
            for field in _TEXT_FIELDS:
                words = str(payload.get(field, "")).split()
                if words:
                    keep = max(3, int(len(words) * config.LEGIBILITY_SHRINK_FACTOR))
                    payload[field] = " ".join(words[:keep])
                    shrunk_field = field
                    break
            if shrunk_field is None and payload.get("labels"):
                # DIAGRAM/flow is the only DIAGRAM template the shot planner
                # ever emits (shotlist.py's mechanism branch); its labels
                # list is the sole on-screen text, not a _TEXT_FIELDS string.
                labels = list(payload["labels"])
                keep_words = max(1, int(3 * config.LEGIBILITY_SHRINK_FACTOR))
                payload["labels"] = [" ".join(str(l).split()[:keep_words])
                                     for l in labels]
                shrunk_field = "labels"
            if shrunk_field is not None:
                entry["payload"] = payload
                _render_shot(ctx, sid, entry["rendered_type"], payload,
                             entry["duration_s"])
                applied.append(f"{sid}: shortened {shrunk_field} for legibility")
            else:
                # No known on-screen text field for this shot's payload
                # shape -- log honestly instead of claiming a fix that
                # re-rendered an identical payload. The revise loop still
                # re-gates and, if this was the only failure, still
                # exhausts its cycle budget and raises loudly (never-blank
                # is unaffected; nothing publishes with a real legibility
                # defect unaddressed).
                applied.append(f"{sid}: no deterministic legibility fix "
                               f"available for this shot's payload shape")
        elif kind == "heuristic_safezone":
            ctx.flags["caption_margin_bump"] = \
                int(ctx.flags.get("caption_margin_bump", 0)) + 40
            applied.append("caption margin bumped +40px")
        elif kind == "heuristic_dark_ribbon":
            # video_agent's dark-ribbon check was built for a lighter-
            # background pipeline and structurally false-positives on
            # shorts_engine's intentional navy brand theme -- no caption
            # fix can raise background luma, so this sets a real flag
            # assemble.run reads to add a persistent brand-gold accent
            # band under the moving progress bar (see config.py for the
            # luma math). ASSEMBLE re-runs on the next _reassemble() call.
            ctx.flags["dark_ribbon_fix"] = True
            applied.append("dark-ribbon accent band added")
        elif kind == "heuristic_audio":
            from shorts_engine.stages import audio
            audio.run(ctx)
            applied.append("audio stage re-run")
    vis_path.write_text(json.dumps(vis, indent=2), encoding="utf-8")
    return applied


def run(ctx) -> dict[str, str]:
    ws = Path(ctx.workspace)
    all_fixes: list[str] = []
    cycles = 0
    result = None
    for cycle in range(1, config.VERIFY_MAX_REVISE_CYCLES + 2):
        cycles = cycle
        result = run_gates(ctx)
        if not result["failures"]:
            break
        if cycle > config.VERIFY_MAX_REVISE_CYCLES:
            raise EngineError(
                f"VERIFY: {len(result['failures'])} failure(s) remain after "
                f"{config.VERIFY_MAX_REVISE_CYCLES} revise cycles: "
                f"{[f['kind'] for f in result['failures']]}")
        logger.info("verify cycle %d: %d failure(s) — applying deterministic "
                    "fixes", cycle, len(result["failures"]))
        all_fixes += apply_fixes(ctx, result["failures"])
        _reassemble(ctx)

    report = {"cycles": cycles, "fixes_applied": all_fixes, "final": result}
    (ws / "verify_report.json").write_text(json.dumps(report, indent=2),
                                           encoding="utf-8")
    from shorts_engine.review.contact_sheet import build
    sheet = build(ctx, report)
    logger.info("verify: passed in %d cycle(s), %d fix(es)", cycles,
                len(all_fixes))
    return {"verify_report": "verify_report.json",
            "contact_sheet": sheet.name}
