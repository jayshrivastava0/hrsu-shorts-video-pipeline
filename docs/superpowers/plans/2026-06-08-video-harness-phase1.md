# Video Harness Phase 1 — Publish Path + Harness Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wrap the existing video generator in a deterministic, resumable harness that renders a short, gates it with no-model heuristic checks, packages YouTube metadata, and auto-uploads it as **unlisted** via the YouTube Data API.

**Architecture:** A `RunManifest` (durable JSON state) flows through a `HarnessRunner` phase state-machine (`PLAN → GENERATE → RENDER → VERIFY → PACKAGE → PUBLISH`). The generator (`orchestrator.build_storyboard`) and renderer (`compose_short_v2`) are reused unchanged. New leaf modules implement verification, packaging, and publishing. `make_video.py` becomes `publish=False` (render+verify only); a new `publish_video.py` runs the full path.

**Tech Stack:** Python 3.11+, pytest, ffmpeg/ffprobe (via `ffmpeg-python` + subprocess), `pydub` (audio RMS), `pillow`/`numpy` (frame analysis), `pytesseract` (caption OCR), `google-api-python-client` + `google-auth-oauthlib` (YouTube), gemma cloud via `OllamaClient` (metadata copy).

**Project conventions:**
- **No git.** Do NOT run any git command. "Checkpoint" steps = verify the diff visually, then continue.
- Tests live under `tests/video_agent/...` mirroring the source tree.
- Run one test: `pytest tests/video_agent/harness/test_manifest.py::test_name -v`
- Run full suite: `pytest tests/ -x -q`

---

## File Structure

**New package:** `video_agent/harness/`
- `video_agent/harness/__init__.py` — package marker
- `video_agent/harness/manifest.py` — `RunManifest` + nested dataclasses + save/load (State subsystem)
- `video_agent/harness/verify_heuristic.py` — no-model artifact gate (Verification subsystem)
- `video_agent/harness/runner.py` — `HarnessRunner` phase state-machine (Lifecycle subsystem)

**New publishers:**
- `video_agent/publishers/youtube_packager.py` — `PublishPackage` builder
- `video_agent/publishers/youtube_publisher.py` — YouTube Data API upload

**Modified:**
- `video_agent/config.py` — YouTube + verify knobs (append only)
- `scripts/make_video.py` — call `HarnessRunner.run(url, publish=False)`

**New script:**
- `scripts/publish_video.py` — `HarnessRunner.run(url, publish=True)`

**Tests:**
- `tests/video_agent/harness/__init__.py`
- `tests/video_agent/harness/test_manifest.py`
- `tests/video_agent/harness/test_verify_heuristic.py`
- `tests/video_agent/harness/test_runner.py`
- `tests/video_agent/publishers/__init__.py`
- `tests/video_agent/publishers/test_youtube_packager.py`
- `tests/video_agent/publishers/test_youtube_publisher.py`

---

## Task 1: Config knobs

**Files:**
- Modify: `video_agent/config.py` (append at end)

- [ ] **Step 1.1: Append YouTube + verify config**

Add to the end of `video_agent/config.py`:

```python
# ─── Harness: artifact verification ────────────────────────────────────────
# Reuse SHORT_FORMAT for duration/size bounds; these are the gate-specific knobs.
VERIFY_AUDIO_RMS_FLOOR = 250.0      # pydub RMS below this == effectively silent
VERIFY_AUDIO_PEAK_CEIL = 32500      # 16-bit peak above this == clipping risk (max 32768)
VERIFY_FRAME_SAMPLES = 5            # frames sampled across the video for visual checks
VERIFY_DARK_RIBBON_STRIP_PX = 120   # bottom strip height inspected for a dark band
VERIFY_DARK_RIBBON_LUMA_MAX = 24    # mean luma below this over the strip == dark ribbon
VERIFY_SAFEZONE_MARGIN_FRAC = 0.06  # caption text must sit inside this margin (6%)

# ─── Harness: YouTube publishing ───────────────────────────────────────────
YOUTUBE_UPLOAD_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]
YOUTUBE_CLIENT_SECRETS = "client_secrets.json"   # reuse Blogger app secrets
YOUTUBE_TOKEN_PATH = "youtube_token.json"        # SEPARATE from blogger token
YOUTUBE_CATEGORY_ID = "28"                        # Science & Technology
YOUTUBE_DEFAULT_PRIVACY = "unlisted"              # Phase 1: never public
YOUTUBE_TITLE_MAX = 100
YOUTUBE_DESC_MAX = 4900                            # API hard limit is 5000
```

- [ ] **Step 1.2: Verify it imports**

Run: `python -c "import video_agent.config as c; print(c.YOUTUBE_DEFAULT_PRIVACY, c.VERIFY_AUDIO_RMS_FLOOR)"`
Expected: `unlisted 250.0`

- [ ] **Step 1.3: Checkpoint** — confirm only appended lines changed in `config.py`.

---

## Task 2: RunManifest (State subsystem)

**Files:**
- Create: `video_agent/harness/__init__.py`
- Create: `video_agent/harness/manifest.py`
- Create: `tests/video_agent/harness/__init__.py`
- Create: `tests/video_agent/harness/test_manifest.py`

- [ ] **Step 2.1: Create package markers**

Create `video_agent/harness/__init__.py` (empty file).
Create `tests/video_agent/harness/__init__.py` (empty file).

- [ ] **Step 2.2: Write failing tests**

Create `tests/video_agent/harness/test_manifest.py`:

```python
"""Tests for RunManifest save/load round-trip and status transitions."""
from pathlib import Path
from video_agent.harness.manifest import (
    RunManifest, VerifyReport, PublishPackage, PublishResult,
    new_manifest, save_manifest, load_manifest,
)


def test_new_manifest_defaults():
    m = new_manifest(blog_url="https://blog.hrsuindore.com/x.html",
                     slug="x", workspace="output/videos/x")
    assert m.version == "1.0"
    assert m.status == "init"
    assert m.slug == "x"
    assert m.run_id          # non-empty
    assert m.attempts == 0
    assert m.verify is None and m.package is None and m.publish is None


def test_roundtrip_with_nested(tmp_path: Path):
    m = new_manifest(blog_url="u", slug="s", workspace=str(tmp_path))
    m.status = "verified"
    m.video_path = str(tmp_path / "video_short.mp4")
    m.verify = VerifyReport(passed=True,
                            checks={"duration_s": 47.2, "audio_rms_ok": True},
                            defects=[])
    m.package = PublishPackage(
        title="T", description="D", tags=["a", "b"], category_id="28",
        thumbnail_path="t.png", caption_srt_path="s.srt",
        privacy_status="unlisted")
    m.publish = PublishResult(platform="youtube", video_id="vid123",
                              url="https://youtu.be/vid123",
                              visibility="unlisted", uploaded_at="2026-06-08T00:00:00Z")

    p = tmp_path / "run_manifest.json"
    save_manifest(m, p)
    loaded = load_manifest(p)

    assert loaded.status == "verified"
    assert loaded.verify.passed is True
    assert loaded.verify.checks["duration_s"] == 47.2
    assert loaded.package.tags == ["a", "b"]
    assert loaded.publish.video_id == "vid123"


def test_roundtrip_all_none(tmp_path: Path):
    m = new_manifest(blog_url="u", slug="s", workspace=str(tmp_path))
    p = tmp_path / "m.json"
    save_manifest(m, p)
    loaded = load_manifest(p)
    assert loaded.verify is None
    assert loaded.package is None
    assert loaded.publish is None
    assert loaded.status == "init"
```

