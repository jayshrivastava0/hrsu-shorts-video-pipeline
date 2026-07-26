# Video Harness Phase 3 — Vision-LLM Verifier + Closed Revise Loop

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a vision-LLM artifact gate that grades each rendered scene's frame against its narration and a written rubric, routes graded defects back to the responsible stage (re-source / re-render) for at most 2 revise cycles, and holds uncertain results for operator review instead of failing hard.

**Architecture:** A new `VISION` phase slots into the `HarnessRunner` between heuristic `VERIFY` and `PACKAGE`. `verify_vision.py` extracts one mid-frame per scene clip, sends frame + narration + rubric to the multimodal Gemma via Ollama's `images` field, and returns per-scene scores/defects. `revise_router.py` maps defect codes to actions (`re_source` scene N → rebuild that scene's asset via the existing `Sourcer`; `re_render` → re-run the RENDER phase). The loop is bounded; anything not cleanly passing after the cycle budget goes to `hold_for_review` (an operator queue file), never to publish.

**Tech Stack:** Python 3.12, pytest, ffmpeg/ffprobe, Pillow, `OllamaClient` (extended with image input), `gemma4:31b-cloud` (vision-verified 2026-06-12: both it and `gemma3:4b` correctly described a test image via `/api/generate` `images`).

**Project conventions:**
- **No git.** Do NOT run any git command. "Checkpoint" = verify the diff visually, then continue.
- Tests live under `tests/video_agent/...` mirroring the source tree.
- Run one test: `pytest tests/video_agent/harness/test_verify_vision.py::test_name -v`
- Run full suite: `pytest tests/ -q` (slow ~4 min; use targeted runs during TDD).

**Phase 3 spec reference:** `docs/superpowers/specs/2026-06-08-video-harness-design.md` (PHASE 3 section). The blocking open question (cloud Gemma multimodality) is RESOLVED — confirmed working.

---

## File Structure

**New files:**
- `video_agent/harness/rubric.py` — the written grading contract: default criteria, save/load of `rubric.json` (emitted during PLAN — "definition-of-done as a written contract")
- `video_agent/harness/verify_vision.py` — frame extraction + per-scene vision grading → `VisionReport`
- `video_agent/harness/revise_router.py` — defect→action mapping + action application (re-source / re-render)

**Modified files:**
- `video_agent/config.py` — vision knobs (append only)
- `video_agent/ollama_client.py` — `images` parameter on `generate` / `generate_json`
- `video_agent/harness/manifest.py` — `SceneGrade`/`VisionReport` dataclasses, `vision` field, new statuses
- `video_agent/harness/runner.py` — PLAN writes rubric; VISION phase + bounded revise loop + hold queue

**New tests:**
- `tests/video_agent/test_ollama_images.py`
- `tests/video_agent/harness/test_rubric.py`
- `tests/video_agent/harness/test_verify_vision.py`
- `tests/video_agent/harness/test_revise_router.py`
- `tests/video_agent/harness/test_runner_vision.py`
- (extend) `tests/video_agent/harness/test_manifest.py`

---

## Task 1: Config knobs

**Files:**
- Modify: `video_agent/config.py` (append at end)

- [ ] **Step 1.1: Append vision-verifier config**

Add to the end of `video_agent/config.py`:

```python
# ─── Harness: vision verification (Phase 3) ────────────────────────────────
VISION_MODEL = "gemma4:31b-cloud"   # multimodal via Ollama images field
VISION_TIMEOUT_S = 300              # cloud round-trip per scene can be slow
VISION_PASS_MIN = 7.0               # every scene's overall >= this -> pass
VISION_FAIL_BELOW = 5.0             # any scene overall < this -> actionable defect
VISION_MAX_REVISE_CYCLES = 2        # bounded revise loop (spec: <=2)
REVIEW_QUEUE_PATH = "review_queue.json"   # operator hold-for-review queue
```

- [ ] **Step 1.2: Verify it imports**

Run: `python -c "import video_agent.config as c; print(c.VISION_MODEL, c.VISION_PASS_MIN, c.VISION_MAX_REVISE_CYCLES)"`
Expected: `gemma4:31b-cloud 7.0 2`

- [ ] **Step 1.3: Checkpoint** — confirm only appended lines changed in `config.py`.

---

## Task 2: OllamaClient image input

**Files:**
- Modify: `video_agent/ollama_client.py`
- Create: `tests/video_agent/test_ollama_images.py`

- [ ] **Step 2.1: Write failing tests**

Create `tests/video_agent/test_ollama_images.py`:

```python
"""OllamaClient must pass base64 images through to /api/generate."""
from unittest.mock import patch, MagicMock
from video_agent.ollama_client import OllamaClient


def _fake_response(text='{"ok": true}'):
    r = MagicMock()
    r.json.return_value = {"response": text}
    r.raise_for_status.return_value = None
    return r


def test_generate_includes_images_in_body():
    client = OllamaClient(model="gemma4:31b-cloud")
    with patch("video_agent.ollama_client.requests.post",
               return_value=_fake_response("a photo")) as post:
        client.generate("describe", images=["QUJD"])  # base64 "ABC"
    body = post.call_args.kwargs["json"]
    assert body["images"] == ["QUJD"]


def test_generate_omits_images_key_when_none():
    client = OllamaClient()
    with patch("video_agent.ollama_client.requests.post",
               return_value=_fake_response()) as post:
        client.generate("hi")
    body = post.call_args.kwargs["json"]
    assert "images" not in body


def test_generate_json_forwards_images():
    client = OllamaClient()
    with patch("video_agent.ollama_client.requests.post",
               return_value=_fake_response('{"score": 8}')) as post:
        out = client.generate_json("grade this", images=["QUJD"])
    assert out == {"score": 8}
    assert post.call_args.kwargs["json"]["images"] == ["QUJD"]
```

- [ ] **Step 2.2: Run tests — verify they fail**

Run: `pytest tests/video_agent/test_ollama_images.py -v`
Expected: FAIL — `TypeError: generate() got an unexpected keyword argument 'images'`

- [ ] **Step 2.3: Add the `images` parameter**

In `video_agent/ollama_client.py`, change the `generate` signature and body:

```python
    def generate(self, prompt: str, system: str | None = None,
                 temperature: float | None = None,
                 top_p: float | None = None,
                 top_k: int | None = None,
                 images: list[str] | None = None) -> str:
```

and after the `body = {...}` construction add:

```python
        if images:
            body["images"] = images
```

Change `generate_json` to forward them:

```python
    def generate_json(self, prompt: str, system: str | None = None,
                      retries: int = OLLAMA_RETRY_MAX,
                      images: list[str] | None = None) -> dict | list:
        sys = (system or "") + "\nRespond with raw JSON only. No prose, no markdown."
        last_err = None
        for attempt in range(1, retries + 1):
            raw = self.generate(prompt, system=sys, images=images)
```

(only the signature line and the `self.generate(...)` call line change).

- [ ] **Step 2.4: Run tests — verify they pass**

Run: `pytest tests/video_agent/test_ollama_images.py -v`
Expected: 3 PASS.

- [ ] **Step 2.5: Checkpoint** — confirm no other `generate()` call sites broke: `pytest tests/video_agent -q -k "ollama or strategist or critic"`.

---

## Task 3: Manifest — VisionReport + new statuses

**Files:**
- Modify: `video_agent/harness/manifest.py`
- Modify: `tests/video_agent/harness/test_manifest.py` (append tests)

- [ ] **Step 3.1: Write failing tests**

Append to `tests/video_agent/harness/test_manifest.py`:

```python
def test_vision_report_roundtrip(tmp_path):
    from video_agent.harness.manifest import SceneGrade, VisionReport
    m = new_manifest(blog_url="u", slug="s", workspace=str(tmp_path))
    m.status = "vision_verified"
    m.vision = VisionReport(
        passed=True, hold=False, cycles_used=1,
        scenes=[SceneGrade(index=0, overall=8.5,
                           scores={"visual_match": 9, "readability": 8},
                           defects=[])],
    )
    p = tmp_path / "m.json"
    save_manifest(m, p)
    loaded = load_manifest(p)
    assert loaded.status == "vision_verified"
    assert loaded.vision.passed is True
    assert loaded.vision.scenes[0].overall == 8.5
    assert loaded.vision.scenes[0].scores["visual_match"] == 9


def test_vision_none_roundtrip(tmp_path):
    m = new_manifest(blog_url="u", slug="s", workspace=str(tmp_path))
    p = tmp_path / "m.json"
    save_manifest(m, p)
    assert load_manifest(p).vision is None
```

- [ ] **Step 3.2: Run tests — verify they fail**

Run: `pytest tests/video_agent/harness/test_manifest.py -v`
Expected: the two new tests FAIL — `ImportError: cannot import name 'SceneGrade'`.

- [ ] **Step 3.3: Implement**

In `video_agent/harness/manifest.py`:

1. Extend the status literal:

```python
RunStatus = Literal["init", "planned", "generated", "rendered", "verified",
                    "vision_verified", "hold_for_review",
                    "packaged", "published", "failed"]
```

2. Add the dataclasses (above `RunManifest`):

```python
@dataclass
class SceneGrade:
    index: int
    overall: float
    scores: dict[str, Any] = field(default_factory=dict)
    defects: list[dict] = field(default_factory=list)


@dataclass
class VisionReport:
    passed: bool
    hold: bool = False
    cycles_used: int = 0
    scenes: list[SceneGrade] = field(default_factory=list)
```

