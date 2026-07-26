# HRSU Vertical Short-Form Video Pipeline — Implementation Plan, Part 2

> **Continuation of** `2026-05-03-video-pipeline-implementation.md` (Sprints 1–3 / Tasks 1–15).
> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans`.

This file covers **Sprints 4–8** — composer, asset library, publishers, scheduler, orchestrator, CLI, integration. It assumes Sprints 1–3 are complete and all 57 tests in `tests/video_agent/` pass.

**Spec reference:** `docs/superpowers/specs/2026-05-02-video-pipeline-design.md` §4.6–§4.12, §5, §6.

**Pattern (every task):** failing test → implement → green → commit. Run full suite at end of every sprint.

---

## Sprint Map (continued)

| Sprint | Tasks | Outcome |
|--------|-------|---------|
| **4** | 16–20 | composer.py, brand-asset renderer, music checker, smoke MP4 |
| **5** | 21–24 | asset_manifest, factory_broll, stock, tag_assets tool |
| **6** | 25–29 | publishers/base, publishers/youtube, scheduler, orchestrator MVP, main.py CLI |
| **7** | 30–32 | publishers/linkedin, token_manager extensions, scheduler integration |
| **8** | 33–37 | publishers/instagram, GitHub CDN, cleanup tool, blog_agent_v3 hook, VIDEO_SETUP.md |

---

## Sprint 4 — Composition: produce playable MP4

### Task 16: TDD `composer.py`

**Files:**
- Create: `tests/video_agent/test_composer.py`
- Create: `video_agent/composer.py`

This is the largest module. We split the implementation into helpers that each have unit tests; the public `compose_short` is exercised via mocks (real ffmpeg integration is verified in Task 20 smoke test).

- [ ] **Step 1: Write failing tests**

Create `tests/video_agent/test_composer.py`:

```python
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from video_agent.composer import (
    _redistribute_durations, _pick_music, _build_subtitle_style,
    ComposerError, compose_short,
)


def test_redistribute_scales_to_audio_duration():
    scenes = [
        {"index": 0, "duration_s": 5.0},
        {"index": 1, "duration_s": 5.0},
        {"index": 2, "duration_s": 5.0},
    ]
    out = _redistribute_durations(scenes, audio_duration_s=30.0)
    assert sum(s["duration_s"] for s in out) == pytest.approx(30.0, abs=0.01)
    assert all(s["duration_s"] == pytest.approx(10.0, abs=0.01) for s in out)


def test_redistribute_handles_zero_total():
    scenes = [{"index": 0, "duration_s": 0.0}, {"index": 1, "duration_s": 0.0}]
    out = _redistribute_durations(scenes, audio_duration_s=20.0)
    # Falls back to uniform split.
    assert all(s["duration_s"] == pytest.approx(10.0, abs=0.01) for s in out)


def test_pick_music_deterministic_by_region(tmp_path):
    (tmp_path / "track_a.mp3").write_bytes(b"x")
    (tmp_path / "track_b.mp3").write_bytes(b"x")
    a = _pick_music(tmp_path, region="australia", persona="procurement")
    b = _pick_music(tmp_path, region="australia", persona="procurement")
    assert a == b
    assert a.suffix == ".mp3"


def test_pick_music_returns_none_when_empty(tmp_path):
    assert _pick_music(tmp_path, region="usa", persona="procurement") is None


def test_subtitle_style_contains_required_keys():
    style = _build_subtitle_style()
    for key in ("FontName", "FontSize", "PrimaryColour", "OutlineColour",
                "Outline", "Alignment", "MarginV"):
        assert key in style


def test_compose_short_validates_inputs(tmp_path):
    with pytest.raises(ComposerError, match="visual_results length"):
        compose_short(
            scenes=[{"index": 0, "duration_s": 5.0}],
            visual_results=[],          # mismatched length
            voiceover={"audio_path": tmp_path / "v.mp3", "duration_s": 30.0},
            subtitle_path=tmp_path / "s.srt",
            output_path=tmp_path / "out.mp4",
        )


def test_compose_short_runs_pipeline(tmp_path):
    """Smoke-mock the heavy ffmpeg/moviepy parts; verify the orchestration shape."""
    scenes = [
        {"index": 0, "duration_s": 5.0, "visual_type": "text_card",
         "transition_in": "fade", "on_screen_text": "HOOK"},
        {"index": 1, "duration_s": 5.0, "visual_type": "infographic",
         "transition_in": "fade", "on_screen_text": ""},
    ]
    visuals = [
        {"asset_path": tmp_path / "0.png", "is_video_clip": False,
         "duration_s": None, "generator_used": "text_card"},
        {"asset_path": tmp_path / "1.png", "is_video_clip": False,
         "duration_s": None, "generator_used": "infographic"},
    ]
    for v in visuals:
        v["asset_path"].write_bytes(b"\x89PNG")
    audio = tmp_path / "v.mp3"; audio.write_bytes(b"ID3")
    srt = tmp_path / "s.srt"; srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHELLO\n\n")
    out = tmp_path / "out.mp4"

    with patch("video_agent.composer._render_main_clip") as mock_main, \
         patch("video_agent.composer._burn_subtitles") as mock_burn, \
         patch("video_agent.composer._concat_intro_outro") as mock_concat, \
         patch("video_agent.composer._validate_output") as mock_val:
        mock_main.return_value = tmp_path / "main.mp4"
        mock_burn.return_value = tmp_path / "main_subs.mp4"
        mock_concat.return_value = out
        mock_val.return_value = None
        out.write_bytes(b"fake mp4")
        result = compose_short(
            scenes=scenes, visual_results=visuals,
            voiceover={"audio_path": audio, "duration_s": 10.0},
            subtitle_path=srt, output_path=out, region="australia",
        )
    assert result == out
    mock_main.assert_called_once()
    mock_burn.assert_called_once()
    mock_val.assert_called_once()
```

- [ ] **Step 2: Run tests — confirm fail**

Run: `python -m pytest tests/video_agent/test_composer.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `composer.py`**

Create `video_agent/composer.py`:

```python
"""Compose final 9:16 MP4 from scenes + visuals + voiceover + subtitles + music."""
import hashlib
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Iterable

from video_agent.config import (
    SHORT_FORMAT, KEN_BURNS_ZOOM_END, TRANSITION_DEFAULT_S, TRANSITION_AFTER_BROLL_S,
    LOGO_OPACITY, PROGRESS_BAR_HEIGHT_PX, MUSIC_VOLUME_DB, MUSIC_DUCKED_DB,
    BRAND_LOGO_WHITE_PATH, INTRO_VIDEO_PATH, OUTRO_VIDEO_PATH,
    BRAND_GOLD,
)

log = logging.getLogger(__name__)


class ComposerError(RuntimeError):
    pass


# ─── Pure helpers (unit-tested) ────────────────────────────────────────────

def _redistribute_durations(scenes: list[dict], audio_duration_s: float) -> list[dict]:
    """Scale per-scene durations so they sum to audio_duration_s. Pure function."""
    out = [dict(s) for s in scenes]
    total = sum(s.get("duration_s", 0.0) for s in out)
    n = max(1, len(out))
    if total <= 0.01:
        each = audio_duration_s / n
        for s in out:
            s["duration_s"] = each
        return out
    factor = audio_duration_s / total
    for s in out:
        s["duration_s"] = s.get("duration_s", 0.0) * factor
    return out


def _pick_music(music_dir: Path, region: str, persona: str) -> Path | None:
    music_dir = Path(music_dir)
    if not music_dir.exists():
        return None
    tracks = sorted(p for p in music_dir.glob("*.mp3"))
    if not tracks:
        return None
    key = f"{region}|{persona}".encode()
    idx = int(hashlib.sha1(key).hexdigest(), 16) % len(tracks)
    return tracks[idx]


def _build_subtitle_style() -> dict:
    """ffmpeg `subtitles` filter `force_style` keys."""
    return {
        "FontName": "Poppins",
        "FontSize": "22",
        "PrimaryColour": "&H00FFFFFF",   # white
        "OutlineColour": "&H00000000",   # black
        "Outline": "3",
        "BorderStyle": "1",
        "Alignment": "2",                # bottom-center
        "MarginV": "200",
        "Bold": "1",
    }


def _format_force_style(style: dict) -> str:
    return ",".join(f"{k}={v}" for k, v in style.items())


# ─── ffmpeg helpers (covered by Task 20 smoke test) ────────────────────────

def _ffmpeg(cmd: list[str]) -> None:
    log.debug("ffmpeg: %s", " ".join(cmd))
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise ComposerError(f"ffmpeg failed (exit {res.returncode}): {res.stderr[-500:]}")


def _ffprobe_duration(path: Path) -> float:
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        raise ComposerError(f"ffprobe failed: {res.stderr[-300:]}")
    return float(res.stdout.strip())


def _render_main_clip(scenes: list[dict], visuals: list[dict],
                      voiceover_path: Path, output_path: Path,
                      music_path: Path | None) -> Path:
    """Render concatenated scenes + voice + (optional) music to MP4 (no subs/intro yet)."""
    from moviepy.editor import (
        ImageClip, VideoFileClip, AudioFileClip, CompositeAudioClip,
        concatenate_videoclips, afx,
    )

    w, h = SHORT_FORMAT["resolution"]
    fps = SHORT_FORMAT["fps"]
    clips = []
    for scene, vis in zip(scenes, visuals):
        dur = scene["duration_s"]
        if vis["is_video_clip"]:
            c = VideoFileClip(str(vis["asset_path"])).without_audio()
            c = c.subclip(0, min(c.duration, dur)).resize((w, h))
            if c.duration < dur:
                # loop
                c = c.fx(__import__("moviepy.video.fx.all", fromlist=["loop"]).loop, duration=dur)
        else:
            c = ImageClip(str(vis["asset_path"])).set_duration(dur).resize((w, h))
            zoom = KEN_BURNS_ZOOM_END
            c = c.resize(lambda t, z=zoom, d=dur: 1.0 + (z - 1.0) * (t / max(d, 0.01)))
        # crossfades
        xfade = TRANSITION_AFTER_BROLL_S if scene.get("visual_type") == "hrsu_edge" else TRANSITION_DEFAULT_S
        if scene.get("transition_in") == "fade":
            c = c.crossfadein(xfade)
        clips.append(c)

    main = concatenate_videoclips(clips, method="compose", padding=-min(TRANSITION_DEFAULT_S, 0.2))

    voice = AudioFileClip(str(voiceover_path))
    audio = voice
    if music_path and music_path.exists():
        music = AudioFileClip(str(music_path)).volumex(10 ** (MUSIC_VOLUME_DB / 20))
        # Loop if shorter
        if music.duration < main.duration:
            music = afx.audio_loop(music, duration=main.duration)
        else:
            music = music.subclip(0, main.duration)
        audio = CompositeAudioClip([music, voice])
    main = main.set_audio(audio).set_duration(min(main.duration, voice.duration + 0.5))

    main.write_videofile(
        str(output_path),
        fps=fps, codec="libx264", audio_codec="aac",
        bitrate=SHORT_FORMAT["bitrate"], preset="medium",
        threads=2, logger=None,
        ffmpeg_params=["-pix_fmt", "yuv420p", "-movflags", "+faststart",
                       "-profile:v", "high", "-level", "4.0"],
    )
    return output_path


def _burn_subtitles(input_mp4: Path, srt_path: Path, output_mp4: Path) -> Path:
    style = _format_force_style(_build_subtitle_style())
    # ffmpeg subtitles filter requires forward slashes even on Windows
    srt_str = str(srt_path).replace("\\", "/").replace(":", r"\:")
    vf = f"subtitles='{srt_str}':force_style='{style}'"
    _ffmpeg([
        "ffmpeg", "-y", "-i", str(input_mp4), "-vf", vf,
        "-c:a", "copy", "-c:v", "libx264", "-preset", "medium",
        "-pix_fmt", "yuv420p", str(output_mp4),
    ])
    return output_mp4


def _concat_intro_outro(main_mp4: Path, intro_mp4: Path | None,
                        outro_mp4: Path | None, output_mp4: Path) -> Path:
    if not intro_mp4 and not outro_mp4:
        shutil.copy2(main_mp4, output_mp4)
        return output_mp4
    parts = [p for p in (intro_mp4, main_mp4, outro_mp4) if p and Path(p).exists()]
    if len(parts) == 1:
        shutil.copy2(parts[0], output_mp4)
        return output_mp4
    listfile = output_mp4.with_suffix(".concat.txt")
    listfile.write_text("\n".join(f"file '{Path(p).as_posix()}'" for p in parts), encoding="utf-8")
    _ffmpeg([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile),
        "-c", "copy", str(output_mp4),
    ])
    listfile.unlink(missing_ok=True)
    return output_mp4


def _validate_output(path: Path) -> None:
    if not path.exists() or path.stat().st_size < 50_000:
        raise ComposerError(f"Output too small: {path}")
    duration = _ffprobe_duration(path)
    if not (SHORT_FORMAT["min_duration_s"] - 2 <= duration <= SHORT_FORMAT["max_duration_s"] + 2):
        raise ComposerError(f"Duration {duration:.1f}s outside [{SHORT_FORMAT['min_duration_s']}, {SHORT_FORMAT['max_duration_s']}]")
    size_mb = path.stat().st_size / 1_048_576
    if size_mb > SHORT_FORMAT["max_filesize_mb"]:
        raise ComposerError(f"File {size_mb:.1f}MB exceeds {SHORT_FORMAT['max_filesize_mb']}MB cap")


# ─── Public API ────────────────────────────────────────────────────────────

def compose_short(*, scenes: list[dict], visual_results: list[dict],
                  voiceover: dict, subtitle_path: Path, output_path: Path,
                  music_track: Path | None = None,
                  region: str = "default", persona: str = "procurement") -> Path:
    if len(visual_results) != len(scenes):
        raise ComposerError(
            f"visual_results length ({len(visual_results)}) != scenes length ({len(scenes)})"
        )
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    workspace = output_path.parent

    audio_path = Path(voiceover["audio_path"])
    audio_duration = float(voiceover["duration_s"])
    scaled_scenes = _redistribute_durations(scenes, audio_duration)

    main_mp4 = workspace / "_main.mp4"
    _render_main_clip(scaled_scenes, visual_results, audio_path, main_mp4, music_track)

    subs_mp4 = workspace / "_main_subs.mp4"
    _burn_subtitles(main_mp4, subtitle_path, subs_mp4)

    intro = Path(INTRO_VIDEO_PATH) if Path(INTRO_VIDEO_PATH).exists() else None
    outro = Path(OUTRO_VIDEO_PATH) if Path(OUTRO_VIDEO_PATH).exists() else None
    _concat_intro_outro(subs_mp4, intro, outro, output_path)

    _validate_output(output_path)
    main_mp4.unlink(missing_ok=True)
    subs_mp4.unlink(missing_ok=True)
    return output_path
```