- [ ] **Step 2.3: Run tests — verify they fail**

Run: `pytest tests/video_agent/harness/test_manifest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'video_agent.harness.manifest'`

- [ ] **Step 2.4: Implement manifest**

Create `video_agent/harness/manifest.py`:

```python
"""RunManifest — the durable, resumable state object for one video run.

Mirrors video_agent.storyboard's asdict->JSON save/load convention. Persisted
as run_manifest.json in the run workspace and checkpointed after every phase so
a crashed run can resume from the last completed phase."""
from __future__ import annotations
import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

RunStatus = Literal["init", "planned", "generated", "rendered", "verified",
                    "packaged", "published", "failed"]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class VerifyReport:
    passed: bool
    checks: dict[str, Any] = field(default_factory=dict)
    defects: list[str] = field(default_factory=list)


@dataclass
class PublishPackage:
    title: str
    description: str
    tags: list[str]
    category_id: str
    thumbnail_path: str
    caption_srt_path: str
    privacy_status: str


@dataclass
class PublishResult:
    platform: str
    video_id: str
    url: str
    visibility: str
    uploaded_at: str


@dataclass
class RunManifest:
    version: str
    run_id: str
    blog_url: str
    slug: str
    status: RunStatus
    workspace: str
    storyboard_path: str | None = None
    video_path: str | None = None
    srt_path: str | None = None
    voice_path: str | None = None
    verify: VerifyReport | None = None
    package: PublishPackage | None = None
    publish: PublishResult | None = None
    attempts: int = 0
    last_error: str | None = None
    created_at: str = ""
    updated_at: str = ""


def new_manifest(blog_url: str, slug: str, workspace: str) -> RunManifest:
    ts = _now()
    return RunManifest(
        version="1.0", run_id=uuid.uuid4().hex[:12], blog_url=blog_url,
        slug=slug, status="init", workspace=workspace,
        created_at=ts, updated_at=ts,
    )


def save_manifest(m: RunManifest, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    m.updated_at = _now()
    path.write_text(json.dumps(asdict(m), indent=2, ensure_ascii=False),
                    encoding="utf-8")


def load_manifest(path: Path) -> RunManifest:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    v = d.get("verify")
    pkg = d.get("package")
    pub = d.get("publish")
    return RunManifest(
        version=d["version"], run_id=d["run_id"], blog_url=d["blog_url"],
        slug=d["slug"], status=d["status"], workspace=d["workspace"],
        storyboard_path=d.get("storyboard_path"),
        video_path=d.get("video_path"), srt_path=d.get("srt_path"),
        voice_path=d.get("voice_path"),
        verify=VerifyReport(**v) if v else None,
        package=PublishPackage(**pkg) if pkg else None,
        publish=PublishResult(**pub) if pub else None,
        attempts=d.get("attempts", 0), last_error=d.get("last_error"),
        created_at=d.get("created_at", ""), updated_at=d.get("updated_at", ""),
    )
```

- [ ] **Step 2.5: Run tests — verify they pass**

Run: `pytest tests/video_agent/harness/test_manifest.py -v`
Expected: 3 PASS.

- [ ] **Step 2.6: Checkpoint** — confirm `manifest.py` matches; nested optionals reconstruct.

---

## Task 3: Heuristic verification gate (Verification subsystem)

**Files:**
- Create: `video_agent/harness/verify_heuristic.py`
- Create: `tests/video_agent/harness/test_verify_heuristic.py`

- [ ] **Step 3.1: Write failing tests (with ffmpeg-generated fixtures)**

Create `tests/video_agent/harness/test_verify_heuristic.py`:

```python
"""Tests for the no-model heuristic artifact gate using ffmpeg-generated MP4s."""
import shutil
import subprocess
import pytest
from pathlib import Path
from video_agent.harness.verify_heuristic import verify_video

FFMPEG = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not on PATH")


def _make_mp4(path: Path, *, seconds: int, silent: bool, dark_bottom: bool):
    """Render a 1080x1920 test clip with controllable audio/visual properties."""
    if dark_bottom:
        # top half grey, bottom half pure black (simulates a dark ribbon)
        vsrc = ("color=c=gray:s=1080x960:d=%d[top];"
                "color=c=black:s=1080x960:d=%d[bot];"
                "[top][bot]vstack" % (seconds, seconds))
        vin = ["-f", "lavfi", "-i", vsrc]
    else:
        vin = ["-f", "lavfi", "-i", f"color=c=gray:s=1080x1920:d={seconds}"]
    if silent:
        ain = ["-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo:d={seconds}"]
    else:
        ain = ["-f", "lavfi", "-i", f"sine=frequency=440:r=44100:d={seconds}"]
    cmd = [FFMPEG, "-y", *vin, *ain, "-shortest",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)


def test_good_video_passes(tmp_path: Path):
    v = tmp_path / "good.mp4"
    _make_mp4(v, seconds=40, silent=False, dark_bottom=False)
    report = verify_video(v)
    assert report.passed is True, report.defects
    assert 39 <= report.checks["duration_s"] <= 41


def test_too_short_fails(tmp_path: Path):
    v = tmp_path / "short.mp4"
    _make_mp4(v, seconds=5, silent=False, dark_bottom=False)
    report = verify_video(v)
    assert report.passed is False
    assert any("duration" in d.lower() for d in report.defects)


def test_silent_audio_fails(tmp_path: Path):
    v = tmp_path / "silent.mp4"
    _make_mp4(v, seconds=40, silent=True, dark_bottom=False)
    report = verify_video(v)
    assert report.passed is False
    assert any("silent" in d.lower() or "audio" in d.lower() for d in report.defects)


def test_dark_ribbon_fails(tmp_path: Path):
    v = tmp_path / "ribbon.mp4"
    _make_mp4(v, seconds=40, silent=False, dark_bottom=True)
    report = verify_video(v)
    assert report.passed is False
    assert any("ribbon" in d.lower() or "dark" in d.lower() for d in report.defects)
```