3. Add the field to `RunManifest` (after `verify`):

```python
    vision: VisionReport | None = None
```

4. Reconstruct it in `load_manifest` (alongside the existing `verify`/`package`/`publish` blocks):

```python
    vis = d.get("vision")
    ...
        vision=(VisionReport(
            passed=vis["passed"], hold=vis.get("hold", False),
            cycles_used=vis.get("cycles_used", 0),
            scenes=[SceneGrade(**s) for s in vis.get("scenes", [])],
        ) if vis else None),
```

- [ ] **Step 3.4: Run tests — verify they pass**

Run: `pytest tests/video_agent/harness/test_manifest.py -v`
Expected: all PASS (old + 2 new).

- [ ] **Step 3.5: Checkpoint** — `asdict` serializes nested `SceneGrade` automatically; confirm the saved JSON contains a `vision` block.

---

## Task 4: Rubric — the written grading contract

**Files:**
- Create: `video_agent/harness/rubric.py`
- Create: `tests/video_agent/harness/test_rubric.py`

- [ ] **Step 4.1: Write failing tests**

Create `tests/video_agent/harness/test_rubric.py`:

```python
from pathlib import Path
from video_agent.harness.rubric import (
    DEFAULT_CRITERIA, write_rubric, load_rubric,
)


def test_default_criteria_complete():
    keys = {c["key"] for c in DEFAULT_CRITERIA}
    assert {"visual_match", "readability", "framing",
            "brand_safety", "coherence"} <= keys
    for c in DEFAULT_CRITERIA:
        assert c["description"]          # every criterion is explained


def test_write_then_load_roundtrip(tmp_path: Path):
    p = write_rubric(tmp_path, hero_claim="25% strength boost")
    assert p.exists()
    rub = load_rubric(tmp_path)
    assert rub["hero_claim"] == "25% strength boost"
    assert rub["criteria"] == DEFAULT_CRITERIA


def test_load_missing_returns_default(tmp_path: Path):
    rub = load_rubric(tmp_path)               # nothing written
    assert rub["criteria"] == DEFAULT_CRITERIA
    assert rub["hero_claim"] == ""
```

- [ ] **Step 4.2: Run tests — verify they fail**

Run: `pytest tests/video_agent/harness/test_rubric.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 4.3: Implement**

Create `video_agent/harness/rubric.py`:

```python
"""The grading rubric: the written definition-of-done contract the verifier
grades against (Anthropic 'contract negotiation' principle). Emitted during
PLAN as rubric.json so it exists before any artifact does."""
from __future__ import annotations
import json
from pathlib import Path

DEFAULT_CRITERIA: list[dict] = [
    {"key": "visual_match",
     "description": ("Does the image plausibly illustrate what the narration "
                     "for this scene is saying? Generic stock that could "
                     "accompany any topic scores <=4.")},
    {"key": "readability",
     "description": ("Is all on-screen text fully visible, uncropped, and "
                     "readable at a glance on a phone?")},
    {"key": "framing",
     "description": ("Is the subject well-framed for 9:16 vertical? No "
                     "squashed/stretched imagery, no accidental empty bands.")},
    {"key": "brand_safety",
     "description": ("Professional B2B tone. No watermarks from other brands, "
                     "no people in unsafe/unprofessional situations, nothing "
                     "that would embarrass an industrial-chemistry company.")},
    {"key": "coherence",
     "description": ("Does this frame look like it belongs to the same video "
                     "as the hero claim (industrial, technical, consistent "
                     "color treatment)?")},
]


def write_rubric(workspace: Path, hero_claim: str = "") -> Path:
    p = Path(workspace) / "rubric.json"
    p.write_text(json.dumps(
        {"hero_claim": hero_claim, "criteria": DEFAULT_CRITERIA},
        indent=2, ensure_ascii=False), encoding="utf-8")
    return p


def load_rubric(workspace: Path) -> dict:
    p = Path(workspace) / "rubric.json"
    if not p.exists():
        return {"hero_claim": "", "criteria": DEFAULT_CRITERIA}
    return json.loads(p.read_text(encoding="utf-8"))
```

- [ ] **Step 4.4: Run tests — verify they pass**

Run: `pytest tests/video_agent/harness/test_rubric.py -v`
Expected: 3 PASS.

---

## Task 5: Vision verifier

**Files:**
- Create: `video_agent/harness/verify_vision.py`
- Create: `tests/video_agent/harness/test_verify_vision.py`

- [ ] **Step 5.1: Write failing tests**

Create `tests/video_agent/harness/test_verify_vision.py`:

```python
"""Vision verifier: frame extraction from scene clips + graded VisionReport
decisions (pass / hold / actionable). The LLM is always mocked."""
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from video_agent.harness.verify_vision import (
    extract_scene_frames, grade_video,
)

