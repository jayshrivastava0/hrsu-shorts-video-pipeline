"""Vision-LLM artifact gate (Phase 3). Extracts one mid-frame per rendered
scene clip and asks the multimodal Gemma to grade it against the scene's
narration and the written rubric. Decision bands:
  every scene overall >= VISION_PASS_MIN          -> passed
  any scene overall  <  VISION_FAIL_BELOW         -> actionable (revise loop)
  otherwise                                        -> hold (operator review)
An ungradeable scene (LLM failure) forces hold — the gate never silently
passes what it could not see.

Production grading uses `ollama run MODEL PROMPT image.jpg` via subprocess
because Ollama cloud models (e.g. gemma4:31b-cloud) reject the /api/generate
`images` field even though `ollama run` works. Tests inject a mock OllamaClient
via the `client` parameter to bypass subprocess entirely."""
from __future__ import annotations
import json
import logging
import re
import shutil
import subprocess
from pathlib import Path

from video_agent.config import (
    VISION_MODEL, VISION_TIMEOUT_S, VISION_PASS_MIN, VISION_FAIL_BELOW,
)
from video_agent.harness.manifest import SceneGrade, VisionReport
from video_agent.harness.rubric import load_rubric
from video_agent.ollama_client import OllamaClient, OllamaError

log = logging.getLogger(__name__)

_FFMPEG = shutil.which("ffmpeg")
_FFPROBE = shutil.which("ffprobe")
_OLLAMA = shutil.which("ollama")

# Strips ANSI escape sequences and Gemma 4 think blocks from CLI output.
# Strip every ESC sequence: CSI (ESC [) sequences and lone ESC + one char.
_ANSI_RE = re.compile(r"\x1b(?:\[[^a-zA-Z]*[a-zA-Z]|[^\[]\S*|\[[^\x1b]*)")
# Terminal line-wrap: (optional cursor-back-N +) erase-to-EOL + newline.
# ollama wraps long model output at terminal width using this pattern,
# which embeds literal newlines inside JSON string values (invalid JSON).
# Removing the sequence joins the split line without inserting a newline.
_TERM_WRAP_RE = re.compile(r"\x1b\[(?:\d+D\x1b\[)?K\r?\n")

_SYSTEM = (
    "You are a strict video-quality grader for B2B industrial shorts. "
    "Grade the supplied frame against each rubric criterion from 0 (terrible) "
    "to 10 (excellent). Be harsh on generic stock imagery that does not "
    "illustrate the narration. Respond ONLY with raw JSON: "
    '{"scores": {"<criterion_key>": <0-10>, ...}, '
    '"defects": [{"code": "<one of: visual_mismatch, text_clipped, '
    'text_unreadable, off_brand, low_quality>", "detail": "<short reason>"}]}'
)