- [ ] **Step 3.2: Run tests — verify they fail**

Run: `pytest tests/video_agent/harness/test_verify_heuristic.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'video_agent.harness.verify_heuristic'`

- [ ] **Step 3.3: Implement the gate**

Create `video_agent/harness/verify_heuristic.py`:

```python
"""No-model artifact gate. Inspects the rendered MP4 with deterministic checks:
duration/streams/filesize (ffprobe), audio level (pydub), dark-ribbon and
caption safe-zone (sampled frames). Returns a VerifyReport; never raises on a
content defect — it records the defect. Missing OCR engine is a soft-skip, not
a failure."""
from __future__ import annotations
import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from video_agent.config import (
    SHORT_FORMAT, VERIFY_AUDIO_RMS_FLOOR, VERIFY_AUDIO_PEAK_CEIL,
    VERIFY_FRAME_SAMPLES, VERIFY_DARK_RIBBON_STRIP_PX,
    VERIFY_DARK_RIBBON_LUMA_MAX, VERIFY_SAFEZONE_MARGIN_FRAC,
)
from video_agent.harness.manifest import VerifyReport

log = logging.getLogger(__name__)
_FFPROBE = shutil.which("ffprobe")
_FFMPEG = shutil.which("ffmpeg")


def _ffprobe_json(video: Path) -> dict:
    out = subprocess.run(
        [_FFPROBE, "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(video)],
        check=True, capture_output=True, text=True,
    ).stdout
    return json.loads(out)


def _extract_frames(video: Path, n: int, dest: Path) -> list[Path]:
    """Grab n evenly-spaced frames as PNGs via ffmpeg fps filter."""
    dur = max(1.0, _probe_duration(video))
    rate = n / dur
    pattern = dest / "frame_%03d.png"
    subprocess.run(
        [_FFMPEG, "-y", "-i", str(video), "-vf", f"fps={rate}",
         "-vframes", str(n), str(pattern)],
        check=True, capture_output=True,
    )
    return sorted(dest.glob("frame_*.png"))


def _probe_duration(video: Path) -> float:
    try:
        info = _ffprobe_json(video)
        return float(info.get("format", {}).get("duration", 0.0))
    except Exception:
        return 0.0


def verify_video(video: Path) -> VerifyReport:
    video = Path(video)
    checks: dict = {}
    defects: list[str] = []

    if _FFPROBE is None or _FFMPEG is None:
        return VerifyReport(passed=False, checks={},
                            defects=["ffmpeg/ffprobe not on PATH"])
    if not video.exists() or video.stat().st_size == 0:
        return VerifyReport(passed=False, checks={},
                            defects=[f"missing or empty file: {video}"])

    # 1. ffprobe: duration / streams / filesize / resolution
    info = _ffprobe_json(video)
    streams = info.get("streams", [])
    has_video = any(s.get("codec_type") == "video" for s in streams)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    duration = float(info.get("format", {}).get("duration", 0.0))
    size_mb = video.stat().st_size / 1_048_576

    checks["duration_s"] = round(duration, 1)
    checks["has_video"] = has_video
    checks["has_audio"] = has_audio
    checks["size_mb"] = round(size_mb, 1)

    if not (SHORT_FORMAT["min_duration_s"] <= duration <= SHORT_FORMAT["max_duration_s"]):
        defects.append(
            f"duration {duration:.1f}s outside "
            f"[{SHORT_FORMAT['min_duration_s']},{SHORT_FORMAT['max_duration_s']}]s")
    if not has_video:
        defects.append("no video stream")
    if not has_audio:
        defects.append("no audio stream")
    if size_mb > SHORT_FORMAT["max_filesize_mb"]:
        defects.append(f"filesize {size_mb:.1f}MB exceeds "
                       f"{SHORT_FORMAT['max_filesize_mb']}MB")

    vstream = next((s for s in streams if s.get("codec_type") == "video"), {})
    w, h = vstream.get("width"), vstream.get("height")
    checks["resolution"] = f"{w}x{h}"
    if (w, h) != tuple(SHORT_FORMAT["resolution"]):
        defects.append(f"resolution {w}x{h} != {SHORT_FORMAT['resolution']}")

    # 2. audio level (pydub)
    if has_audio:
        rms_ok, peak_ok, rms = _check_audio(video)
        checks["audio_rms"] = rms
        checks["audio_rms_ok"] = rms_ok
        if not rms_ok:
            defects.append(f"audio effectively silent (rms={rms:.0f} < "
                           f"{VERIFY_AUDIO_RMS_FLOOR})")
        if not peak_ok:
            defects.append("audio peak indicates clipping")

    # 3 + 4. frame-based checks (dark ribbon + safe-zone OCR)
    with tempfile.TemporaryDirectory() as td:
        frames = _extract_frames(video, VERIFY_FRAME_SAMPLES, Path(td))
        if frames:
            ribbon = _dark_ribbon(frames)
            checks["dark_ribbon"] = ribbon
            if ribbon:
                defects.append("dark ribbon detected at bottom strip")
            safe, note = _safezone_ok(frames)
            checks["safezone"] = note
            if safe is False:
                defects.append(f"caption outside safe zone ({note})")

    report = VerifyReport(passed=(len(defects) == 0), checks=checks, defects=defects)
    log.info("verify_video: passed=%s defects=%s", report.passed, defects)
    return report


def _check_audio(video: Path) -> tuple[bool, bool, float]:
    from pydub import AudioSegment
    seg = AudioSegment.from_file(video)
    rms = float(seg.rms)
    peak = seg.max
    return (rms >= VERIFY_AUDIO_RMS_FLOOR, peak <= VERIFY_AUDIO_PEAK_CEIL, rms)


def _luma(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("L"), dtype=np.float32)


def _dark_ribbon(frames: list[Path]) -> bool:
    """True if every sampled frame's bottom strip is near-black (a solid band)."""
    strip = VERIFY_DARK_RIBBON_STRIP_PX
    for f in frames:
        arr = _luma(Image.open(f))
        bottom = arr[-strip:, :]
        if bottom.mean() >= VERIFY_DARK_RIBBON_LUMA_MAX:
            return False   # at least one frame has content in the strip
    return True


def _safezone_ok(frames: list[Path]) -> tuple[bool | None, str]:
    """Return (ok, note). None == soft-skip (OCR engine unavailable)."""
    try:
        import pytesseract
        from pytesseract import Output
    except Exception:
        return None, "ocr-unavailable"
    margin = VERIFY_SAFEZONE_MARGIN_FRAC
    for f in frames:
        img = Image.open(f)
        W, H = img.size
        try:
            data = pytesseract.image_to_data(img, output_type=Output.DICT)
        except Exception:
            return None, "ocr-engine-missing"
        for i, txt in enumerate(data["text"]):
            if not txt.strip() or int(data["conf"][i]) < 60:
                continue
            x, y, w, h = (data["left"][i], data["top"][i],
                          data["width"][i], data["height"][i])
            if (x < margin * W or y < margin * H or
                    x + w > (1 - margin) * W or y + h > (1 - margin) * H):
                return False, f"text@({x},{y},{w},{h})"
    return True, "ok"
```