- [ ] **Step 4: Run tests until green**

Run: `python -m pytest tests/video_agent/test_composer.py -v`
Expected: All 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add video_agent/composer.py tests/video_agent/test_composer.py
git commit -m "feat(video_agent): composer (moviepy + ffmpeg) with helpers and validation"
```

---

### Task 17: TDD brand asset renderer (`tools/render_brand_assets.py`)

Pre-renders `intro_3s.mp4` and `outro_5s.mp4` with brand colors. Optional — composer skips intro/outro gracefully if files don't exist.

**Files:**
- Create: `tests/video_agent/tools/__init__.py`
- Create: `tests/video_agent/tools/test_render_brand_assets.py`
- Create: `video_agent/tools/render_brand_assets.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/video_agent/tools/test_render_brand_assets.py
from pathlib import Path
from unittest.mock import patch
from video_agent.tools.render_brand_assets import render_intro, render_outro


def test_render_intro_calls_ffmpeg(tmp_path):
    out = tmp_path / "intro.mp4"
    with patch("video_agent.tools.render_brand_assets._ffmpeg") as m:
        m.side_effect = lambda cmd: out.write_bytes(b"fake mp4")
        render_intro(out, duration_s=3.0)
    m.assert_called_once()


def test_render_outro_calls_ffmpeg(tmp_path):
    out = tmp_path / "outro.mp4"
    with patch("video_agent.tools.render_brand_assets._ffmpeg") as m:
        m.side_effect = lambda cmd: out.write_bytes(b"fake mp4")
        render_outro(out, duration_s=5.0)
    m.assert_called_once()
```

- [ ] **Step 2: Implement**

```python
# video_agent/tools/render_brand_assets.py
"""Pre-render intro/outro MP4s. Run once during setup."""
import argparse
import logging
import subprocess
import tempfile
from pathlib import Path
from PIL import Image, ImageDraw

from video_agent.config import (
    SHORT_FORMAT, BRAND_DARK_NAVY, BRAND_GOLD, BRAND_TEXT_LIGHT,
    INTRO_VIDEO_PATH, OUTRO_VIDEO_PATH,
)
from video_agent.visual_engine.text_card import _load_font

log = logging.getLogger(__name__)


def _ffmpeg(cmd: list[str]) -> None:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {res.stderr[-400:]}")