def _clip_duration(clip: Path) -> float:
    out = subprocess.run(
        [_FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(clip)],
        check=True, capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def extract_scene_frames(workspace: Path) -> list[tuple[int, Path]]:
    """Mid-frame JPEG per scene_clips/scene_XX.mp4, ordered by scene index."""
    workspace = Path(workspace)
    clips_dir = workspace / "scene_clips"
    frames_dir = workspace / "_vision_frames"
    frames_dir.mkdir(exist_ok=True)
    result: list[tuple[int, Path]] = []
    for clip in sorted(clips_dir.glob("scene_*.mp4")):
        try:
            idx = int(clip.stem.split("_")[1])
        except (IndexError, ValueError):
            continue
        mid = max(0.1, _clip_duration(clip) / 2)
        frame = frames_dir / f"scene_{idx:02d}.jpg"
        subprocess.run(
            [_FFMPEG, "-y", "-loglevel", "error", "-ss", f"{mid:.2f}",
             "-i", str(clip), "-vframes", "1", "-q:v", "3", str(frame)],
            check=True, capture_output=True)
        if frame.exists():
            result.append((idx, frame))
    return result


def _build_prompt(narration: str, on_screen_text: str, rubric: dict) -> str:
    criteria_text = "\n".join(
        f"- {c['key']}: {c['description']}" for c in rubric["criteria"])
    return (
        f"{_SYSTEM}\n\n"
        f"Hero claim of the video: {rubric.get('hero_claim', '')}\n"
        f"Scene narration (what the voiceover says over this frame):\n"
        f"  {narration}\n"
        f"Expected on-screen text: {on_screen_text}\n\n"
        f"Rubric criteria:\n{criteria_text}\n\n"
        "Grade this frame. Respond with raw JSON only."
    )


def _parse_grade(raw: str, rubric: dict) -> dict | None:
    """Strip ANSI codes, isolate post-thinking text, extract last JSON object.
    `ollama run` with Gemma 4 emits 'Thinking...<think>...done thinking.\n\n<answer>'.
    We take only the answer section so we don't match partial JSON in the think block."""
    # 0. Remove terminal line-wrap sequences (cursor-back + erase + newline)
    #    before ANSI stripping — these embed literal newlines inside JSON
    #    string values (invalid JSON) when ollama wraps long output.
    clean = _TERM_WRAP_RE.sub("", raw)
    # 1. Strip remaining ANSI escape sequences
    clean = _ANSI_RE.sub("", clean)
    # 2. Isolate post-thinking section if present
    marker = "...done thinking."
    pos = clean.rfind(marker)
    if pos >= 0:
        after = clean[pos + len(marker):]
        log.debug("post-thinking (%d chars): %r", len(after), after[:500])
        clean = after
    else:
        log.debug("no thinking marker; full clean (%d chars): %r", len(clean), clean[:500])
    # 3. Find the LAST balanced { ... } (the model's actual JSON answer)
    last_obj = None
    i = len(clean) - 1
    while i >= 0:
        if clean[i] == "}":
            # walk backwards to find matching {
            depth = 0
            for j in range(i, -1, -1):
                if clean[j] == "}":
                    depth += 1
                elif clean[j] == "{":
                    depth -= 1
                    if depth == 0:
                        try:
                            # Normalize newlines before parsing: terminal
                            # line-wrap may embed literal \n inside string
                            # values (invalid JSON); spaces are safe.
                            candidate = clean[j:i + 1].replace("\n", " ")
                            last_obj = json.loads(candidate)
                            break
                        except json.JSONDecodeError:
                            break
            if last_obj is not None:
                break
        i -= 1
    return last_obj


def _grade_scene_cli(frame: Path, narration: str, on_screen_text: str,
                     rubric: dict, model: str,
                     timeout: float) -> SceneGrade | None:
    """Grade via `ollama run MODEL PROMPT image.jpg` — works for cloud models
    that reject the /api/generate images field.
    TERM=dumb prevents cursor-rewrite ANSI sequences that corrupt captured JSON."""
    prompt = _build_prompt(narration, on_screen_text, rubric)
    import os
    env = {**os.environ, "TERM": "dumb"}
    try:
        result = subprocess.run(
            [_OLLAMA, "run", model, prompt, str(frame)],
            capture_output=True, timeout=timeout, env=env,
            stdin=subprocess.DEVNULL,  # force non-interactive (no TTY cursor rewrites)
        )
        stdout = result.stdout.decode("utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        log.warning("vision grade timed out after %.0fs", timeout)
        return None
    except Exception as e:
        log.warning("vision grade subprocess error: %s", e)
        return None

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        log.warning("vision grade failed (exit %d): %s",
                    result.returncode, stderr[:200])
        return None

    out = _parse_grade(stdout, rubric)
    if not isinstance(out, dict) or not isinstance(out.get("scores"), dict):
        log.warning("vision grade malformed payload: %r", stdout[:300])
        return None

    keys = [c["key"] for c in rubric["criteria"]]
    scores = {k: float(out["scores"].get(k, 0)) for k in keys}
    overall = round(sum(scores.values()) / max(1, len(scores)), 2)
    defects = [d for d in out.get("defects", [])
               if isinstance(d, dict) and d.get("code")]
    return SceneGrade(index=-1, overall=overall, scores=scores, defects=defects)


def _grade_scene(client: OllamaClient, frame: Path, narration: str,
                 on_screen_text: str, rubric: dict) -> SceneGrade | None:
    """Test-injectable path: uses OllamaClient.generate_json with base64 image."""
    import base64
    b64 = base64.b64encode(frame.read_bytes()).decode()
    criteria_text = "\n".join(
        f"- {c['key']}: {c['description']}" for c in rubric["criteria"])
    prompt = (
        f"Hero claim of the video: {rubric.get('hero_claim', '')}\n"
        f"Scene narration: {narration}\n"
        f"Expected on-screen text: {on_screen_text}\n\n"
        f"Rubric criteria:\n{criteria_text}\n\nGrade this frame."
    )
    try:
        out = client.generate_json(prompt, system=_SYSTEM, images=[b64])
    except OllamaError as e:
        log.warning("vision grade failed: %s", e)
        return None
    if not isinstance(out, dict) or not isinstance(out.get("scores"), dict):
        log.warning("vision grade returned malformed payload: %r", out)
        return None
    keys = [c["key"] for c in rubric["criteria"]]
    scores = {k: float(out["scores"].get(k, 0)) for k in keys}
    overall = round(sum(scores.values()) / max(1, len(scores)), 2)
    defects = [d for d in out.get("defects", [])
               if isinstance(d, dict) and d.get("code")]
    return SceneGrade(index=-1, overall=overall, scores=scores, defects=defects)


def grade_video(storyboard, workspace: Path,
                client: OllamaClient | None = None) -> VisionReport:
    """Grade all scene frames. When `client` is None (production), uses the
    `ollama run` CLI subprocess which supports cloud multimodal models. When
    `client` is provided (tests), uses the OllamaClient API path."""
    workspace = Path(workspace)
    use_cli = client is None
    if not use_cli:
        # Test path: use the injected mock client
        _client = client
    rubric = load_rubric(workspace)
    frames = extract_scene_frames(workspace)
    scenes_by_index = {s.index: s for s in storyboard.scenes}

    graded: list[SceneGrade] = []
    ungradeable = False
    for idx, frame in frames:
        scene = scenes_by_index.get(idx)
        if scene is None:
            continue
        if use_cli:
            g = _grade_scene_cli(frame, scene.narration, scene.on_screen_text,
                                  rubric, VISION_MODEL, VISION_TIMEOUT_S)
        else:
            g = _grade_scene(_client, frame, scene.narration,
                             scene.on_screen_text, rubric)
        if g is None:
            ungradeable = True
            graded.append(SceneGrade(index=idx, overall=0.0, scores={},
                                     defects=[{"code": "ungradeable",
                                               "detail": "LLM call failed"}]))
            continue
        g.index = idx
        graded.append(g)
        log.info("vision: scene %d overall=%.1f defects=%s",
                 idx, g.overall, [d["code"] for d in g.defects])

    if not graded:
        return VisionReport(passed=False, hold=True, scenes=[])

    if ungradeable:
        # Could not see everything -> never auto-publish, never auto-revise.
        return VisionReport(passed=False, hold=True, scenes=graded)

    overalls = [g.overall for g in graded]
    if all(o >= VISION_PASS_MIN for o in overalls):
        return VisionReport(passed=True, hold=False, scenes=graded)
    if any(o < VISION_FAIL_BELOW for o in overalls):
        return VisionReport(passed=False, hold=False, scenes=graded)
    return VisionReport(passed=False, hold=True, scenes=graded)