- [ ] **Step 3.4: Run tests — verify they pass**

Run: `pytest tests/video_agent/harness/test_verify_heuristic.py -v`
Expected: 4 PASS (or SKIP all if ffmpeg absent — acceptable on a machine without ffmpeg, but the dev box has it).

- [ ] **Step 3.5: Checkpoint** — confirm OCR missing-engine path soft-skips (returns `None`), never fails the gate.

---

## Task 4: YouTube packager

**Files:**
- Create: `video_agent/publishers/youtube_packager.py`
- Create: `tests/video_agent/publishers/__init__.py`
- Create: `tests/video_agent/publishers/test_youtube_packager.py`

- [ ] **Step 4.1: Create test package marker**

Create `tests/video_agent/publishers/__init__.py` (empty file).

- [ ] **Step 4.2: Write failing tests**

Create `tests/video_agent/publishers/test_youtube_packager.py`:

```python
"""Tests for YouTube metadata packaging — deterministic validation around the
optional LLM copy step."""
from pathlib import Path
from unittest.mock import MagicMock
from video_agent.storyboard import Storyboard, HeroClaim
from video_agent.publishers.youtube_packager import build_package


def _sb() -> Storyboard:
    sb = Storyboard(version="2.0",
                    blog={"region": "australia", "category": "mining",
                          "title": "Calcium Nitrate in AMD Control"})
    sb.hero_claim = HeroClaim(stat="$2B/yr",
                              claim_text="Calcium nitrate cuts acid mine drainage costs")
    return sb


def test_package_required_fields(tmp_path: Path):
    thumb = tmp_path / "thumb.png"; thumb.write_bytes(b"x")
    srt = tmp_path / "subs.srt"; srt.write_text("1\n", encoding="utf-8")
    pkg = build_package(_sb(), thumbnail_path=thumb, caption_srt_path=srt,
                        client=None)
    assert pkg.category_id == "28"
    assert pkg.privacy_status == "unlisted"
    assert pkg.caption_srt_path == str(srt)
    assert pkg.thumbnail_path == str(thumb)
    assert "#Shorts" in pkg.description
    assert "hrsuindore.com" in pkg.description
    assert pkg.tags                      # non-empty


def test_title_truncated_to_100(tmp_path: Path):
    thumb = tmp_path / "t.png"; thumb.write_bytes(b"x")
    srt = tmp_path / "s.srt"; srt.write_text("1", encoding="utf-8")
    client = MagicMock()
    client.generate_json.return_value = {"title": "A" * 250, "description": "D"}
    pkg = build_package(_sb(), thumbnail_path=thumb, caption_srt_path=srt,
                        client=client)
    assert len(pkg.title) <= 100


def test_banned_phrase_stripped(tmp_path: Path):
    thumb = tmp_path / "t.png"; thumb.write_bytes(b"x")
    srt = tmp_path / "s.srt"; srt.write_text("1", encoding="utf-8")
    client = MagicMock()
    client.generate_json.return_value = {
        "title": "Thanks for watching our mining video",
        "description": "Hope you enjoyed this clip",
    }
    pkg = build_package(_sb(), thumbnail_path=thumb, caption_srt_path=srt,
                        client=client)
    assert "thanks for watching" not in pkg.title.lower()
    assert "hope you enjoyed" not in pkg.description.lower()


def test_llm_failure_falls_back(tmp_path: Path):
    from video_agent.ollama_client import OllamaError
    thumb = tmp_path / "t.png"; thumb.write_bytes(b"x")
    srt = tmp_path / "s.srt"; srt.write_text("1", encoding="utf-8")
    client = MagicMock()
    client.generate_json.side_effect = OllamaError("down")
    pkg = build_package(_sb(), thumbnail_path=thumb, caption_srt_path=srt,
                        client=client)
    # Falls back to hero-claim-derived title; still valid.
    assert pkg.title
    assert len(pkg.title) <= 100
    assert "hrsuindore.com" in pkg.description
```

- [ ] **Step 4.3: Run tests — verify they fail**

Run: `pytest tests/video_agent/publishers/test_youtube_packager.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'video_agent.publishers.youtube_packager'`

- [ ] **Step 4.4: Implement the packager**

Create `video_agent/publishers/youtube_packager.py`:

```python
"""Builds YouTube Shorts metadata (PublishPackage) from a Storyboard. LLM copy
is optional and always wrapped in deterministic validation: char limits, banned
phrases, required CTA + hashtags. Never raises on LLM failure — falls back to
hero-claim-derived copy."""
from __future__ import annotations
import logging

from video_agent.config import (
    MAIN_WEBSITE, YOUTUBE_CATEGORY_ID, YOUTUBE_DEFAULT_PRIVACY,
    YOUTUBE_TITLE_MAX, YOUTUBE_DESC_MAX, SCRIPT_BANNED_PHRASES,
)
from video_agent.ollama_client import OllamaClient, OllamaError
from video_agent.storyboard import Storyboard
from video_agent.harness.manifest import PublishPackage

log = logging.getLogger(__name__)

_SYSTEM = (
    "You write YouTube Shorts metadata for a B2B industrial-chemistry channel. "
    "Audience: procurement managers. No fluff, no 'thanks for watching'. "
    'Respond ONLY with raw JSON: {"title":"...","description":"..."} . '
    f"Title <= {YOUTUBE_TITLE_MAX} chars, keyword-front-loaded."
)


def _regional_hashtags(region: str, category: str) -> list[str]:
    base = ["#Shorts", "#CalciumNitrate", "#IndustrialChemistry"]
    region_tag = {"australia": "#Mining", "usa": "#WaterTreatment",
                  "eu": "#REACH", "germany": "#Industrie",
                  "east_asia": "#Manufacturing", "gulf": "#OilAndGas"}.get(region)
    if region_tag:
        base.append(region_tag)
    if category:
        base.append("#" + category.replace("_", "").title())
    return base


def _strip_banned(text: str) -> str:
    low = text.lower()
    for phrase in SCRIPT_BANNED_PHRASES:
        if phrase in low:
            # remove the offending sentence fragment containing the phrase
            idx = low.find(phrase)
            text = (text[:idx] + text[idx + len(phrase):]).strip()
            low = text.lower()
    return text.strip(" .,-")


def _fallback_title(sb: Storyboard) -> str:
    claim = sb.hero_claim.claim_text if sb.hero_claim else sb.blog.get("title", "HRSU")
    return claim[:YOUTUBE_TITLE_MAX]


def build_package(sb: Storyboard, *, thumbnail_path, caption_srt_path,
                  client: OllamaClient | None = None) -> PublishPackage:
    region = sb.blog.get("region", "default")
    category = sb.blog.get("category", "")
    hashtags = _regional_hashtags(region, category)

    title = _fallback_title(sb)
    summary = sb.hero_claim.claim_text if sb.hero_claim else sb.blog.get("title", "")

    if client is not None:
        prompt = (
            f"Hero claim: {summary}\n"
            f"Region: {region} | Category: {category}\n"
            f"Blog title: {sb.blog.get('title','')}\n"
            "Write the title and a 2-sentence description."
        )
        try:
            out = client.generate_json(prompt, system=_SYSTEM)
            if isinstance(out, dict):
                title = (out.get("title") or title)
                summary = (out.get("description") or summary)
        except OllamaError as e:
            log.warning("packager: LLM failed (%s); using fallback copy", e)

    title = _strip_banned(title)[:YOUTUBE_TITLE_MAX] or _fallback_title(sb)
    summary = _strip_banned(summary)

    description = (
        f"{summary}\n\n"
        f"Learn more at {MAIN_WEBSITE}\n\n"
        + " ".join(hashtags)
    )[:YOUTUBE_DESC_MAX]

    tags = [h.lstrip("#") for h in hashtags]

    return PublishPackage(
        title=title, description=description, tags=tags,
        category_id=YOUTUBE_CATEGORY_ID, thumbnail_path=str(thumbnail_path),
        caption_srt_path=str(caption_srt_path),
        privacy_status=YOUTUBE_DEFAULT_PRIVACY,
    )
```