FFMPEG = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not on PATH")


def _make_clip(path: Path, seconds: float = 2.0, color: str = "gray"):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [FFMPEG, "-y", "-f", "lavfi",
         "-i", f"color=c={color}:s=1080x1920:d={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True)


class _FakeScene:
    def __init__(self, index, narration="n", on_screen_text="t"):
        self.index = index
        self.narration = narration
        self.on_screen_text = on_screen_text


class _FakeSB:
    def __init__(self, n_scenes):
        self.scenes = [_FakeScene(i, narration=f"narration {i}")
                       for i in range(n_scenes)]
        self.hero_claim = None


def test_extract_scene_frames(tmp_path: Path):
    _make_clip(tmp_path / "scene_clips" / "scene_00.mp4")
    _make_clip(tmp_path / "scene_clips" / "scene_01.mp4")
    frames = extract_scene_frames(tmp_path)
    assert [i for i, _ in frames] == [0, 1]
    for _, f in frames:
        assert f.exists() and f.stat().st_size > 100


def _client_returning(score: float, defects=None):
    client = MagicMock()
    client.generate_json.return_value = {
        "scores": {"visual_match": score, "readability": score,
                   "framing": score, "brand_safety": score,
                   "coherence": score},
        "defects": defects or [],
    }
    return client


def test_all_high_scores_pass(tmp_path: Path):
    _make_clip(tmp_path / "scene_clips" / "scene_00.mp4")
    report = grade_video(_FakeSB(1), tmp_path, client=_client_returning(9))
    assert report.passed is True
    assert report.hold is False
    assert report.scenes[0].overall == 9.0


def test_low_score_is_actionable_not_hold(tmp_path: Path):
    _make_clip(tmp_path / "scene_clips" / "scene_00.mp4")
    client = _client_returning(
        3, defects=[{"code": "visual_mismatch", "detail": "stock photo"}])
    report = grade_video(_FakeSB(1), tmp_path, client=client)
    assert report.passed is False
    assert report.hold is False           # actionable -> revise, not hold
    assert report.scenes[0].defects[0]["code"] == "visual_mismatch"


def test_middle_band_holds(tmp_path: Path):
    _make_clip(tmp_path / "scene_clips" / "scene_00.mp4")
    report = grade_video(_FakeSB(1), tmp_path, client=_client_returning(6))
    assert report.passed is False
    assert report.hold is True            # uncertain -> operator queue


def test_prompt_contains_narration_and_image(tmp_path: Path):
    _make_clip(tmp_path / "scene_clips" / "scene_00.mp4")
    client = _client_returning(9)
    grade_video(_FakeSB(1), tmp_path, client=client)
    kwargs = client.generate_json.call_args.kwargs
    args = client.generate_json.call_args.args
    prompt = args[0] if args else kwargs.get("prompt", "")
    assert "narration 0" in prompt
    assert kwargs["images"] and len(kwargs["images"][0]) > 100  # base64 frame


def test_llm_failure_marks_scene_zero_and_holds(tmp_path: Path):
    from video_agent.ollama_client import OllamaError
    _make_clip(tmp_path / "scene_clips" / "scene_00.mp4")
    client = MagicMock()
    client.generate_json.side_effect = OllamaError("cloud down")
    report = grade_video(_FakeSB(1), tmp_path, client=client)
    assert report.passed is False
    assert report.hold is True            # cannot grade -> never auto-publish
```

- [ ] **Step 5.2: Run tests — verify they fail**

Run: `pytest tests/video_agent/harness/test_verify_vision.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 5.3: Implement**

Create `video_agent/harness/verify_vision.py`:

```python
"""Vision-LLM artifact gate (Phase 3). Extracts one mid-frame per rendered
scene clip and asks the multimodal Gemma to grade it against the scene's
narration and the written rubric. Decision bands:
  every scene overall >= VISION_PASS_MIN          -> passed
  any scene overall  <  VISION_FAIL_BELOW         -> actionable (revise loop)
  otherwise                                        -> hold (operator review)
An ungradeable scene (LLM failure) forces hold — the gate never silently
passes what it could not see."""
from __future__ import annotations
import base64
import json
import logging
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


def _grade_scene(client: OllamaClient, frame: Path, narration: str,
                 on_screen_text: str, rubric: dict) -> SceneGrade | None:
    """Returns SceneGrade, or None when the scene could not be graded."""
    b64 = base64.b64encode(frame.read_bytes()).decode()
    criteria_text = "\n".join(
        f"- {c['key']}: {c['description']}" for c in rubric["criteria"])
    prompt = (
        f"Hero claim of the video: {rubric.get('hero_claim', '')}\n"
        f"Scene narration (what the voiceover says over this frame):\n"
        f"  {narration}\n"
        f"Expected on-screen text: {on_screen_text}\n\n"
        f"Rubric criteria:\n{criteria_text}\n\n"
        "Grade this frame."
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
    return SceneGrade(index=-1, overall=overall, scores=scores,
                      defects=defects)


def grade_video(storyboard, workspace: Path,
                client: OllamaClient | None = None) -> VisionReport:
    workspace = Path(workspace)
    client = client or OllamaClient(model=VISION_MODEL,
                                    timeout=VISION_TIMEOUT_S)
    rubric = load_rubric(workspace)
    frames = extract_scene_frames(workspace)
    scenes_by_index = {s.index: s for s in storyboard.scenes}

    graded: list[SceneGrade] = []
    ungradeable = False
    for idx, frame in frames:
        scene = scenes_by_index.get(idx)
        if scene is None:
            continue
        g = _grade_scene(client, frame, scene.narration,
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
```

- [ ] **Step 5.4: Run tests — verify they pass**

Run: `pytest tests/video_agent/harness/test_verify_vision.py -v`
Expected: 6 PASS.

- [ ] **Step 5.5: Checkpoint** — confirm decision bands: 9→pass, 6→hold, 3→actionable, LLM-error→hold.

---

## Task 6: Revise router

**Files:**
- Create: `video_agent/harness/revise_router.py`
- Create: `tests/video_agent/harness/test_revise_router.py`

- [ ] **Step 6.1: Write failing tests**

Create `tests/video_agent/harness/test_revise_router.py`:

```python
"""Defect -> action routing. Re-source actions are per-scene; re-render is
global and deduplicated (one re-render covers every text defect)."""
from unittest.mock import MagicMock, patch
from video_agent.harness.manifest import SceneGrade, VisionReport
from video_agent.harness.revise_router import route_defects, apply_actions


def _report(*scene_defects):
    scenes = []
    for i, defects in enumerate(scene_defects):
        scenes.append(SceneGrade(
            index=i, overall=3.0,
            defects=[{"code": c, "detail": ""} for c in defects]))
    return VisionReport(passed=False, hold=False, scenes=scenes)


def test_visual_mismatch_routes_to_re_source():
    actions = route_defects(_report(["visual_mismatch"]))
    assert ("re_source", 0) in actions


def test_text_defects_route_to_single_re_render():
    actions = route_defects(_report(["text_clipped"], ["text_unreadable"]))
    assert actions.count(("re_render", None)) == 1


def test_re_source_implies_re_render_not_duplicated():
    actions = route_defects(_report(["visual_mismatch", "text_clipped"]))
    assert ("re_source", 0) in actions
    assert actions.count(("re_render", None)) == 1


def test_low_quality_and_off_brand_re_source():
    actions = route_defects(_report(["low_quality"], ["off_brand"]))
    assert ("re_source", 0) in actions and ("re_source", 1) in actions


def test_unknown_codes_yield_no_actions():
    assert route_defects(_report(["ungradeable"])) == []


def test_apply_actions_re_sources_then_saves(tmp_path):
    sb = MagicMock()
    scene = MagicMock(); scene.index = 0
    sb.scenes = [scene]
    sb.blog = {"category": "mining"}
    sb.narrative_thread = []
    sb.hero_claim = None
    sourcer = MagicMock()
    with patch("video_agent.harness.revise_router.save_storyboard") as save:
        apply_actions([("re_source", 0)], sb, sourcer, tmp_path)
    sourcer.re_source_scene.assert_called_once()
    save.assert_called_once()
```

- [ ] **Step 6.2: Run tests — verify they fail**

Run: `pytest tests/video_agent/harness/test_revise_router.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 6.3: Implement**

Create `video_agent/harness/revise_router.py`:

```python
"""Maps graded vision defects back to the responsible pipeline stage
(spec: 'caption clipped -> re-RENDER; scene image off-topic -> re-source').
Actions are (kind, scene_index|None) tuples; the runner applies them and then
re-renders. Unknown defect codes produce no action — the runner's hold path
covers them."""
from __future__ import annotations
import logging
from pathlib import Path

from video_agent.harness.manifest import VisionReport
from video_agent.storyboard import save_storyboard

log = logging.getLogger(__name__)

# defect code -> action kind
_RE_SOURCE_CODES = {"visual_mismatch", "off_brand", "low_quality"}
_RE_RENDER_CODES = {"text_clipped", "text_unreadable"}

Action = tuple[str, int | None]


def route_defects(report: VisionReport) -> list[Action]:
    actions: list[Action] = []
    needs_render = False
    for grade in report.scenes:
        for d in grade.defects:
            code = d.get("code", "")
            if code in _RE_SOURCE_CODES:
                a = ("re_source", grade.index)
                if a not in actions:
                    actions.append(a)
            elif code in _RE_RENDER_CODES:
                needs_render = True
    # A re-source always flows into the runner's re-render afterwards, so one
    # trailing re_render covers both cases.
    if needs_render or any(k == "re_source" for k, _ in actions):
        actions.append(("re_render", None))
    return actions


def apply_actions(actions: list[Action], storyboard, sourcer,
                  workspace: Path) -> bool:
    """Apply re_source actions to the storyboard in place and persist it.
    Returns True if anything changed (the runner then re-renders)."""
    workspace = Path(workspace)
    changed = False
    scenes_by_index = {s.index: s for s in storyboard.scenes}
    hero = (storyboard.hero_claim.claim_text
            if storyboard.hero_claim else "")
    for kind, idx in actions:
        if kind != "re_source":
            continue
        scene = scenes_by_index.get(idx)
        if scene is None:
            continue
        thread = (storyboard.narrative_thread[idx]
                  if storyboard.narrative_thread
                  and idx < len(storyboard.narrative_thread) else [])
        excluded = ({scene.chosen_asset.url}
                    if scene.chosen_asset else set())
        log.info("revise: re-sourcing scene %d", idx)
        sourcer.re_source_scene(
            scene, storyboard.blog.get("category", ""),
            exclude_urls=excluded, thread_keywords=thread, hero_claim=hero)
        changed = True
    if changed:
        save_storyboard(storyboard, workspace / "storyboard.json")
    # re_render with no re_source still counts as a change (re-render only)
    return changed or any(k == "re_render" for k, _ in actions)
```

- [ ] **Step 6.4: Run tests — verify they pass**

Run: `pytest tests/video_agent/harness/test_revise_router.py -v`
Expected: 6 PASS.

- [ ] **Step 6.5: Checkpoint** — verify `Sourcer.re_source_scene`'s signature matches the call (`scene, category, exclude_urls=, thread_keywords=, hero_claim=`) — it is the same call `Reviser._re_source` makes (`video_agent/agents/reviser.py:89`).

---

## Task 7: Runner integration — VISION phase + bounded revise loop

**Files:**
- Modify: `video_agent/harness/runner.py`
- Create: `tests/video_agent/harness/test_runner_vision.py`

- [ ] **Step 7.1: Write failing tests**

Create `tests/video_agent/harness/test_runner_vision.py`:

```python
"""VISION phase loop: pass-through, revise-then-pass, hold after budget,
hold queue file written."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from video_agent.harness import runner as runner_mod
from video_agent.harness.manifest import (
    new_manifest, save_manifest, VisionReport, SceneGrade,
)


def _manifest(tmp_path, status="verified"):
    m = new_manifest("https://blog.hrsuindore.com/x.html", "x", str(tmp_path))
    m.status = status
    m.video_path = str(tmp_path / "video_short.mp4")
    m.storyboard_path = str(tmp_path / "storyboard.json")
    return m


def _passing():
    return VisionReport(passed=True, scenes=[SceneGrade(0, 9.0)])


def _actionable():
    return VisionReport(passed=False, hold=False, scenes=[
        SceneGrade(0, 3.0, defects=[{"code": "visual_mismatch",
                                     "detail": ""}])])


def _hold():
    return VisionReport(passed=False, hold=True,
                        scenes=[SceneGrade(0, 6.0)])


@pytest.fixture
def patched(tmp_path, monkeypatch):
    """Patch every collaborator of _phase_vision."""
    mocks = {}
    monkeypatch.setattr(runner_mod, "load_storyboard_for_vision",
                        MagicMock(return_value=MagicMock()), raising=False)
    for name in ("grade_video", "route_defects", "apply_actions",
                 "_revise_sourcer", "_phase_render", "verify_heuristic"):
        mocks[name] = MagicMock()
        monkeypatch.setattr(runner_mod, name, mocks[name], raising=False)
    mocks["verify_heuristic"].return_value = MagicMock(passed=True,
                                                       defects=[])
    return mocks


def test_vision_pass_sets_status(tmp_path, patched):
    patched["grade_video"].return_value = _passing()
    m = _manifest(tmp_path)
    runner_mod._phase_vision(m, str(tmp_path), {})
    assert m.status == "vision_verified"
    assert m.vision.passed


def test_actionable_revises_then_passes(tmp_path, patched):
    patched["grade_video"].side_effect = [_actionable(), _passing()]
    patched["route_defects"].return_value = [("re_source", 0),
                                             ("re_render", None)]
    patched["apply_actions"].return_value = True
    m = _manifest(tmp_path)
    runner_mod._phase_vision(m, str(tmp_path), {})
    assert m.status == "vision_verified"
    assert m.vision.cycles_used == 1
    patched["_phase_render"].assert_called_once()


def test_budget_exhausted_holds(tmp_path, patched):
    patched["grade_video"].return_value = _actionable()
    patched["route_defects"].return_value = [("re_render", None)]
    patched["apply_actions"].return_value = True
    m = _manifest(tmp_path)
    runner_mod._phase_vision(m, str(tmp_path), {})
    assert m.status == "hold_for_review"
    # 1 initial + VISION_MAX_REVISE_CYCLES re-grades
    assert patched["grade_video"].call_count == 3


def test_hold_band_holds_immediately_and_writes_queue(tmp_path, patched,
                                                      monkeypatch):
    monkeypatch.setattr(runner_mod, "REVIEW_QUEUE_PATH",
                        str(tmp_path / "review_queue.json"))
    patched["grade_video"].return_value = _hold()
    m = _manifest(tmp_path)
    runner_mod._phase_vision(m, str(tmp_path), {})
    assert m.status == "hold_for_review"
    patched["apply_actions"].assert_not_called()
    queue = json.loads((tmp_path / "review_queue.json").read_text())
    assert queue[0]["run_id"] == m.run_id
```

- [ ] **Step 7.2: Run tests — verify they fail**

Run: `pytest tests/video_agent/harness/test_runner_vision.py -v`
Expected: FAIL — `AttributeError: ... has no attribute '_phase_vision'`.

- [ ] **Step 7.3: Implement the VISION phase**

In `video_agent/harness/runner.py`:

1. Add imports near the existing harness imports:

```python
from video_agent.config import VISION_MAX_REVISE_CYCLES, REVIEW_QUEUE_PATH
from video_agent.harness.verify_vision import grade_video
from video_agent.harness.revise_router import route_defects, apply_actions
from video_agent.harness.rubric import write_rubric
```

2. Extend `_STATUS_ORDER` (order matters):

```python
_STATUS_ORDER = [
    "init",
    "planned",
    "generated",
    "rendered",
    "verified",
    "vision_verified",
    "packaged",
    "published",
]
```

3. In `_phase_plan`, after the history load, emit the rubric (the contract
exists before any artifact):

```python
    # Phase 3: the written grading contract is emitted up front.
    write_rubric(Path(workspace), hero_claim="")
```

4. Add the helpers + phase at the end of the phase-implementations section:

```python
def load_storyboard_for_vision(manifest: RunManifest):
    from video_agent.storyboard import load_storyboard
    return load_storyboard(Path(manifest.storyboard_path))


def _revise_sourcer(workspace: str):
    from video_agent.orchestrator import _build_sourcer
    return _build_sourcer(Path(workspace))


def _append_review_queue(manifest: RunManifest) -> None:
    """Append a hold entry to the operator review queue (best-effort)."""
    import json
    qp = Path(REVIEW_QUEUE_PATH)
    try:
        queue = json.loads(qp.read_text(encoding="utf-8")) if qp.exists() else []
    except Exception:
        queue = []
    queue.append({
        "run_id": manifest.run_id,
        "blog_url": manifest.blog_url,
        "video_path": manifest.video_path,
        "workspace": manifest.workspace,
        "scene_overalls": ([
            {"index": s.index, "overall": s.overall}
            for s in manifest.vision.scenes] if manifest.vision else []),
        "held_at": manifest.updated_at,
    })
    qp.write_text(json.dumps(queue, indent=2, ensure_ascii=False),
                  encoding="utf-8")


def _phase_vision(manifest: RunManifest, workspace: str, history: dict) -> None:
    """VISION phase: grade -> (route -> revise -> re-render -> re-grade)*N.
    Ends in status vision_verified, hold_for_review, or failed."""
    storyboard = load_storyboard_for_vision(manifest)
    cycles = 0
    while True:
        report = grade_video(storyboard, Path(workspace))
        report.cycles_used = cycles
        manifest.vision = report

        if report.passed:
            manifest.status = "vision_verified"
            log.info("[VISION] passed (cycles=%d)", cycles)
            return

        if report.hold:
            manifest.status = "hold_for_review"
            _append_review_queue(manifest)
            log.warning("[VISION] hold for review (uncertain grades)")
            return

        actions = route_defects(report)
        if not actions or cycles >= VISION_MAX_REVISE_CYCLES:
            manifest.status = "hold_for_review"
            _append_review_queue(manifest)
            log.warning("[VISION] hold for review (cycles=%d, actions=%s)",
                        cycles, actions)
            return

        log.info("[VISION] revise cycle %d: %s", cycles + 1, actions)
        sourcer = _revise_sourcer(workspace)
        apply_actions(actions, storyboard, sourcer, Path(workspace))
        _phase_render(manifest, workspace, history)
        heur = verify_heuristic(manifest.video_path, workspace)
        if not heur.passed:
            manifest.status = "failed"
            manifest.last_error = (
                f"re-render failed heuristic gate: {heur.defects}")
            return
        cycles += 1
```

5. Wire it into `HarnessRunner.run` between the VERIFY and PACKAGE blocks:

```python
            # ────────────────────────────────────────────────────────────────
            # PHASE 4.5: VISION (Phase 3 gate; runs only after heuristic pass)
            # ────────────────────────────────────────────────────────────────
            if _status_index(manifest.status) <= _status_index("verified"):
                log.info("[VISION] Starting...")
                try:
                    history = _load_history_record(blog_url)
                    _phase_vision(manifest, str(workspace), history)
                    save_manifest(manifest, str(manifest_path))
                    if manifest.status != "vision_verified":
                        log.warning("[VISION] stopped: status=%s",
                                    manifest.status)
                        return manifest
                    log.info("[VISION] Complete -> status=vision_verified")
                except Exception as e:
                    log.error("[VISION] Failed: %s", e, exc_info=True)
                    manifest.status = "failed"
                    manifest.last_error = str(e)
                    save_manifest(manifest, str(manifest_path))
                    return manifest
```

6. Update the two downstream gates to chain from the new status:
   - PACKAGE: `if publish and _status_index(manifest.status) <= _status_index("vision_verified"):`
   - The `resume`-from-failed shortcut (`manifest.status = "rendered"`) stays as is — a resumed failed run re-enters at VERIFY then VISION.

- [ ] **Step 7.4: Run tests — verify they pass**

Run: `pytest tests/video_agent/harness/test_runner_vision.py tests/video_agent/harness/test_runner.py -v`
Expected: all PASS (new vision tests + existing runner tests unchanged).

- [ ] **Step 7.5: Checkpoint** — read the diff of `runner.py`; confirm: VISION runs for *both* `publish=True` and `publish=False` paths (quality gate is unconditional), hold never proceeds to PACKAGE, and `dry_run` semantics are untouched.

---

## Task 8: Full suite + live end-to-end

- [ ] **Step 8.1: Full suite**

Run: `pytest tests/ -q`
Expected: all green (416 pre-existing + ~20 new).

- [ ] **Step 8.2: Live end-to-end with the cloud model (manual; requires Ollama running)**

Run: `python scripts/publish_video.py https://blog.hrsuindore.com/2026/02/optimizing-early-age-strength.html --dry-run`

Expected outcomes (any of these is a *correct* Phase 3 behavior):
- `status=verified` at the end of a dry-run with the VISION phase logged as passed, OR
- `status=hold_for_review` with an entry in `review_queue.json` and per-scene
  scores in `run_manifest.json` → inspect `_vision_frames/*.jpg` to confirm the
  grader's judgment was reasonable, OR
- a revise cycle in the log (`[VISION] revise cycle 1: [('re_source', N), ...]`)
  followed by a re-render and a second grade.

What would be a *bug*: VISION passing scenes whose frame obviously has nothing
to do with the narration (compare `_vision_frames/scene_XX.jpg` against the
scene narration in `storyboard.json`).

- [ ] **Step 8.3: Checkpoint** — `run_manifest.json` has a `vision` block with per-scene `scores` and `defects`; statuses progress `verified → vision_verified → packaged` on the happy path.

---

## Out of scope (unchanged from the design spec)

- Phase 2 (queue runner, quota caps, go-public switch) — next after this.
- Phase 4 (generator re-architecture, deleting JSON-repair shims).
- LinkedIn publisher.
- A UI for the review queue — `review_queue.json` is consumed manually for now.
- The spec's third routing example ("flat hook → re-cinematograph hook"): a
  static frame cannot evidence flat *motion*, so a frame-grader can't emit that
  defect reliably. Deferred until the verifier samples multiple frames per
  scene; re-source + re-render cover the defects a single frame can prove.

## Risks

- **Cloud model latency/availability**: every grade is a cloud call (5 scenes ×
  up to 3 grading rounds). Mitigated by `VISION_TIMEOUT_S` and the rule that an
  ungradeable run holds rather than fails or passes.
- **Grader leniency**: if Gemma grades generic stock ≥7 on `visual_match`, the
  gate is toothless. The system prompt explicitly instructs harshness on stock
  imagery; tune `VISION_PASS_MIN` upward if live runs show leniency.
- **Revise loop cost**: each cycle re-renders (~90 s) and re-grades. Bounded at
  2 cycles by config.