def _make_card(text_lines: list[tuple[str, str, int]],
               output_png: Path) -> None:
    w, h = SHORT_FORMAT["resolution"]
    img = Image.new("RGB", (w, h), color=BRAND_DARK_NAVY)
    draw = ImageDraw.Draw(img)
    total = sum(size for _, _, size in text_lines) + 30 * (len(text_lines) - 1)
    y = (h - total) // 2
    for text, color, size in text_lines:
        font = _load_font(size)
        bbox = draw.textbbox((0, 0), text, font=font)
        line_w = bbox[2] - bbox[0]
        draw.text(((w - line_w) // 2, y), text, font=font, fill=color)
        y += size + 30
    img.save(output_png, "PNG")


def _png_to_mp4(png: Path, output_mp4: Path, duration_s: float) -> None:
    fps = SHORT_FORMAT["fps"]
    _ffmpeg([
        "ffmpeg", "-y", "-loop", "1", "-t", str(duration_s),
        "-i", str(png), "-r", str(fps), "-c:v", "libx264",
        "-pix_fmt", "yuv420p", "-profile:v", "high",
        "-an", str(output_mp4),
    ])


def render_intro(output_mp4: Path, duration_s: float = 3.0) -> Path:
    output_mp4 = Path(output_mp4)
    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        png = Path(td) / "intro.png"
        _make_card([
            ("HRSU INDORE", BRAND_GOLD, 110),
            ("Calcium Nitrate Specialists", BRAND_TEXT_LIGHT, 44),
        ], png)
        _png_to_mp4(png, output_mp4, duration_s)
    return output_mp4


def render_outro(output_mp4: Path, duration_s: float = 5.0) -> Path:
    output_mp4 = Path(output_mp4)
    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        png = Path(td) / "outro.png"
        _make_card([
            ("Need calcium nitrate?", BRAND_TEXT_LIGHT, 56),
            ("HRSUINDORE.COM", BRAND_GOLD, 100),
            ("DM us for a spec sheet", BRAND_TEXT_LIGHT, 42),
        ], png)
        _png_to_mp4(png, output_mp4, duration_s)
    return output_mp4


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--intro-only", action="store_true")
    p.add_argument("--outro-only", action="store_true")
    args = p.parse_args()
    if not args.outro_only:
        render_intro(Path(INTRO_VIDEO_PATH))
        log.info("Wrote %s", INTRO_VIDEO_PATH)
    if not args.intro_only:
        render_outro(Path(OUTRO_VIDEO_PATH))
        log.info("Wrote %s", OUTRO_VIDEO_PATH)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
```

- [ ] **Step 3: Run tests, commit**

```bash
python -m pytest tests/video_agent/tools/test_render_brand_assets.py -v
git add video_agent/tools/render_brand_assets.py tests/video_agent/tools/
git commit -m "feat(video_agent): brand intro/outro renderer"
```

---

### Task 18: TDD music library checker (`tools/check_music_library.py`)

**Files:**
- Create: `tests/video_agent/tools/test_check_music_library.py`
- Create: `video_agent/tools/check_music_library.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/video_agent/tools/test_check_music_library.py
from pathlib import Path
from video_agent.tools.check_music_library import audit


def test_audit_empty(tmp_path):
    r = audit(tmp_path)
    assert r["track_count"] == 0
    assert r["ok"] is False
    assert "no tracks" in r["message"].lower()


def test_audit_finds_mp3s(tmp_path):
    (tmp_path / "a.mp3").write_bytes(b"x")
    (tmp_path / "b.mp3").write_bytes(b"x")
    (tmp_path / "readme.txt").write_text("ignore me")
    r = audit(tmp_path)
    assert r["track_count"] == 2
    assert r["ok"] is True
```

- [ ] **Step 2: Implement**

```python
# video_agent/tools/check_music_library.py
"""Audit asset_library/music/ for usable tracks."""
import argparse
from pathlib import Path

MIN_TRACKS = 3


def audit(music_dir: Path) -> dict:
    music_dir = Path(music_dir)
    tracks = sorted(music_dir.glob("*.mp3")) if music_dir.exists() else []
    if not tracks:
        return {"track_count": 0, "ok": False,
                "message": f"No tracks in {music_dir} — composer will run music-free."}
    if len(tracks) < MIN_TRACKS:
        return {"track_count": len(tracks), "ok": True,
                "message": f"Only {len(tracks)} tracks — fewer than recommended ({MIN_TRACKS})."}
    return {"track_count": len(tracks), "ok": True,
            "message": f"{len(tracks)} tracks available."}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dir", default="asset_library/music")
    args = p.parse_args()
    r = audit(Path(args.dir))
    print(f"[{'OK' if r['ok'] else 'WARN'}] {r['message']}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run, commit**

```bash
python -m pytest tests/video_agent/tools/test_check_music_library.py -v
git add video_agent/tools/check_music_library.py tests/video_agent/tools/test_check_music_library.py
git commit -m "feat(video_agent): music library auditor"
```

---

### Task 19: Sprint 4 verification (test suite)

- [ ] **Step 1: Run all tests**

Run: `python -m pytest tests/video_agent/ -v`
Expected: All Sprint 1+2+3+4 tests PASS.

- [ ] **Step 2: Commit + tag**

```bash
git tag video-agent-sprint-4-tests
```

---

### Task 20: Live smoke test — produce a real MP4

This is an integration check. Requires Ollama running and FFmpeg on PATH.

- [ ] **Step 1: Write smoke script**

Create `scripts/smoke_video.py` (project root):

```python
"""End-to-end smoke: build script → voiceover → visuals → subtitles → composer."""
import asyncio
from pathlib import Path
import logging
logging.basicConfig(level=logging.INFO)

from video_agent.script_builder import build_script
from video_agent.voiceover import synthesize
from video_agent.visual_engine.dispatcher import generate_all_visuals
from video_agent.subtitles import generate_srt
from video_agent.composer import compose_short

BLOG = {
    "blog_id": "smoke_e2e",
    "title": "Calcium Nitrate Cuts H2S in Australian Wastewater Plants",
    "url": "https://blog.hrsuindore.com/test",
    "region": "australia", "persona": "procurement",
    "category": "wastewater_treatment", "subcategory": "h2s",
    "content_html": "<p>Calcium nitrate cut H2S by 90% at 50 mg/L in 24 hours. "
                    "Australian utilities reported 15% chemical-cost savings.</p>",
    "summary": "smoke",
}

def main():
    workspace = Path("output/videos/smoke_e2e"); workspace.mkdir(parents=True, exist_ok=True)
    script = build_script(BLOG, output_dir=workspace)
    print(f"Scenes: {len(script['scenes'])}, narration words: {len(script['narration'].split())}")

    voice = synthesize(script["narration"], workspace / "voiceover.mp3", region="australia")
    print(f"Voice: {voice}")

    visuals = generate_all_visuals(script["scenes"], workspace / "scenes")

    srt = generate_srt(voice["audio_path"], workspace / "subtitles.srt",
                       narration_hint=script["narration"])

    out = workspace / "video_short.mp4"
    compose_short(
        scenes=script["scenes"], visual_results=visuals, voiceover=voice,
        subtitle_path=srt, output_path=out, music_track=None,
        region="australia", persona="procurement",
    )
    print(f"Done: {out} ({out.stat().st_size / 1_048_576:.1f} MB)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run**

```bash
python scripts/smoke_video.py
```

Expected: prints scene count + voice info, ends with `Done: output/videos/smoke_e2e/video_short.mp4 (X.X MB)`. Open the file — should be 1080×1920, ~30–60s, with subtitles burned-in.

- [ ] **Step 3: Tag sprint completion**

```bash
git tag video-agent-sprint-4
```

---

## Sprint 5 — Real footage: factory_broll, stock, manifest, tag tool

### Task 21: TDD `asset_manifest.py`

**Files:**
- Create: `tests/video_agent/test_asset_manifest.py`
- Create: `video_agent/asset_manifest.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/video_agent/test_asset_manifest.py
import json
import pytest
from pathlib import Path
from video_agent.asset_manifest import (
    load_manifest, AssetManifestError, validate_entry,
)


def _write_manifest(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def test_load_returns_empty_when_missing(tmp_path):
    out = load_manifest(tmp_path / "nope.json")
    assert out == []


def test_load_parses_valid(tmp_path):
    p = _write_manifest(tmp_path, {"assets": [
        {"file": "factory.mp4", "type": "video", "categories": ["wastewater_treatment"],
         "personas": ["procurement"], "tags": ["plant", "tank"], "esg_relevant": False},
    ]})
    out = load_manifest(p)
    assert len(out) == 1
    assert out[0]["file"] == "factory.mp4"


def test_validate_entry_rejects_missing_keys():
    with pytest.raises(AssetManifestError, match="missing"):
        validate_entry({"file": "x.mp4"})


def test_validate_entry_normalizes_lists():
    e = validate_entry({"file": "a.mp4", "type": "video",
                        "categories": "wastewater_treatment", "personas": "procurement",
                        "tags": [], "esg_relevant": False})
    assert e["categories"] == ["wastewater_treatment"]
    assert e["personas"] == ["procurement"]


def test_load_skips_invalid_with_warning(tmp_path, caplog):
    p = _write_manifest(tmp_path, {"assets": [
        {"file": "ok.mp4", "type": "video", "categories": ["mining"],
         "personas": ["procurement"], "tags": [], "esg_relevant": False},
        {"file": "broken.mp4"},  # missing keys
    ]})
    out = load_manifest(p)
    assert len(out) == 1
    assert any("manifest" in r.message.lower() for r in caplog.records)
```

- [ ] **Step 2: Implement**

```python
# video_agent/asset_manifest.py
"""Load/validate asset_library/factory/manifest.json."""
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

REQUIRED = {"file", "type", "categories", "personas", "tags", "esg_relevant"}


class AssetManifestError(ValueError):
    pass


def validate_entry(entry: dict) -> dict:
    missing = REQUIRED - set(entry.keys())
    if missing:
        raise AssetManifestError(f"manifest entry missing keys: {sorted(missing)}")
    out = dict(entry)
    for k in ("categories", "personas", "tags"):
        if isinstance(out[k], str):
            out[k] = [out[k]]
    return out


def load_manifest(manifest_path: Path) -> list[dict]:
    manifest_path = Path(manifest_path)
    if not manifest_path.exists():
        return []
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        log.warning("manifest unreadable: %s", e)
        return []
    out = []
    for entry in raw.get("assets", []):
        try:
            out.append(validate_entry(entry))
        except AssetManifestError as e:
            log.warning("manifest entry skipped: %s", e)
    return out
```

- [ ] **Step 3: Commit**

```bash
python -m pytest tests/video_agent/test_asset_manifest.py -v
git add video_agent/asset_manifest.py tests/video_agent/test_asset_manifest.py
git commit -m "feat(video_agent): asset_manifest loader/validator"
```

---

### Task 22: TDD `visual_engine/factory_broll.py`

**Files:**
- Create: `tests/video_agent/visual_engine/test_factory_broll.py`
- Create: `video_agent/visual_engine/factory_broll.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/video_agent/visual_engine/test_factory_broll.py
import json
from pathlib import Path
from unittest.mock import patch
from video_agent.visual_engine.factory_broll import (
    score_asset, select_asset, render_factory_broll,
)


def _asset(file, **kw):
    base = {"file": file, "type": "video", "categories": ["all"],
            "personas": ["procurement"], "tags": [], "esg_relevant": False}
    base.update(kw)
    return base


def test_score_category_match():
    a = _asset("a.mp4", categories=["wastewater_treatment"])
    b = _asset("b.mp4", categories=["mining"])
    sa = score_asset(a, blog_category="wastewater_treatment", persona="procurement",
                     narration_tokens=set(), used_files=set())
    sb = score_asset(b, blog_category="wastewater_treatment", persona="procurement",
                     narration_tokens=set(), used_files=set())
    assert sa > sb


def test_score_esg_boost_when_keywords_present():
    a = _asset("solar.mp4", esg_relevant=True, tags=["solar"])
    b = _asset("plant.mp4", esg_relevant=False, tags=["plant"])
    sa = score_asset(a, "esg", "executive", {"solar"}, used_files=set())
    sb = score_asset(b, "esg", "executive", {"solar"}, used_files=set())
    assert sa > sb


def test_score_anti_repeat():
    a = _asset("used.mp4")
    fresh = score_asset(a, "mining", "procurement", set(), used_files=set())
    repeat = score_asset(a, "mining", "procurement", set(), used_files={"used.mp4"})
    assert repeat < fresh


def test_select_returns_none_for_empty():
    assert select_asset([], "mining", "procurement", "narration", set()) is None


def test_select_picks_highest_scorer():
    assets = [
        _asset("low.mp4", categories=["other"]),
        _asset("high.mp4", categories=["mining"], tags=["tunnel"]),
    ]
    pick = select_asset(assets, "mining", "procurement", "tunnel", set())
    assert pick["file"] == "high.mp4"


def test_render_falls_back_when_no_match(tmp_path):
    scene = {"index": 0, "visual_type": "hrsu_edge",
             "visual_spec": {"fallback_text": "HRSU EDGE"},
             "on_screen_text": "HRSU EDGE", "duration_s": 3.0,
             "narration": "blah"}
    blog = {"category": "mining", "persona": "procurement"}
    out = tmp_path / "0.png"
    with patch("video_agent.visual_engine.factory_broll.load_manifest", return_value=[]):
        result = render_factory_broll(scene, blog, out, asset_root=tmp_path)
    assert result["asset_path"].exists()
    assert result["generator_used"] == "text_card"
```

- [ ] **Step 2: Implement**

```python
# video_agent/visual_engine/factory_broll.py
"""Select + use real factory footage from asset_library/factory/."""
import logging
import re
import shutil
from pathlib import Path
from video_agent.config import ESG_KEYWORDS
from video_agent.asset_manifest import load_manifest
from video_agent.visual_engine.text_card import render_text_card

log = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"\b[a-z]{3,}\b")


def _tokens(text: str) -> set:
    return set(_TOKEN_RE.findall(text.lower()))


def score_asset(asset: dict, blog_category: str, persona: str,
                narration_tokens: set, used_files: set) -> float:
    score = 0.0
    cats = asset.get("categories", [])
    if blog_category in cats:
        score += 5.0
    elif "all" in cats:
        score += 1.0
    else:
        return -1.0
    if persona in asset.get("personas", []):
        score += 1.0
    if asset.get("esg_relevant") and (set(ESG_KEYWORDS) & narration_tokens):
        score += 3.0
    score += len(set(asset.get("tags", [])) & narration_tokens) * 0.5
    if asset.get("file") in used_files:
        score -= 10.0
    return score


def select_asset(assets: list[dict], blog_category: str, persona: str,
                 narration: str, used_files: set) -> dict | None:
    if not assets:
        return None
    tokens = _tokens(narration)
    scored = [(score_asset(a, blog_category, persona, tokens, used_files), a) for a in assets]
    scored = [s for s in scored if s[0] >= 0]
    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])
    return scored[0][1]


def render_factory_broll(scene: dict, blog: dict, output_path: Path, *,
                         asset_root: Path = Path("asset_library/factory"),
                         used_files: set | None = None) -> dict:
    asset_root = Path(asset_root)
    output_path = Path(output_path)
    used_files = used_files if used_files is not None else set()

    assets = load_manifest(asset_root / "manifest.json")
    pick = select_asset(
        assets,
        blog_category=blog.get("category", ""),
        persona=blog.get("persona", "procurement"),
        narration=scene.get("narration", ""),
        used_files=used_files,
    )

    if not pick:
        log.info("No factory asset matched scene %s — text_card fallback", scene.get("index"))
        text = scene.get("on_screen_text") or scene.get("visual_spec", {}).get("fallback_text") or "HRSU"
        png = output_path.with_suffix(".png")
        render_text_card(png, layout="hook", text=text)
        return {"asset_path": png, "is_video_clip": False, "duration_s": None,
                "generator_used": "text_card"}

    src = asset_root / pick["file"]
    if not src.exists():
        log.warning("Manifest references missing file %s — text_card fallback", src)
        text = scene.get("on_screen_text") or "HRSU"
        png = output_path.with_suffix(".png")
        render_text_card(png, layout="hook", text=text)
        return {"asset_path": png, "is_video_clip": False, "duration_s": None,
                "generator_used": "text_card"}

    dest = output_path.with_suffix(src.suffix)
    shutil.copy2(src, dest)
    used_files.add(pick["file"])
    return {"asset_path": dest, "is_video_clip": pick.get("type") == "video",
            "duration_s": None, "generator_used": "broll"}
```

- [ ] **Step 3: Wire into dispatcher**

Edit `video_agent/visual_engine/dispatcher.py`. Replace the `# hrsu_edge / stock` branch with:

```python
        if vt == "hrsu_edge":
            return _safe_factory_broll(scene, output_path)
```

Add at top of file:
```python
from video_agent.visual_engine.factory_broll import render_factory_broll
```

Add helper:
```python
def _safe_factory_broll(scene: dict, output_path: Path) -> dict:
    blog = {"category": scene.get("_blog_category", ""),
            "persona": scene.get("_blog_persona", "procurement")}
    return render_factory_broll(scene, blog, output_path)
```

Update `dispatcher` tests if needed (existing tests use the fallback path which still works).

- [ ] **Step 4: Run, commit**

```bash
python -m pytest tests/video_agent/visual_engine/ -v
git add video_agent/visual_engine/factory_broll.py video_agent/visual_engine/dispatcher.py tests/video_agent/visual_engine/test_factory_broll.py
git commit -m "feat(video_agent): factory_broll selector + dispatcher wiring"
```

---

### Task 23: TDD `visual_engine/stock.py` (Pexels filler, optional)

**Files:**
- Create: `tests/video_agent/visual_engine/test_stock.py`
- Create: `video_agent/visual_engine/stock.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/video_agent/visual_engine/test_stock.py
import responses
from pathlib import Path
from unittest.mock import patch
from video_agent.visual_engine.stock import fetch_stock_image, StockError


@responses.activate
def test_fetch_falls_back_when_no_api_key(tmp_path, monkeypatch):
    monkeypatch.setattr("video_agent.visual_engine.stock._get_api_key",
                        lambda: None)
    out = fetch_stock_image("water", tmp_path / "x.jpg")
    assert out is None


@responses.activate
def test_fetch_caches_by_query(tmp_path, monkeypatch):
    monkeypatch.setattr("video_agent.visual_engine.stock._get_api_key",
                        lambda: "fake")
    monkeypatch.setattr("video_agent.visual_engine.stock.STOCK_CACHE_DIR",
                        str(tmp_path / "cache"))
    responses.add(
        responses.GET, "https://api.pexels.com/v1/search",
        json={"photos": [{"src": {"large": "https://x/img.jpg"},
                          "photographer": "Jane Doe"}]},
        status=200,
    )
    responses.add(responses.GET, "https://x/img.jpg",
                  body=b"\xff\xd8\xff\xe0fakejpeg", status=200)
    out1 = fetch_stock_image("water", tmp_path / "a.jpg")
    out2 = fetch_stock_image("water", tmp_path / "b.jpg")  # served from cache
    assert out1 and out2
    assert out1.read_bytes() == out2.read_bytes()
```

- [ ] **Step 2: Implement**

```python
# video_agent/visual_engine/stock.py
"""Pexels stock-image filler. Optional. Falls back to None when no key."""
import hashlib
import logging
import shutil
from pathlib import Path
import requests

from video_agent.config import PEXELS_API_BASE, STOCK_CACHE_DIR

log = logging.getLogger(__name__)


class StockError(RuntimeError):
    pass


def _get_api_key() -> str | None:
    try:
        from token_manager import TokenManager  # type: ignore
        return TokenManager().get_pexels_api_key()
    except Exception:
        return None


def _cache_path(query: str) -> Path:
    h = hashlib.sha1(query.encode()).hexdigest()
    return Path(STOCK_CACHE_DIR) / f"{h}.jpg"


def fetch_stock_image(query: str, output_path: Path) -> Path | None:
    """Returns output_path on success, None on no-key/no-result/network-error."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cache = _cache_path(query)
    cache.parent.mkdir(parents=True, exist_ok=True)

    if cache.exists():
        shutil.copy2(cache, output_path)
        return output_path

    api_key = _get_api_key()
    if not api_key:
        log.info("Pexels disabled (no key)")
        return None

    try:
        r = requests.get(
            f"{PEXELS_API_BASE}/search",
            params={"query": query, "per_page": 1, "orientation": "portrait"},
            headers={"Authorization": api_key},
            timeout=15,
        )
        r.raise_for_status()
        photos = r.json().get("photos", [])
        if not photos:
            return None
        url = photos[0]["src"]["large"]
        photographer = photos[0].get("photographer", "Pexels")
        img = requests.get(url, timeout=30)
        img.raise_for_status()
        cache.write_bytes(img.content)
        shutil.copy2(cache, output_path)
        log.info("Stock image '%s' by %s", query, photographer)
        return output_path
    except (requests.RequestException, OSError) as e:
        log.warning("Stock fetch failed: %s", e)
        return None
```

- [ ] **Step 3: Run, commit**

```bash
python -m pytest tests/video_agent/visual_engine/test_stock.py -v
git add video_agent/visual_engine/stock.py tests/video_agent/visual_engine/test_stock.py
git commit -m "feat(video_agent): pexels stock filler with cache"
```

---

### Task 24: Asset-tagging tool (`tools/tag_assets.py`)

Interactive CLI — no test (pure I/O). Lower priority; build if time permits.

```python
# video_agent/tools/tag_assets.py
"""Interactive: scan asset_library/factory/, prompt for tags, write manifest.json."""
import json
from pathlib import Path

CATEGORIES = [
    "wastewater_treatment", "concrete_construction", "mining",
    "agriculture_fertilizer", "oil_gas", "latex_rubber",
    "food_processing", "water_treatment", "specialty_applications", "esg",
    "all",
]
PERSONAS = ["procurement", "executive", "all"]


def _prompt_list(label: str, options: list[str]) -> list[str]:
    print(f"\n{label} (comma-separated indices):")
    for i, o in enumerate(options):
        print(f"  {i}. {o}")
    raw = input("> ").strip()
    if not raw:
        return [options[-1]]  # default to "all"
    idxs = [int(x) for x in raw.split(",") if x.strip().isdigit()]
    return [options[i] for i in idxs if 0 <= i < len(options)]


def main():
    root = Path("asset_library/factory")
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifest.json"
    existing = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"assets": []}
    known = {a["file"] for a in existing.get("assets", [])}

    files = [f for f in root.iterdir() if f.is_file()
             and f.suffix.lower() in (".mp4", ".mov", ".jpg", ".png")]
    new_files = [f for f in files if f.name not in known]
    if not new_files:
        print("No new files to tag.")
        return

    for f in new_files:
        print(f"\n=== {f.name} ===")
        cats = _prompt_list("Categories", CATEGORIES)
        personas = _prompt_list("Personas", PERSONAS)
        tags = input("Free tags (comma-separated): ").strip().split(",")
        tags = [t.strip() for t in tags if t.strip()]
        esg = input("ESG relevant? (y/N): ").strip().lower() == "y"
        existing["assets"].append({
            "file": f.name,
            "type": "video" if f.suffix.lower() in (".mp4", ".mov") else "image",
            "categories": cats, "personas": personas,
            "tags": tags, "esg_relevant": esg,
        })

    manifest_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    print(f"\nWrote {manifest_path} with {len(existing['assets'])} assets.")


if __name__ == "__main__":
    main()
```

Commit:
```bash
git add video_agent/tools/tag_assets.py
git commit -m "feat(video_agent): interactive asset tagger"
git tag video-agent-sprint-5
```

---

## Sprint 6 — Publishers (YouTube), scheduler, orchestrator MVP, CLI

### Task 25: TDD `publishers/base.py`

**Files:**
- Create: `tests/video_agent/publishers/test_base.py`
- Create: `video_agent/publishers/base.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/video_agent/publishers/test_base.py
import pytest
from datetime import datetime
from pathlib import Path
from video_agent.publishers.base import BasePublisher, make_failure_result


class _Stub(BasePublisher):
    platform = "stub"
    def upload(self, *, video_path, title, description, hashtags, blog_url,
               region, scheduled_for=None):
        return {"success": True, "platform": self.platform, "post_url": "x",
                "post_id": "1", "scheduled": False, "scheduled_for": None, "error": None}


def test_cannot_instantiate_abstract():
    with pytest.raises(TypeError):
        BasePublisher()


def test_subclass_works(tmp_path):
    p = _Stub()
    r = p.upload(video_path=tmp_path / "v.mp4", title="t", description="d",
                 hashtags=["#x"], blog_url="u", region="usa")
    assert r["success"] and r["platform"] == "stub"


def test_failure_result_shape():
    r = make_failure_result("youtube", "boom")
    assert r == {"success": False, "platform": "youtube", "post_url": None,
                 "post_id": None, "scheduled": False, "scheduled_for": None,
                 "error": "boom"}
```

- [ ] **Step 2: Implement**

```python
# video_agent/publishers/base.py
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path


def make_failure_result(platform: str, error: str) -> dict:
    return {
        "success": False, "platform": platform, "post_url": None,
        "post_id": None, "scheduled": False, "scheduled_for": None, "error": error,
    }


class BasePublisher(ABC):
    platform: str = "base"

    @abstractmethod
    def upload(self, *, video_path: Path, title: str, description: str,
               hashtags: list[str], blog_url: str, region: str,
               scheduled_for: datetime | None = None) -> dict: ...
```

- [ ] **Step 3: Commit**

```bash
python -m pytest tests/video_agent/publishers/test_base.py -v
git add video_agent/publishers/base.py tests/video_agent/publishers/test_base.py
git commit -m "feat(video_agent): BasePublisher ABC"
```

---

### Task 26: TDD `publishers/youtube.py`

**Files:**
- Create: `tests/video_agent/publishers/test_youtube.py`
- Create: `video_agent/publishers/youtube.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/video_agent/publishers/test_youtube.py
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from video_agent.publishers.youtube import YouTubePublisher


def _fake_service():
    svc = MagicMock()
    req = MagicMock()
    req.next_chunk.return_value = (None, {"id": "abc123"})
    svc.videos.return_value.insert.return_value = req
    return svc


def test_build_description_includes_blog_url():
    pub = YouTubePublisher.__new__(YouTubePublisher)  # skip __init__
    desc = pub._build_description(
        narration_hook="Calcium nitrate stops H2S.",
        blog_url="https://blog.hrsuindore.com/x", hashtags=["#x", "#y"],
    )
    assert "https://blog.hrsuindore.com/x" in desc
    assert "#Shorts" in desc
    assert "hrsuindore.com" in desc


def test_region_to_iso():
    pub = YouTubePublisher.__new__(YouTubePublisher)
    assert pub._region_to_iso("germany") == "de"
    assert pub._region_to_iso("australia") == "en"
    assert pub._region_to_iso("unknown") == "en"


def test_upload_returns_post_url(tmp_path):
    video = tmp_path / "v.mp4"; video.write_bytes(b"fake")
    with patch("video_agent.publishers.youtube.YouTubePublisher._get_service",
               return_value=_fake_service()), \
         patch("video_agent.publishers.youtube.MediaFileUpload"):
        pub = YouTubePublisher()
        r = pub.upload(
            video_path=video, title="T", description="D", hashtags=["#x"],
            blog_url="https://blog.hrsuindore.com/x", region="usa",
        )
    assert r["success"]
    assert r["post_url"] == "https://youtu.be/abc123"
    assert r["post_id"] == "abc123"


def test_scheduled_upload_uses_publishAt(tmp_path):
    video = tmp_path / "v.mp4"; video.write_bytes(b"fake")
    captured = {}
    fake_svc = _fake_service()
    fake_svc.videos.return_value.insert.side_effect = (
        lambda **kw: captured.update(kw) or fake_svc.videos.return_value.insert.return_value
    )
    when = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    with patch("video_agent.publishers.youtube.YouTubePublisher._get_service",
               return_value=fake_svc), \
         patch("video_agent.publishers.youtube.MediaFileUpload"):
        pub = YouTubePublisher()
        r = pub.upload(
            video_path=video, title="T", description="D", hashtags=["#x"],
            blog_url="u", region="usa", scheduled_for=when,
        )
    assert r["scheduled"] is True
    body = captured["body"]
    assert body["status"]["privacyStatus"] == "private"
    assert body["status"]["publishAt"] == when.isoformat()
```

- [ ] **Step 2: Implement**

```python
# video_agent/publishers/youtube.py
"""YouTube Data API v3 uploader. Reuses Google OAuth from blog_agent_v3."""
import logging
from datetime import datetime
from pathlib import Path

from video_agent.config import REGION_TO_ISO_LANG
from video_agent.publishers.base import BasePublisher, make_failure_result

log = logging.getLogger(__name__)

YT_SCOPE = "https://www.googleapis.com/auth/youtube.upload"


class YouTubePublisher(BasePublisher):
    platform = "youtube"

    def __init__(self, service=None):
        self._service = service or self._get_service()

    @staticmethod
    def _get_service():
        # Reuse blog_agent_v3.py's OAuth flow.
        try:
            from blog_agent_v3 import _get_blogger_service  # type: ignore
        except ImportError:
            from googleapiclient.discovery import build
            from google.oauth2.credentials import Credentials
            import pickle
            with open("token.pickle", "rb") as f:
                creds: Credentials = pickle.load(f)
            return build("youtube", "v3", credentials=creds)
        # Fall back to direct build via picked creds
        from googleapiclient.discovery import build
        import pickle
        with open("token.pickle", "rb") as f:
            creds = pickle.load(f)
        return build("youtube", "v3", credentials=creds)

    def _region_to_iso(self, region: str) -> str:
        return REGION_TO_ISO_LANG.get(region, "en")

    def _build_description(self, narration_hook: str, blog_url: str,
                           hashtags: list[str]) -> str:
        tags = " ".join(t if t.startswith("#") else f"#{t}" for t in hashtags[:20])
        return (
            f"{narration_hook}\n\n"
            f"📖 Full breakdown: {blog_url}\n"
            f"🌐 https://hrsuindore.com\n"
            f"📩 Spec sheet & quote — comment 'SPEC' or DM us\n\n"
            f"#Shorts {tags}"
        )

    def upload(self, *, video_path: Path, title: str, description: str,
               hashtags: list[str], blog_url: str, region: str,
               scheduled_for: datetime | None = None) -> dict:
        try:
            from googleapiclient.http import MediaFileUpload
            video_path = Path(video_path)
            if not video_path.exists():
                return make_failure_result(self.platform, f"missing file {video_path}")

            full_desc = self._build_description(
                narration_hook=description.split("\n")[0][:200],
                blog_url=blog_url, hashtags=hashtags,
            )
            body = {
                "snippet": {
                    "title": title[:100],
                    "description": full_desc,
                    "tags": [t.lstrip("#") for t in hashtags[:30]],
                    "categoryId": "27",
                    "defaultLanguage": self._region_to_iso(region),
                },
                "status": {
                    "privacyStatus": "public" if not scheduled_for else "private",
                    "publishAt": scheduled_for.isoformat() if scheduled_for else None,
                    "selfDeclaredMadeForKids": False,
                },
            }
            media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True,
                                    mimetype="video/mp4")
            req = self._service.videos().insert(
                part="snippet,status", body=body, media_body=media,
            )
            response = None
            while response is None:
                _status, response = req.next_chunk()
            video_id = response["id"]
            return {
                "success": True, "platform": self.platform,
                "post_url": f"https://youtu.be/{video_id}",
                "post_id": video_id,
                "scheduled": scheduled_for is not None,
                "scheduled_for": scheduled_for, "error": None,
            }
        except Exception as e:
            log.exception("YouTube upload failed")
            return make_failure_result(self.platform, str(e))
```

- [ ] **Step 3: Run, commit**

```bash
python -m pytest tests/video_agent/publishers/test_youtube.py -v
git add video_agent/publishers/youtube.py tests/video_agent/publishers/test_youtube.py
git commit -m "feat(video_agent): YouTube Shorts publisher (reuses Google OAuth)"
```

---

### Task 27: TDD `scheduler.py`

**Files:**
- Create: `tests/video_agent/test_scheduler.py`
- Create: `video_agent/scheduler.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/video_agent/test_scheduler.py
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from video_agent.scheduler import Scheduler, _compute_target_utc


def test_compute_target_uses_region_tz():
    now = datetime(2026, 5, 9, 6, 0, tzinfo=timezone.utc)  # Sat 06:00 UTC
    t = _compute_target_utc("australia", now=now, hour=9, weekday=1)  # Tue 9am Sydney
    assert t.weekday() == 1
    assert t > now


def test_enqueue_creates_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr("video_agent.scheduler.SCHEDULER_JOBSTORE_URL",
                        f"sqlite:///{tmp_path / 'q.db'}")
    fake_sched = MagicMock()
    fake_sched.add_job.return_value = MagicMock(id="job-1")
    with patch("video_agent.scheduler.BackgroundScheduler", return_value=fake_sched):
        s = Scheduler()
        ids = s.enqueue_for_region(
            video_path=tmp_path / "v.mp4",
            blog_record={"region": "usa", "blog_id": "b1",
                         "url": "https://blog/x", "title": "T"},
            platforms=["linkedin", "instagram"],
            title="T", description="D", hashtags=["#x"],
        )
    assert len(ids) == 2
    assert fake_sched.add_job.call_count == 2


def test_enqueue_skips_youtube(tmp_path, monkeypatch):
    monkeypatch.setattr("video_agent.scheduler.SCHEDULER_JOBSTORE_URL",
                        f"sqlite:///{tmp_path / 'q.db'}")
    fake_sched = MagicMock()
    fake_sched.add_job.return_value = MagicMock(id="x")
    with patch("video_agent.scheduler.BackgroundScheduler", return_value=fake_sched):
        s = Scheduler()
        ids = s.enqueue_for_region(
            video_path=tmp_path / "v.mp4",
            blog_record={"region": "usa", "blog_id": "b1",
                         "url": "u", "title": "T"},
            platforms=["youtube"],
            title="T", description="D", hashtags=["#x"],
        )
    # YouTube returns its own scheduled-by-platform path; scheduler doesn't enqueue.
    assert ids == []
```

- [ ] **Step 2: Implement**

```python
# video_agent/scheduler.py
"""APScheduler-driven post queue with regional timing + retry backoff."""
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

import pytz

from video_agent.config import (
    SCHEDULER_JOBSTORE_URL, SCHEDULER_RETRY_BACKOFF_S, SCHEDULER_MAX_ATTEMPTS,
    REGION_TO_TZ, FAILURE_LOG,
)
from config import REGION_POSTING_SCHEDULE  # type: ignore

log = logging.getLogger(__name__)

# Default fallback if config region missing
DEFAULT_HOUR = 9
DEFAULT_WEEKDAY = 1  # Tuesday


def _compute_target_utc(region: str, *, now: datetime | None = None,
                        hour: int | None = None, weekday: int | None = None) -> datetime:
    tz = pytz.timezone(REGION_TO_TZ.get(region, "UTC"))
    sched = REGION_POSTING_SCHEDULE.get(region, {}) if "REGION_POSTING_SCHEDULE" in dir() else {}
    target_hour = hour if hour is not None else sched.get("hour", DEFAULT_HOUR)
    target_weekday = weekday if weekday is not None else sched.get("weekday", DEFAULT_WEEKDAY)
    now_utc = now or datetime.now(timezone.utc)
    now_local = now_utc.astimezone(tz)
    days_ahead = (target_weekday - now_local.weekday()) % 7
    candidate = now_local.replace(hour=target_hour, minute=0, second=0, microsecond=0) \
                + timedelta(days=days_ahead)
    if candidate <= now_local:
        candidate += timedelta(days=7)
    return candidate.astimezone(timezone.utc)


def _publish_job(platform: str, video_path: str, title: str, description: str,
                 hashtags: list, blog_url: str, region: str, attempt: int = 1) -> None:
    """Body executed by APScheduler. Imports lazily to avoid heavy module load on enqueue."""
    try:
        if platform == "linkedin":
            from video_agent.publishers.linkedin import LinkedInVideoPublisher as P
        elif platform == "instagram":
            from video_agent.publishers.instagram import InstagramPublisher as P
        else:
            raise ValueError(f"unknown platform {platform}")
        pub = P()
        result = pub.upload(
            video_path=Path(video_path), title=title, description=description,
            hashtags=hashtags, blog_url=blog_url, region=region,
        )
        if not result.get("success"):
            raise RuntimeError(result.get("error") or "publish failed")
        log.info("Published to %s: %s", platform, result.get("post_url"))
    except Exception as e:
        log.warning("Publish %s attempt %d failed: %s", platform, attempt, e)
        if attempt >= SCHEDULER_MAX_ATTEMPTS:
            with open(FAILURE_LOG, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now(timezone.utc).isoformat()} {platform} {video_path} {e}\n")
            return
        # Reschedule self
        from apscheduler.schedulers.background import BackgroundScheduler
        # The running scheduler will process this via its persistent jobstore.
        delay = SCHEDULER_RETRY_BACKOFF_S[attempt - 1]
        run_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
        s = Scheduler()
        s.scheduler.add_job(
            _publish_job, "date", run_date=run_at,
            args=[platform, video_path, title, description, hashtags,
                  blog_url, region, attempt + 1],
            id=f"retry-{platform}-{attempt}-{int(run_at.timestamp())}",
        )


class Scheduler:
    def __init__(self):
        jobstore = SQLAlchemyJobStore(url=SCHEDULER_JOBSTORE_URL)
        self.scheduler = BackgroundScheduler(jobstores={"default": jobstore},
                                              timezone=pytz.UTC)

    def start(self) -> None:
        if not self.scheduler.running:
            self.scheduler.start()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def enqueue_for_region(self, *, video_path: Path, blog_record: dict,
                           platforms: list[str], title: str, description: str,
                           hashtags: list[str]) -> list[str]:
        region = blog_record.get("region", "default")
        target_utc = _compute_target_utc(region)
        out_ids = []
        for plat in platforms:
            if plat == "youtube":
                # YouTube schedules natively via publishAt; not queued here.
                continue
            job = self.scheduler.add_job(
                _publish_job, "date", run_date=target_utc,
                args=[plat, str(video_path), title, description, hashtags,
                      blog_record.get("url", ""), region, 1],
                id=f"{plat}-{blog_record.get('blog_id')}-{int(target_utc.timestamp())}",
                replace_existing=True,
            )
            out_ids.append(job.id)
        return out_ids

    def list_queue(self) -> list[dict]:
        return [{"id": j.id, "next_run": j.next_run_time, "name": j.name}
                for j in self.scheduler.get_jobs()]

    def cancel(self, queue_id: str) -> bool:
        try:
            self.scheduler.remove_job(queue_id)
            return True
        except Exception:
            return False
```

- [ ] **Step 3: Install pytz if not already**

```bash
pip install pytz
```

- [ ] **Step 4: Run, commit**

```bash
python -m pytest tests/video_agent/test_scheduler.py -v
git add video_agent/scheduler.py tests/video_agent/test_scheduler.py
git commit -m "feat(video_agent): APScheduler-based regional post queue"
```

---

### Task 28: TDD `orchestrator.py` (MVP — YouTube only)

**Files:**
- Create: `tests/video_agent/test_orchestrator.py`
- Create: `video_agent/orchestrator.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/video_agent/test_orchestrator.py
from pathlib import Path
from unittest.mock import patch, MagicMock
from video_agent.orchestrator import generate_video_for_blog


SAMPLE_BLOG = {
    "blog_id": "test-1", "title": "X",
    "url": "https://blog/x", "region": "australia",
    "persona": "procurement", "category": "wastewater_treatment",
    "subcategory": "h2s",
    "content_html": "<p>x</p>", "summary": "s",
}


def test_dry_run_skips_publish(tmp_path, monkeypatch):
    monkeypatch.setattr("video_agent.orchestrator.OUTPUT_BASE", str(tmp_path))
    with patch("video_agent.orchestrator.build_script") as mb, \
         patch("video_agent.orchestrator.synthesize") as mv, \
         patch("video_agent.orchestrator.generate_all_visuals") as mg, \
         patch("video_agent.orchestrator.generate_srt") as ms, \
         patch("video_agent.orchestrator.compose_short") as mc, \
         patch("video_agent.orchestrator.YouTubePublisher") as myt:
        mb.return_value = {"narration": "n " * 30, "scenes": [{"index": 0, "duration_s": 5,
                            "visual_type": "text_card", "visual_spec": {"layout": "hook"},
                            "on_screen_text": "X", "transition_in": "fade",
                            "narration": "n"} for _ in range(3)],
                            "title": "T", "description": "D", "hashtags": ["#x"],
                            "extraction_metadata": {"tier_used": 1, "numeric_count": 3,
                                                    "punch_points_count": 0,
                                                    "fell_back_to_template": False}}
        mv.return_value = {"audio_path": tmp_path / "v.mp3", "duration_s": 30,
                           "voice_used": "x", "engine_used": "edge-tts", "fell_back": False}
        mg.return_value = [{"asset_path": tmp_path / "0.png", "is_video_clip": False,
                            "duration_s": None, "generator_used": "text_card"}] * 3
        ms.return_value = tmp_path / "s.srt"
        mc.return_value = tmp_path / "out.mp4"
        (tmp_path / "out.mp4").write_bytes(b"fake")

        result = generate_video_for_blog(SAMPLE_BLOG, publish_to=("youtube",), dry_run=True)
    assert result["dry_run"] is True
    myt.assert_not_called()


def test_publishes_to_youtube_when_not_dry(tmp_path, monkeypatch):
    monkeypatch.setattr("video_agent.orchestrator.OUTPUT_BASE", str(tmp_path))
    fake_pub = MagicMock()
    fake_pub.upload.return_value = {"success": True, "platform": "youtube",
                                     "post_url": "https://youtu.be/x", "post_id": "x",
                                     "scheduled": False, "scheduled_for": None,
                                     "error": None}
    with patch("video_agent.orchestrator.build_script") as mb, \
         patch("video_agent.orchestrator.synthesize") as mv, \
         patch("video_agent.orchestrator.generate_all_visuals") as mg, \
         patch("video_agent.orchestrator.generate_srt") as ms, \
         patch("video_agent.orchestrator.compose_short") as mc, \
         patch("video_agent.orchestrator.YouTubePublisher", return_value=fake_pub), \
         patch("video_agent.orchestrator.history.append_video") as mh:
        mb.return_value = {"narration": "n " * 30, "scenes": [{"index": 0, "duration_s": 5,
                            "visual_type": "text_card", "visual_spec": {"layout": "hook"},
                            "on_screen_text": "X", "transition_in": "fade",
                            "narration": "n"}] * 3,
                            "title": "T", "description": "D", "hashtags": ["#x"],
                            "extraction_metadata": {"tier_used": 1, "numeric_count": 3,
                                                    "punch_points_count": 0,
                                                    "fell_back_to_template": False}}
        mv.return_value = {"audio_path": tmp_path / "v.mp3", "duration_s": 30,
                           "voice_used": "x", "engine_used": "edge-tts", "fell_back": False}
        mg.return_value = [{"asset_path": tmp_path / "0.png", "is_video_clip": False,
                            "duration_s": None, "generator_used": "text_card"}] * 3
        ms.return_value = tmp_path / "s.srt"
        mc.return_value = tmp_path / "out.mp4"
        (tmp_path / "out.mp4").write_bytes(b"fake")

        result = generate_video_for_blog(SAMPLE_BLOG, publish_to=("youtube",),
                                          scheduled=False, dry_run=False)
    fake_pub.upload.assert_called_once()
    assert result["publish_results"]["youtube"]["success"]
    mh.assert_called_once()
```

- [ ] **Step 2: Implement**

```python
# video_agent/orchestrator.py
"""End-to-end pipeline glue. One call per blog."""
import logging
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path

from video_agent.config import OUTPUT_BASE, COSTS_LOG
from video_agent.script_builder import build_script
from video_agent.voiceover import synthesize
from video_agent.visual_engine.dispatcher import generate_all_visuals
from video_agent.subtitles import generate_srt
from video_agent.composer import compose_short
from video_agent import history
from video_agent.publishers.youtube import YouTubePublisher

log = logging.getLogger(__name__)


def _slug(record: dict) -> str:
    raw = record.get("title") or record.get("blog_id") or "video"
    return re.sub(r"[^a-z0-9]+", "-", raw.lower())[:60].strip("-")


def _workspace(record: dict) -> Path:
    return Path(OUTPUT_BASE) / f"{date.today().isoformat()}_{_slug(record)}"


def _emit_cost(blog_id: str, elapsed_s: float, metadata: dict) -> None:
    line = (f"{datetime.now(timezone.utc).isoformat()} blog_id={blog_id} "
            f"elapsed_s={elapsed_s:.1f} tier={metadata.get('tier_used')} "
            f"numeric={metadata.get('numeric_count')}\n")
    Path(COSTS_LOG).write_text(
        (Path(COSTS_LOG).read_text(encoding="utf-8") if Path(COSTS_LOG).exists() else "") + line,
        encoding="utf-8",
    )


def generate_video_for_blog(blog_record: dict, *,
                             publish_to: tuple = ("youtube",),
                             scheduled: bool = True,
                             dry_run: bool = False,
                             force: bool = False) -> dict:
    t0 = time.time()
    blog_id = blog_record["blog_id"]

    existing = history.find_by_blog_id(blog_id)
    if existing and not force and Path(existing.get("video_path", "")).exists():
        log.info("Cached video for blog %s — skipping", blog_id)
        return {"blog_id": blog_id, "cached": True,
                "video_path": existing["video_path"], "publish_results": {}}

    workspace = _workspace(blog_record); workspace.mkdir(parents=True, exist_ok=True)

    script = build_script(blog_record, output_dir=workspace, force=force)
    voice = synthesize(script["narration"], workspace / "voiceover.mp3",
                        region=blog_record.get("region", "default"))
    # Inject blog context into scenes for factory_broll
    for s in script["scenes"]:
        s["_blog_category"] = blog_record.get("category", "")
        s["_blog_persona"] = blog_record.get("persona", "procurement")
    visuals = generate_all_visuals(script["scenes"], workspace / "scenes")
    srt = generate_srt(voice["audio_path"], workspace / "subtitles.srt",
                        narration_hint=script["narration"])
    out_mp4 = compose_short(
        scenes=script["scenes"], visual_results=visuals,
        voiceover=voice, subtitle_path=srt,
        output_path=workspace / "video_short.mp4",
        region=blog_record.get("region", "default"),
        persona=blog_record.get("persona", "procurement"),
    )

    publish_results = {}
    if dry_run:
        log.info("[DRY RUN] Would publish %s to %s", out_mp4, publish_to)
    else:
        if "youtube" in publish_to:
            try:
                pub = YouTubePublisher()
                publish_results["youtube"] = pub.upload(
                    video_path=out_mp4, title=script["title"],
                    description=script["description"], hashtags=script["hashtags"],
                    blog_url=blog_record.get("url", ""),
                    region=blog_record.get("region", "default"),
                    scheduled_for=None,  # YT scheduling added in Sprint 7+
                )
            except Exception as e:
                log.exception("YouTube publish errored")
                publish_results["youtube"] = {"success": False, "platform": "youtube",
                                                "error": str(e), "post_url": None,
                                                "post_id": None, "scheduled": False,
                                                "scheduled_for": None}

        if scheduled and any(p in publish_to for p in ("linkedin", "instagram")):
            try:
                from video_agent.scheduler import Scheduler
                s = Scheduler(); s.start()
                queue_ids = s.enqueue_for_region(
                    video_path=out_mp4, blog_record=blog_record,
                    platforms=[p for p in publish_to if p in ("linkedin", "instagram")],
                    title=script["title"], description=script["description"],
                    hashtags=script["hashtags"],
                )
                for plat in ("linkedin", "instagram"):
                    if plat in publish_to:
                        publish_results[plat] = {"success": True, "platform": plat,
                                                  "post_url": None, "post_id": None,
                                                  "scheduled": True, "scheduled_for": None,
                                                  "error": None,
                                                  "queue_id": next(iter(queue_ids), None)}
            except Exception as e:
                log.exception("Scheduler enqueue errored")

    elapsed = time.time() - t0
    record = {
        "blog_id": blog_id, "video_path": str(out_mp4),
        "duration_s": voice["duration_s"],
        "region": blog_record.get("region"),
        "persona": blog_record.get("persona"),
        "category": blog_record.get("category"),
        "publish_results": publish_results,
        "extraction_metadata": script.get("extraction_metadata", {}),
        "elapsed_s": elapsed,
    }
    history.append_video(record)
    _emit_cost(blog_id, elapsed, script.get("extraction_metadata", {}))

    return {**record, "dry_run": dry_run, "errors": []}
```

- [ ] **Step 3: Run, commit**

```bash
python -m pytest tests/video_agent/test_orchestrator.py -v
git add video_agent/orchestrator.py tests/video_agent/test_orchestrator.py
git commit -m "feat(video_agent): orchestrator MVP (YouTube + scheduled queue stubs)"
```

---

### Task 29: `main.py` CLI

**Files:**
- Create: `tests/video_agent/test_main.py`
- Create: `video_agent/main.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/video_agent/test_main.py
import json
from pathlib import Path
from unittest.mock import patch, MagicMock
from video_agent.main import main


def test_dry_run_from_blog_id(monkeypatch, tmp_path, capsys):
    blog_history = tmp_path / "blog_history.json"
    blog_history.write_text(json.dumps({"posts": [
        {"blog_id": "abc", "title": "T", "url": "u", "region": "usa",
         "persona": "procurement", "category": "wastewater_treatment",
         "subcategory": "h2s", "content_html": "<p>x</p>", "summary": "s"}
    ]}), encoding="utf-8")
    with patch("video_agent.main.BLOG_HISTORY_FILE", str(blog_history)), \
         patch("video_agent.main.generate_video_for_blog") as m:
        m.return_value = {"blog_id": "abc", "video_path": "v", "duration_s": 30,
                          "publish_results": {}, "extraction_metadata": {}, "elapsed_s": 1}
        rc = main(["--from-blog-id", "abc", "--dry-run"])
    assert rc == 0
    m.assert_called_once()


def test_blog_id_not_found(monkeypatch, tmp_path):
    blog_history = tmp_path / "blog_history.json"
    blog_history.write_text(json.dumps({"posts": []}), encoding="utf-8")
    with patch("video_agent.main.BLOG_HISTORY_FILE", str(blog_history)):
        rc = main(["--from-blog-id", "nope"])
    assert rc != 0


def test_stats(tmp_path, monkeypatch):
    monkeypatch.setattr("video_agent.history.HISTORY_PATH", tmp_path / "vh.json")
    rc = main(["--stats"])
    assert rc == 0
```

- [ ] **Step 2: Implement**

```python
# video_agent/main.py
"""CLI entry point for video_agent."""
import argparse
import json
import logging
import sys
from pathlib import Path

from video_agent.orchestrator import generate_video_for_blog
from video_agent import history
from video_agent.config import LOG_FILE

BLOG_HISTORY_FILE = "blog_history.json"

log = logging.getLogger(__name__)


def _load_blog_record(blog_id: str) -> dict | None:
    p = Path(BLOG_HISTORY_FILE)
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    for post in data.get("posts", []):
        if post.get("blog_id") == blog_id:
            return post
    return None


def _load_latest_blog() -> dict | None:
    p = Path(BLOG_HISTORY_FILE)
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    posts = data.get("posts", [])
    return posts[-1] if posts else None


def _setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)],
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="video_agent")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--from-blog-id", metavar="ID")
    src.add_argument("--latest", action="store_true")
    src.add_argument("--backfill", action="store_true")
    src.add_argument("--stats", action="store_true")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--region", default=None)
    p.add_argument("--platforms", default="youtube",
                   help="comma-separated: youtube,linkedin,instagram")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--no-schedule", action="store_true")
    args = p.parse_args(argv)

    _setup_logging()

    if args.stats:
        s = history.stats(days=30)
        print(json.dumps(s, indent=2, default=str))
        return 0

    platforms = tuple(x.strip() for x in args.platforms.split(",") if x.strip())

    if args.from_blog_id:
        rec = _load_blog_record(args.from_blog_id)
        if not rec:
            print(f"blog_id not found: {args.from_blog_id}", file=sys.stderr)
            return 2
        result = generate_video_for_blog(
            rec, publish_to=platforms, scheduled=not args.no_schedule,
            dry_run=args.dry_run, force=args.force,
        )
        print(json.dumps({k: str(v) for k, v in result.items() if k != "publish_results"},
                          indent=2))
        return 0

    if args.latest:
        rec = _load_latest_blog()
        if not rec:
            print("blog_history.json empty", file=sys.stderr); return 2
        result = generate_video_for_blog(
            rec, publish_to=platforms, scheduled=not args.no_schedule,
            dry_run=args.dry_run, force=args.force,
        )
        print(f"Done: {result.get('video_path')}")
        return 0

    if args.backfill:
        p_blogs = Path(BLOG_HISTORY_FILE)
        if not p_blogs.exists():
            print("blog_history.json missing", file=sys.stderr); return 2
        posts = json.loads(p_blogs.read_text(encoding="utf-8")).get("posts", [])
        if args.region:
            posts = [b for b in posts if b.get("region") == args.region]
        posts = posts[-args.limit:]
        for rec in posts:
            try:
                generate_video_for_blog(
                    rec, publish_to=platforms, scheduled=not args.no_schedule,
                    dry_run=args.dry_run, force=args.force,
                )
            except Exception as e:
                log.exception("Failed for %s: %s", rec.get("blog_id"), e)
        return 0

    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Run, commit**

```bash
python -m pytest tests/video_agent/test_main.py -v
git add video_agent/main.py tests/video_agent/test_main.py
git commit -m "feat(video_agent): CLI entrypoint with blog_id/latest/backfill/stats"
git tag video-agent-sprint-6
```

---

## Sprint 7 — LinkedIn publisher

### Task 30: `token_manager.py` extensions

Existing project file `token_manager.py` needs new getters. Add (don't replace) the following methods. Find the existing class and append:

```python
def get_instagram_user_id(self) -> str:
    return self._read_secret("IG_USER_ID")

def get_instagram_publishing_enabled(self) -> bool:
    raw = self._read_secret("IG_PUBLISHING_ENABLED", default="false")
    return raw.strip().lower() in ("true", "1", "yes")

def get_github_token(self) -> str:
    return self._read_secret("GITHUB_TOKEN")

def get_github_cdn_repo(self) -> str:
    return self._read_secret("GITHUB_CDN_REPO")

def get_pexels_api_key(self) -> str | None:
    try:
        return self._read_secret("PEXELS_API_KEY")
    except KeyError:
        return None
```

If `_read_secret` doesn't exist with `default=` support, add a small wrapper. Test:

```python
# tests/video_agent/test_token_manager_ext.py
from unittest.mock import patch
from token_manager import TokenManager  # type: ignore


def test_ig_disabled_default(tmp_path, monkeypatch):
    secrets = tmp_path / "secrets.txt"; secrets.write_text("FOO=bar\n")
    monkeypatch.setattr("token_manager.SECRETS_PATH", str(secrets))
    tm = TokenManager()
    assert tm.get_instagram_publishing_enabled() is False
```

Commit:
```bash
git add token_manager.py tests/video_agent/test_token_manager_ext.py
git commit -m "feat(token_manager): IG/GitHub/Pexels getters"
```

---

### Task 31: TDD `publishers/linkedin.py`

**Files:**
- Create: `tests/video_agent/publishers/test_linkedin.py`
- Create: `video_agent/publishers/linkedin.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/video_agent/publishers/test_linkedin.py
import responses
from pathlib import Path
from unittest.mock import patch, MagicMock
from video_agent.publishers.linkedin import LinkedInVideoPublisher


@responses.activate
def test_three_step_upload(tmp_path):
    responses.add(
        responses.POST,
        "https://api.linkedin.com/rest/videos?action=initializeUpload",
        json={"value": {"video": "urn:li:video:abc",
                         "uploadInstructions": [
                             {"uploadUrl": "https://up/x", "firstByte": 0, "lastByte": 9}
                         ],
                         "uploadToken": "tok"}},
        status=200,
    )
    responses.add(responses.PUT, "https://up/x",
                  status=200, headers={"etag": "e1"})
    responses.add(
        responses.POST,
        "https://api.linkedin.com/rest/videos?action=finalizeUpload",
        json={"value": {}},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.linkedin.com/rest/videos/urn:li:video:abc",
        json={"status": {"processed": True}},
        status=200,
    )
    responses.add(
        responses.POST, "https://api.linkedin.com/rest/posts",
        json={"id": "urn:li:share:99"},
        status=201, headers={"x-restli-id": "urn:li:share:99"},
    )
    video = tmp_path / "v.mp4"; video.write_bytes(b"x" * 10)
    with patch("video_agent.publishers.linkedin.LinkedInVideoPublisher._access_token",
               return_value="fake-token"), \
         patch("video_agent.publishers.linkedin.LinkedInVideoPublisher._page_urn",
               return_value="urn:li:organization:1"):
        pub = LinkedInVideoPublisher()
        r = pub.upload(video_path=video, title="T",
                        description="D\n\nblah", hashtags=["#x"],
                        blog_url="u", region="usa")
    assert r["success"]
    assert r["post_id"] == "urn:li:share:99"
```

- [ ] **Step 2: Implement**

```python
# video_agent/publishers/linkedin.py
"""LinkedIn Page video publisher (Videos API + UGC Posts)."""
import logging
import time
from datetime import datetime
from pathlib import Path
import requests

from video_agent.publishers.base import BasePublisher, make_failure_result

log = logging.getLogger(__name__)

API_BASE = "https://api.linkedin.com/rest"
HEADERS_BASE = {
    "LinkedIn-Version": "202604",
    "X-Restli-Protocol-Version": "2.0.0",
}


class LinkedInVideoPublisher(BasePublisher):
    platform = "linkedin"

    def __init__(self):
        pass

    def _access_token(self) -> str:
        try:
            from token_manager import TokenManager  # type: ignore
            return TokenManager().get_linkedin_token()
        except Exception as e:
            raise RuntimeError(f"no LinkedIn token: {e}")

    def _page_urn(self) -> str:
        try:
            from token_manager import TokenManager  # type: ignore
            return TokenManager().get_linkedin_page_urn()
        except Exception:
            return "urn:li:organization:0"

    def _auth_headers(self) -> dict:
        return {**HEADERS_BASE, "Authorization": f"Bearer {self._access_token()}"}

    def _initialize(self, owner_urn: str, file_size: int) -> dict:
        body = {"initializeUploadRequest": {"owner": owner_urn,
                                              "fileSizeBytes": file_size}}
        r = requests.post(f"{API_BASE}/videos?action=initializeUpload",
                          headers=self._auth_headers(), json=body, timeout=30)
        r.raise_for_status()
        return r.json()["value"]

    def _upload_chunks(self, instructions: list[dict], video_path: Path) -> list[str]:
        etags = []
        with open(video_path, "rb") as f:
            for inst in instructions:
                first = int(inst["firstByte"])
                last = int(inst["lastByte"])
                f.seek(first)
                chunk = f.read(last - first + 1)
                r = requests.put(inst["uploadUrl"], data=chunk, timeout=120)
                r.raise_for_status()
                etag = r.headers.get("etag") or r.headers.get("ETag")
                if etag:
                    etags.append(etag)
        return etags

    def _finalize(self, video_urn: str, upload_token: str, etags: list[str]) -> None:
        body = {"finalizeUploadRequest": {
            "video": video_urn,
            "uploadToken": upload_token,
            "uploadedPartIds": etags,
        }}
        r = requests.post(f"{API_BASE}/videos?action=finalizeUpload",
                          headers=self._auth_headers(), json=body, timeout=30)
        r.raise_for_status()

    def _wait_processed(self, video_urn: str, timeout_s: int = 300) -> bool:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            r = requests.get(f"{API_BASE}/videos/{video_urn}",
                              headers=self._auth_headers(), timeout=30)
            if r.ok and r.json().get("status", {}).get("processed"):
                return True
            time.sleep(5)
        return False

    def _create_post(self, owner_urn: str, video_urn: str,
                      caption: str) -> tuple[str, str]:
        body = {
            "author": owner_urn,
            "commentary": caption,
            "visibility": "PUBLIC",
            "distribution": {"feedDistribution": "MAIN_FEED",
                              "targetEntities": [], "thirdPartyDistributionChannels": []},
            "content": {"media": {"id": video_urn, "title": ""}},
            "lifecycleState": "PUBLISHED",
        }
        r = requests.post(f"{API_BASE}/posts",
                          headers=self._auth_headers(), json=body, timeout=30)
        r.raise_for_status()
        post_id = r.headers.get("x-restli-id") or r.json().get("id", "")
        return post_id, f"https://www.linkedin.com/feed/update/{post_id}"

    def upload(self, *, video_path: Path, title: str, description: str,
               hashtags: list[str], blog_url: str, region: str,
               scheduled_for: datetime | None = None) -> dict:
        try:
            video_path = Path(video_path)
            owner = self._page_urn()
            init = self._initialize(owner, video_path.stat().st_size)
            etags = self._upload_chunks(init["uploadInstructions"], video_path)
            self._finalize(init["video"], init["uploadToken"], etags)
            self._wait_processed(init["video"])
            tags = " ".join(t if t.startswith("#") else f"#{t}" for t in hashtags[:15])
            caption = (
                f"{description.split(chr(10))[0]}\n\n"
                f"{description}\n\n"
                f"🔗 {blog_url}\n📩 DM us for spec sheet & quote.\n\n{tags}"
            )[:3000]
            post_id, post_url = self._create_post(owner, init["video"], caption)
            return {"success": True, "platform": self.platform,
                    "post_url": post_url, "post_id": post_id,
                    "scheduled": False, "scheduled_for": None, "error": None}
        except Exception as e:
            log.exception("LinkedIn upload failed")
            return make_failure_result(self.platform, str(e))
```

- [ ] **Step 3: Run, commit**

```bash
python -m pytest tests/video_agent/publishers/test_linkedin.py -v
git add video_agent/publishers/linkedin.py tests/video_agent/publishers/test_linkedin.py
git commit -m "feat(video_agent): LinkedIn page video publisher (3-step + post)"
```

---

### Task 32: Sprint 7 verification

```bash
python -m pytest tests/video_agent/ -v
git tag video-agent-sprint-7
```

---

## Sprint 8 — Instagram, GitHub CDN, blog hook, docs

### Task 33: TDD `publishers/instagram.py` (gated)

**Files:**
- Create: `tests/video_agent/publishers/test_instagram.py`
- Create: `video_agent/publishers/instagram.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/video_agent/publishers/test_instagram.py
from pathlib import Path
from unittest.mock import patch, MagicMock
from video_agent.publishers.instagram import InstagramPublisher


def test_disabled_returns_no_op(tmp_path):
    fake_tm = MagicMock()
    fake_tm.get_instagram_publishing_enabled.return_value = False
    with patch("video_agent.publishers.instagram._tm", return_value=fake_tm):
        pub = InstagramPublisher()
        r = pub.upload(video_path=tmp_path / "v.mp4", title="t",
                        description="d", hashtags=["#x"], blog_url="u", region="usa")
    assert r["success"] is False
    assert "Meta app review" in r["error"]


def test_enabled_runs_two_step(tmp_path, monkeypatch):
    fake_tm = MagicMock()
    fake_tm.get_instagram_publishing_enabled.return_value = True
    fake_tm.get_instagram_user_id.return_value = "ig123"
    fake_tm.get_facebook_token.return_value = "tok"
    video = tmp_path / "v.mp4"; video.write_bytes(b"x")
    with patch("video_agent.publishers.instagram._tm", return_value=fake_tm), \
         patch("video_agent.publishers.instagram.upload_to_github_release",
               return_value="https://github/x.mp4"), \
         patch("video_agent.publishers.instagram.requests") as mr:
        mr.post.side_effect = [
            MagicMock(ok=True, json=lambda: {"id": "container-1"}),
            MagicMock(ok=True, json=lambda: {"id": "media-99"}),
        ]
        mr.get.side_effect = [
            MagicMock(ok=True, json=lambda: {"status_code": "FINISHED"}),
            MagicMock(ok=True, json=lambda: {"permalink": "https://ig/p/x"}),
        ]
        pub = InstagramPublisher()
        r = pub.upload(video_path=video, title="t", description="d",
                        hashtags=["#x"], blog_url="u", region="usa")
    assert r["success"]
    assert r["post_url"] == "https://ig/p/x"
```

- [ ] **Step 2: Implement**

```python
# video_agent/publishers/instagram.py
"""Instagram Reels publisher. Gated by IG_PUBLISHING_ENABLED secret."""
import logging
import time
from datetime import datetime
from pathlib import Path
import requests

from video_agent.publishers.base import BasePublisher, make_failure_result

log = logging.getLogger(__name__)
GRAPH = "https://graph.facebook.com/v19.0"


def _tm():
    from token_manager import TokenManager  # type: ignore
    return TokenManager()


def upload_to_github_release(video_path: Path) -> str:
    """Upload an MP4 as a GitHub Release asset; return the public download URL."""
    from github import Github
    tm = _tm()
    gh = Github(tm.get_github_token())
    repo = gh.get_repo(tm.get_github_cdn_repo())
    tag = f"video-{int(time.time())}"
    release = repo.create_git_release(tag=tag, name=tag, message="auto", draft=False)
    asset = release.upload_asset(str(video_path), content_type="video/mp4")
    return asset.browser_download_url


class InstagramPublisher(BasePublisher):
    platform = "instagram"

    def upload(self, *, video_path: Path, title: str, description: str,
               hashtags: list[str], blog_url: str, region: str,
               scheduled_for: datetime | None = None) -> dict:
        tm = _tm()
        if not tm.get_instagram_publishing_enabled():
            return make_failure_result(
                self.platform,
                "IG publishing disabled — pending Meta app review",
            )

        try:
            ig_user_id = tm.get_instagram_user_id()
            token = tm.get_facebook_token()  # must already exist in TokenManager
            video_url = upload_to_github_release(Path(video_path))

            tags = " ".join(t if t.startswith("#") else f"#{t}" for t in hashtags[:30])
            caption = f"{description}\n\n🔗 {blog_url}\n\n{tags}"[:2200]

            r = requests.post(
                f"{GRAPH}/{ig_user_id}/media",
                params={"media_type": "REELS", "video_url": video_url,
                         "caption": caption, "share_to_feed": "true",
                         "access_token": token},
                timeout=30,
            )
            if not r.ok:
                return make_failure_result(self.platform, f"create container: {r.text[:300]}")
            container_id = r.json()["id"]

            # Poll until FINISHED
            deadline = time.time() + 300
            while time.time() < deadline:
                p = requests.get(f"{GRAPH}/{container_id}",
                                  params={"fields": "status_code", "access_token": token},
                                  timeout=30)
                if p.ok and p.json().get("status_code") == "FINISHED":
                    break
                time.sleep(5)

            r2 = requests.post(
                f"{GRAPH}/{ig_user_id}/media_publish",
                params={"creation_id": container_id, "access_token": token},
                timeout=30,
            )
            if not r2.ok:
                return make_failure_result(self.platform, f"publish: {r2.text[:300]}")
            media_id = r2.json()["id"]
            link = requests.get(f"{GRAPH}/{media_id}",
                                params={"fields": "permalink", "access_token": token},
                                timeout=30)
            permalink = link.json().get("permalink") if link.ok else None
            return {"success": True, "platform": self.platform,
                    "post_url": permalink, "post_id": media_id,
                    "scheduled": False, "scheduled_for": None, "error": None}
        except Exception as e:
            log.exception("Instagram upload failed")
            return make_failure_result(self.platform, str(e))
```

- [ ] **Step 3: Commit**

```bash
python -m pytest tests/video_agent/publishers/test_instagram.py -v
git add video_agent/publishers/instagram.py tests/video_agent/publishers/test_instagram.py
git commit -m "feat(video_agent): Instagram Reels publisher (gated by Meta review)"
```

---

### Task 34: GitHub CDN cleanup tool (`tools/cleanup_cdn.py`)

```python
# video_agent/tools/cleanup_cdn.py
"""Delete GitHub release assets older than N days."""
import argparse
import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)