- [ ] **Step 4.5: Run tests — verify they pass**

Run: `pytest tests/video_agent/publishers/test_youtube_packager.py -v`
Expected: 4 PASS.

- [ ] **Step 4.6: Checkpoint** — confirm `MAIN_WEBSITE` import resolves (it is re-exported by `video_agent/config.py` from the root `config.py`).

---

## Task 5: YouTube publisher

**Files:**
- Create: `video_agent/publishers/youtube_publisher.py`
- Create: `tests/video_agent/publishers/test_youtube_publisher.py`

- [ ] **Step 5.1: Write failing tests (googleapiclient fully mocked)**

Create `tests/video_agent/publishers/test_youtube_publisher.py`:

```python
"""Tests for YouTubePublisher with a fully mocked Google API service — no auth,
no network."""
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from video_agent.harness.manifest import PublishPackage
from video_agent.publishers.youtube_publisher import YouTubePublisher


def _pkg(tmp_path: Path) -> PublishPackage:
    thumb = tmp_path / "t.png"; thumb.write_bytes(b"x")
    srt = tmp_path / "s.srt"; srt.write_text("1\n", encoding="utf-8")
    return PublishPackage(title="T", description="D", tags=["a"],
                          category_id="28", thumbnail_path=str(thumb),
                          caption_srt_path=str(srt), privacy_status="unlisted")


def _fake_service():
    svc = MagicMock()
    # videos().insert(...).next_chunk() loop -> returns (status, response)
    insert_req = MagicMock()
    insert_req.next_chunk.return_value = (None, {"id": "vid999"})
    svc.videos.return_value.insert.return_value = insert_req
    # thumbnails().set(...).execute() and captions().insert(...).execute()
    svc.thumbnails.return_value.set.return_value.execute.return_value = {}
    svc.captions.return_value.insert.return_value.execute.return_value = {}
    return svc


def test_upload_returns_result(tmp_path: Path):
    video = tmp_path / "v.mp4"; video.write_bytes(b"x" * 10)
    pub = YouTubePublisher(service=_fake_service())
    res = pub.upload(_pkg(tmp_path), video_path=video)
    assert res.platform == "youtube"
    assert res.video_id == "vid999"
    assert res.url == "https://youtu.be/vid999"
    assert res.visibility == "unlisted"
    assert res.uploaded_at


def test_upload_sets_unlisted_privacy(tmp_path: Path):
    video = tmp_path / "v.mp4"; video.write_bytes(b"x" * 10)
    svc = _fake_service()
    pub = YouTubePublisher(service=svc)
    pub.upload(_pkg(tmp_path), video_path=video)
    _, kwargs = svc.videos.return_value.insert.call_args
    body = kwargs["body"]
    assert body["status"]["privacyStatus"] == "unlisted"
    assert body["snippet"]["title"] == "T"
    assert body["snippet"]["categoryId"] == "28"


def test_dry_run_does_not_call_insert(tmp_path: Path):
    video = tmp_path / "v.mp4"; video.write_bytes(b"x" * 10)
    svc = _fake_service()
    pub = YouTubePublisher(service=svc)
    res = pub.upload(_pkg(tmp_path), video_path=video, dry_run=True)
    svc.videos.return_value.insert.assert_not_called()
    assert res.video_id == "DRYRUN"
```

- [ ] **Step 5.2: Run tests — verify they fail**

Run: `pytest tests/video_agent/publishers/test_youtube_publisher.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'video_agent.publishers.youtube_publisher'`

- [ ] **Step 5.3: Implement the publisher**

Create `video_agent/publishers/youtube_publisher.py`:

```python
"""YouTubePublisher — uploads a rendered short via the YouTube Data API v3 as
unlisted, then sets the thumbnail and inserts captions. OAuth is lazy: a service
is built from client_secrets.json + a SEPARATE youtube_token.json only when no
service is injected (tests inject a mock)."""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from pathlib import Path

from video_agent.config import (
    YOUTUBE_UPLOAD_SCOPES, YOUTUBE_CLIENT_SECRETS, YOUTUBE_TOKEN_PATH,
)
from video_agent.harness.manifest import PublishPackage, PublishResult

log = logging.getLogger(__name__)


def _build_service():
    """Build an authenticated YouTube Data API client. Imports google libs
    lazily so tests that inject a mock never touch OAuth."""
    import os
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = None
    if os.path.exists(YOUTUBE_TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(
            YOUTUBE_TOKEN_PATH, YOUTUBE_UPLOAD_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                YOUTUBE_CLIENT_SECRETS, YOUTUBE_UPLOAD_SCOPES)
            creds = flow.run_local_server(port=0)
        Path(YOUTUBE_TOKEN_PATH).write_text(creds.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=creds)


class YouTubePublisher:
    def __init__(self, service=None):
        self._service = service   # injected mock in tests; lazily built otherwise

    @property
    def service(self):
        if self._service is None:
            self._service = _build_service()
        return self._service

    def upload(self, package: PublishPackage, *, video_path: Path,
               dry_run: bool = False) -> PublishResult:
        video_path = Path(video_path)
        body = {
            "snippet": {
                "title": package.title,
                "description": package.description,
                "tags": package.tags,
                "categoryId": package.category_id,
            },
            "status": {
                "privacyStatus": package.privacy_status,
                "selfDeclaredMadeForKids": False,
            },
        }
        if dry_run:
            log.info("[dry-run] would upload %s as %s: %r",
                     video_path, package.privacy_status, package.title)
            return PublishResult(
                platform="youtube", video_id="DRYRUN",
                url="https://youtu.be/DRYRUN", visibility=package.privacy_status,
                uploaded_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

        from googleapiclient.http import MediaFileUpload
        media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True)
        req = self.service.videos().insert(
            part="snippet,status", body=body, media_body=media)
        response = None
        while response is None:
            _, response = req.next_chunk()
        video_id = response["id"]
        log.info("uploaded video_id=%s", video_id)

        self._set_thumbnail(video_id, package.thumbnail_path)
        self._insert_caption(video_id, package.caption_srt_path)

        return PublishResult(
            platform="youtube", video_id=video_id,
            url=f"https://youtu.be/{video_id}",
            visibility=package.privacy_status,
            uploaded_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

    def _set_thumbnail(self, video_id: str, thumbnail_path: str) -> None:
        if not thumbnail_path or not Path(thumbnail_path).exists():
            return
        from googleapiclient.http import MediaFileUpload
        try:
            self.service.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(thumbnail_path)).execute()
        except Exception as e:
            log.warning("thumbnail set failed (non-fatal): %s", e)

    def _insert_caption(self, video_id: str, srt_path: str) -> None:
        if not srt_path or not Path(srt_path).exists():
            return
        from googleapiclient.http import MediaFileUpload
        try:
            self.service.captions().insert(
                part="snippet",
                body={"snippet": {"videoId": video_id, "language": "en",
                                  "name": "captions", "isDraft": False}},
                media_body=MediaFileUpload(srt_path)).execute()
        except Exception as e:
            log.warning("caption insert failed (non-fatal): %s", e)
```

- [ ] **Step 5.4: Run tests — verify they pass**

Run: `pytest tests/video_agent/publishers/test_youtube_publisher.py -v`
Expected: 3 PASS.

- [ ] **Step 5.5: Checkpoint** — confirm google libs are imported *inside* functions (mock path never triggers OAuth); thumbnail/caption failures are non-fatal.

---

## Task 6: HarnessRunner (Lifecycle subsystem)

**Files:**
- Create: `video_agent/harness/runner.py`
- Create: `tests/video_agent/harness/test_runner.py`

- [ ] **Step 6.1: Write failing tests**

Create `tests/video_agent/harness/test_runner.py`:

```python
"""Tests for the HarnessRunner phase state-machine: ordering, resume/idempotency,
publish=False gate, verify-failure stop."""
from pathlib import Path
from unittest.mock import MagicMock
from video_agent.harness.runner import HarnessRunner
from video_agent.harness.manifest import VerifyReport, PublishPackage, PublishResult


def _runner_with_stubs(tmp_path, *, verify_passes=True, publish=False):
    """Build a runner with every external collaborator stubbed so no real
    blog fetch / generation / render / upload happens."""
    r = HarnessRunner(workspace_base=tmp_path, publish=publish)
    r._do_plan = MagicMock(side_effect=lambda m: _set(m, status="planned"))
    r._do_generate = MagicMock(side_effect=lambda m: _set(m, status="generated",
                               storyboard_path=str(tmp_path / "sb.json")))
    r._do_render = MagicMock(side_effect=lambda m: _set(m, status="rendered",
                             video_path=str(tmp_path / "v.mp4"),
                             srt_path=str(tmp_path / "s.srt")))
    report = VerifyReport(passed=verify_passes, checks={}, defects=[]
                          if verify_passes else ["bad"])
    r._do_verify = MagicMock(side_effect=lambda m: _set(
        m, status="verified" if verify_passes else "failed", verify=report))
    r._do_package = MagicMock(side_effect=lambda m: _set(m, status="packaged",
        package=PublishPackage("T", "D", ["a"], "28", "t.png", "s.srt", "unlisted")))
    r._do_publish = MagicMock(side_effect=lambda m: _set(m, status="published",
        publish=PublishResult("youtube", "vid", "u", "unlisted", "now")))
    return r


def _set(m, **kw):
    for k, v in kw.items():
        setattr(m, k, v)
    return m


def test_publish_false_stops_after_verify(tmp_path: Path):
    r = _runner_with_stubs(tmp_path, publish=False)
    m = r.run(blog_url="u", slug="s")
    assert m.status == "verified"
    r._do_package.assert_not_called()
    r._do_publish.assert_not_called()


def test_full_path_publishes(tmp_path: Path):
    r = _runner_with_stubs(tmp_path, publish=True)
    m = r.run(blog_url="u", slug="s")
    assert m.status == "published"
    r._do_package.assert_called_once()
    r._do_publish.assert_called_once()


def test_verify_failure_stops_before_package(tmp_path: Path):
    r = _runner_with_stubs(tmp_path, publish=True, verify_passes=False)
    m = r.run(blog_url="u", slug="s")
    assert m.status == "failed"
    r._do_package.assert_not_called()
    r._do_publish.assert_not_called()


def test_resume_skips_completed_phases(tmp_path: Path):
    # First run renders+verifies (publish=False), persisting the manifest.
    r1 = _runner_with_stubs(tmp_path, publish=False)
    r1.run(blog_url="u", slug="s")
    # Second run (publish=True) on same slug must skip plan/generate/render/verify.
    r2 = _runner_with_stubs(tmp_path, publish=True)
    m = r2.run(blog_url="u", slug="s", resume=True)
    r2._do_generate.assert_not_called()
    r2._do_render.assert_not_called()
    assert m.status == "published"
```

- [ ] **Step 6.2: Run tests — verify they fail**

Run: `pytest tests/video_agent/harness/test_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'video_agent.harness.runner'`

- [ ] **Step 6.3: Implement the runner**

Create `video_agent/harness/runner.py`:

```python
"""HarnessRunner — deterministic, resumable phase state-machine that drives one
blog URL from fetch to (optional) unlisted YouTube upload. Phases:
PLAN -> GENERATE -> RENDER -> VERIFY -> PACKAGE -> PUBLISH. The manifest is
checkpointed after every phase; on resume, already-completed phases are skipped
(idempotent). The `_do_*` phase methods are separated so tests can stub them."""
from __future__ import annotations
import logging
import re
from pathlib import Path

import requests

from video_agent.harness.manifest import (
    RunManifest, new_manifest, save_manifest, load_manifest,
)
from video_agent.harness.verify_heuristic import verify_video
from video_agent.publishers.youtube_packager import build_package
from video_agent.publishers.youtube_publisher import YouTubePublisher

log = logging.getLogger(__name__)

# status ordering used to decide what a resumed run may skip. "init" is the
# pre-PLAN sentinel so a fresh manifest (status="init") never skips PLAN.
_ORDER = ["init", "planned", "generated", "rendered", "verified",
          "packaged", "published"]
_BROWSER_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _slug(url: str) -> str:
    path = url.rstrip("/").split("/")[-1]
    s = re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-")
    return s[:60] or "video"


class HarnessRunner:
    def __init__(self, workspace_base: Path = Path("output/videos"),
                 publish: bool = False, publisher: YouTubePublisher | None = None):
        self.workspace_base = Path(workspace_base)
        self.publish = publish
        self.publisher = publisher

    # ── public entry ────────────────────────────────────────────────────────
    def run(self, blog_url: str, slug: str | None = None,
            resume: bool = False) -> RunManifest:
        slug = slug or _slug(blog_url)
        workspace = self.workspace_base / slug
        workspace.mkdir(parents=True, exist_ok=True)
        mpath = workspace / "run_manifest.json"

        if resume and mpath.exists():
            m = load_manifest(mpath)
            log.info("resuming run %s from status=%s", m.run_id, m.status)
        else:
            m = new_manifest(blog_url=blog_url, slug=slug, workspace=str(workspace))
            save_manifest(m, mpath)

        phases = [
            ("planned",   self._do_plan),
            ("generated", self._do_generate),
            ("rendered",  self._do_render),
            ("verified",  self._do_verify),
        ]
        if self.publish:
            phases += [("packaged", self._do_package),
                       ("published", self._do_publish)]

        for target_status, fn in phases:
            if self._already_done(m, target_status):
                log.info("skip %s (already %s)", target_status, m.status)
                continue
            try:
                fn(m)
            except Exception as e:
                m.status = "failed"
                m.last_error = f"{fn.__name__}: {e}"
                save_manifest(m, mpath)
                log.error("phase %s FAILED: %s", fn.__name__, e, exc_info=True)
                raise
            save_manifest(m, mpath)
            if m.status == "failed":          # gate (e.g. verify) failed cleanly
                log.warning("stopping: status=failed after %s", fn.__name__)
                break
        return m

    def _already_done(self, m: RunManifest, target: str) -> bool:
        if m.status == "failed":
            return False
        return _ORDER.index(m.status) >= _ORDER.index(target)

    # ── phases (stubbed in tests) ─────────────────────────────────────────────
    def _do_plan(self, m: RunManifest) -> None:
        r = requests.get(m.blog_url, timeout=30, headers=_BROWSER_HEADERS)
        r.raise_for_status()
        ws = Path(m.workspace)
        (ws / "blog.html").write_text(r.text, encoding="utf-8")
        m._html = r.text                      # transient, not serialized
        m.status = "planned"

    def _do_generate(self, m: RunManifest) -> None:
        from video_agent.orchestrator import build_storyboard
        from video_agent.script_builder import extract_facts
        ws = Path(m.workspace)
        html = getattr(m, "_html", None) or (ws / "blog.html").read_text(encoding="utf-8")
        blog_record = self._blog_record(m, html)
        m._blog_record = blog_record
        facts, _ = extract_facts(blog_record)
        sb = build_storyboard(blog=blog_record, facts=facts, blog_html=html,
                              workspace=ws)
        m._sb = sb
        m.storyboard_path = str(ws / "storyboard.json")
        m.status = "generated"

    def _do_render(self, m: RunManifest) -> None:
        from video_agent.voiceover import synthesize_segments, VoiceSegment
        from video_agent.subtitles import generate_srt
        from video_agent.composer import compose_short_v2
        from video_agent.storyboard import load_storyboard
        ws = Path(m.workspace)
        sb = getattr(m, "_sb", None) or load_storyboard(ws / "storyboard.json")
        region = (getattr(m, "_blog_record", {}) or {}).get("region", "default")

        segs = []
        for s in sb.scenes:
            prosody = "conversational"
            if s.cinematography and s.cinematography.voice_prosody:
                prosody = s.cinematography.voice_prosody
            segs.append(VoiceSegment(text=s.narration, prosody=prosody))
        voice_path = ws / "voiceover.mp3"
        voice = synthesize_segments(segs, voice_path, region=region)
        narration = " ".join(s.narration for s in sb.scenes)
        srt = generate_srt(voice["audio_path"], ws / "subtitles.srt",
                           narration_hint=narration)
        out = ws / "video_short.mp4"
        compose_short_v2(sb, voice_path=voice["audio_path"], subtitle_path=srt,
                         output_path=out, workspace=ws)
        m.voice_path = str(voice_path)
        m.srt_path = str(srt)
        m.video_path = str(out)
        m.status = "rendered"

    def _do_verify(self, m: RunManifest) -> None:
        report = verify_video(Path(m.video_path))
        m.verify = report
        m.status = "verified" if report.passed else "failed"
        if not report.passed:
            m.last_error = "verify gate failed: " + "; ".join(report.defects)

    def _do_package(self, m: RunManifest) -> None:
        from video_agent.ollama_client import OllamaClient
        from video_agent.storyboard import load_storyboard
        ws = Path(m.workspace)
        sb = getattr(m, "_sb", None) or load_storyboard(ws / "storyboard.json")
        thumb = self._thumbnail(m)
        m.package = build_package(sb, thumbnail_path=thumb,
                                  caption_srt_path=m.srt_path,
                                  client=OllamaClient())
        m.status = "packaged"

    def _do_publish(self, m: RunManifest) -> None:
        pub = self.publisher or YouTubePublisher()
        m.publish = pub.upload(m.package, video_path=Path(m.video_path))
        m.status = "published"

    # ── helpers ───────────────────────────────────────────────────────────────
    def _blog_record(self, m: RunManifest, html: str) -> dict:
        import json
        history = {}
        hp = Path("blog_history.json")
        if hp.exists():
            data = json.loads(hp.read_text(encoding="utf-8"))
            for post in data.get("posts", []):
                if post.get("url", "").rstrip("/") == m.blog_url.rstrip("/"):
                    history = post
                    break
        return {
            "blog_id": m.slug, "title": history.get("title", "HRSU Blog Video"),
            "url": m.blog_url, "content_html": html,
            "region": history.get("region", "default"), "persona": "procurement",
            "category": history.get("category", "industry"),
            "subcategory": history.get("subcategory", ""),
            "language": history.get("language", "en-US"),
        }

    def _thumbnail(self, m: RunManifest) -> Path:
        """Grab a hero frame (2s in) as the thumbnail; fall back to the video
        path if extraction fails (publisher treats a missing thumb as non-fatal)."""
        import shutil, subprocess
        ws = Path(m.workspace)
        thumb = ws / "thumbnail.png"
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg and m.video_path:
            try:
                subprocess.run([ffmpeg, "-y", "-ss", "2", "-i", m.video_path,
                                "-vframes", "1", str(thumb)],
                               check=True, capture_output=True)
                if thumb.exists():
                    return thumb
            except Exception as e:
                log.warning("thumbnail grab failed: %s", e)
        return thumb
```