def cleanup(days: int = 7) -> int:
    from github import Github
    from token_manager import TokenManager  # type: ignore
    tm = TokenManager()
    gh = Github(tm.get_github_token())
    repo = gh.get_repo(tm.get_github_cdn_repo())
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    deleted = 0
    for release in repo.get_releases():
        if release.created_at.replace(tzinfo=timezone.utc) < cutoff:
            log.info("Deleting release %s", release.tag_name)
            release.delete_release()
            deleted += 1
    return deleted


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=7)
    args = p.parse_args()
    n = cleanup(args.days)
    print(f"Deleted {n} releases older than {args.days} days.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
```

Smoke test only — no unit test (live GitHub I/O). Commit:

```bash
git add video_agent/tools/cleanup_cdn.py
git commit -m "feat(video_agent): GitHub release cleanup tool"
```

---

### Task 35: `blog_agent_v3.py` `--with-video` hook

Find the existing `main()` in `blog_agent_v3.py`. Add to its argparse:

```python
parser.add_argument("--with-video", action="store_true",
                    help="Also generate + queue a vertical short for this blog.")
```

Then near the end of `main()`, after the blog has been published successfully, append:

```python
if args.with_video:
    try:
        from video_agent.orchestrator import generate_video_for_blog
        generate_video_for_blog(
            blog_record,
            publish_to=("youtube", "linkedin"),  # IG remains gated
            scheduled=True,
        )
    except Exception as e:
        log.warning("Video generation failed (blog still published): %s", e)
```

Also extend the existing Google OAuth `SCOPES` list to include `https://www.googleapis.com/auth/youtube.upload`. **Manual one-time step** — delete `token.pickle` and rerun any blogger command to trigger re-consent.

Commit:
```bash
git add blog_agent_v3.py
git commit -m "feat(blog_agent_v3): --with-video flag + youtube.upload scope"
```

---

### Task 36: Write `VIDEO_SETUP.md`

Single-page setup checklist. Mirror spec §7. Include:

1. System deps (FFmpeg, fonts).
2. Pip install `pip install -r requirements.txt`.
3. Account setup steps (YT, IG, Meta app review, GitHub CDN repo).
4. `secrets.txt` keys.
5. Asset library bootstrap (shoot list, tag tool, music, brand assets).
6. First-run smoke commands.

Commit:
```bash
git add VIDEO_SETUP.md
git commit -m "docs: VIDEO_SETUP.md operator checklist"
```

---

### Task 37: Final acceptance run

- [ ] Run full suite: `python -m pytest tests/video_agent/ -v` — all green.
- [ ] Run smoke: `python scripts/smoke_video.py` — produces playable MP4.
- [ ] Run dry-run: `python -m video_agent.main --latest --dry-run` — succeeds without publishing.
- [ ] Tag: `git tag video-agent-v1.0`.

---

## Notes for the Implementing Agent

- **Failure isolation:** orchestrator should never abort the whole run because one publisher errored — log and continue.
- **Test mocking discipline:** publishers test against `responses`-mocked HTTP, not real APIs. Live calls happen only in Task 37 acceptance and operator smoke tests.
- **Windows path quirks:** the FFmpeg `subtitles` filter needs forward-slash paths and escaped colons (`E\\:/path/to.srt`). The composer already handles this.
- **Quota awareness:** YouTube allots 10,000 units/day; one upload costs ~1,600. Backfill of 20 = 32k units, exceeds daily quota — schedule across multiple days or accept partial.
- **Don't re-run brainstorming.** This plan is the contract. If genuinely blocked (a spec ambiguity or a hard-to-reproduce failure), surface to the user — don't reinterpret.
- **One commit per task.** Atomic, reviewable, easy to revert.

---

**End of Part 2.** Sprints 4–8 complete the production pipeline. After Task 37, the system is shippable per spec §8 acceptance criteria.