- [ ] **Step 6.4: Run tests — verify they pass**

Run: `pytest tests/video_agent/harness/test_runner.py -v`
Expected: 4 PASS.

> Note: the phases pass transient data between each other via underscore
> attributes (`m._html`, `m._sb`, `m._blog_record`) set on the manifest instance.
> `RunManifest` is a plain dataclass (no `__slots__`), so these ad-hoc attributes
> are allowed, and `dataclasses.asdict` serializes only *declared* fields — so the
> underscore attributes are ignored on save (intentional: they are an in-process,
> single-run handoff and must not pollute the persisted JSON). On a resumed run
> in a fresh process they are absent, which is why each phase falls back to
> reloading from disk (`load_storyboard`, `blog.html`).

- [ ] **Step 6.5: Checkpoint** — confirm `_already_done` skips correctly and a failed verify sets `status="failed"` (caught by the `break`).

---

## Task 7: Entry points

**Files:**
- Modify: `scripts/make_video.py`
- Create: `scripts/publish_video.py`

- [ ] **Step 7.1: Add the publish entry point**

Create `scripts/publish_video.py`:

```python
"""Run the full video harness on an HRSU blog post and upload it to YouTube
(unlisted).

Usage:
    python scripts/publish_video.py <blog-url>
    python scripts/publish_video.py <blog-url> --dry-run   # build+verify+package, no upload
    python scripts/publish_video.py <blog-url> --resume    # resume from last checkpoint
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("publish_video")

from video_agent.harness.runner import HarnessRunner
from video_agent.publishers.youtube_publisher import YouTubePublisher


def main():
    p = argparse.ArgumentParser(description="Generate + publish a short to YouTube.")
    p.add_argument("url", help="Full URL of the blog post")
    p.add_argument("--dry-run", action="store_true",
                   help="Run through packaging but do not upload")
    p.add_argument("--resume", action="store_true",
                   help="Resume from the last checkpointed phase")
    args = p.parse_args()

    publisher = None
    if args.dry_run:
        # Inject a publisher whose upload() is forced to dry-run.
        class _DryPublisher(YouTubePublisher):
            def upload(self, package, *, video_path, dry_run=False):
                return super().upload(package, video_path=video_path, dry_run=True)
        publisher = _DryPublisher()

    runner = HarnessRunner(publish=True, publisher=publisher)
    m = runner.run(blog_url=args.url, resume=args.resume)

    if m.status == "published":
        print(f"\n  Published ({m.publish.visibility}): {m.publish.url}\n")
    elif m.status == "failed":
        print(f"\n  FAILED: {m.last_error}\n")
        sys.exit(1)
    else:
        print(f"\n  Stopped at status={m.status}\n")


if __name__ == "__main__":
    main()
```

- [ ] **Step 7.2: Verify dry-run path imports and parses**

Run: `python scripts/publish_video.py --help`
Expected: argparse help text listing `url`, `--dry-run`, `--resume`. No import errors.

- [ ] **Step 7.3: Repoint make_video.py at the runner (render+verify only)**

In `scripts/make_video.py`, replace the entire body of `main()` (everything after
`args = parser.parse_args()`) with a delegation to the runner, keeping the
existing CLI flags accepted for back-compat:

```python
    from video_agent.harness.runner import HarnessRunner, _slug
    slug = _slug(args.url)
    log.info("Workspace: %s", Path("output/videos") / slug)

    runner = HarnessRunner(publish=False)          # render + verify, no upload
    m = runner.run(blog_url=args.url, resume=False)

    if m.status == "failed":
        log.error("Pipeline failed: %s", m.last_error)
        sys.exit(1)

    out = Path(m.video_path)
    size_mb = out.stat().st_size / 1_048_576
    log.info("Done: %s  (%.1f MB) — verify passed=%s",
             out, size_mb, m.verify.passed if m.verify else "n/a")
    print(f"\n  Video ready: {out.resolve()}  ({size_mb:.1f} MB)\n")
    try:
        subprocess.Popen(["start", "", str(out.resolve())], shell=True)
    except Exception:
        pass
```

Leave the imports, `_slug` (now unused locally but harmless), and the
`argparse` setup at the top of `main()` intact. The `--force` / `--no-voice`
flags remain accepted (no-ops under the runner) so existing muscle-memory does
not break.

- [ ] **Step 7.4: Smoke-check make_video imports**

Run: `python -c "import scripts.make_video"`
Expected: no error.

- [ ] **Step 7.5: Checkpoint** — confirm both scripts import; `make_video.py` no
  longer calls `synthesize_segments` / `compose_short_v2` directly (the runner owns that now).

---

## Task 8: Full suite + end-to-end dry run

- [ ] **Step 8.1: Run the whole suite**

Run: `pytest tests/ -x -q`
Expected: all green; no regressions in existing video_agent tests.

- [ ] **Step 8.2: End-to-end dry run on a real blog (manual, requires ffmpeg + Ollama)**

Run: `python scripts/publish_video.py https://blog.hrsuindore.com/<some-post>.html --dry-run`

Expected: pipeline runs PLAN→GENERATE→RENDER→VERIFY→PACKAGE→PUBLISH(dry),
prints `Published (unlisted): https://youtu.be/DRYRUN`, and writes
`output/videos/<slug>/run_manifest.json` with `status: "published"`,
a populated `verify` block, and a `package` block. **No real upload occurs.**

- [ ] **Step 8.3: Checkpoint** — open `run_manifest.json`; confirm `verify.passed`,
  `verify.checks` populated (duration/audio/ribbon), and `package.title` ≤ 100 chars,
  `package.privacy_status == "unlisted"`.

---

## Real-upload prerequisites (perform once, before a non-dry-run publish)

These are **manual setup steps**, not code:

1. In Google Cloud Console (the project behind `client_secrets.json`): **enable
   the YouTube Data API v3**.
2. First non-dry-run invocation opens a browser consent for the two scopes
   (`youtube.upload`, `youtube.force-ssl`) and writes `youtube_token.json`.
   This token is separate from the Blogger token — do not overwrite either.
3. Confirm the signed-in Google account owns the target YouTube channel.

After setup: `python scripts/publish_video.py <blog-url>` performs a real
unlisted upload and prints the watch URL.
