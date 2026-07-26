"""Tests for the ASSEMBLE run() function."""
from __future__ import annotations
import json
from pathlib import Path
import pytest
from pydub import AudioSegment

SHOTS = {"shots": [
    {"id": "s00", "beat": "hook", "type": "HEADLINE_CARD", "duration_s": 2.5,
     "narration_span": "", "payload": {"text": "Nitrate limits tighten"},
     "fallback": None},
    {"id": "s01", "beat": "cta", "type": "LOGO_CTA", "duration_s": 3.0,
     "narration_span": "", "payload": {"differentiator": "high-purity",
                                       "cta_line": "guide",
                                       "domain": "hrsuindore.com"},
     "fallback": None},
], "total_s": 5.5}
BEATS_AUDIO = [{"beat": "hook", "start_s": 0.0, "duration_s": 2.4},
               {"beat": "cta", "start_s": 2.7, "duration_s": 2.8}]
WORDS = [{"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4}
         for i, w in enumerate("nitrate limits are tightening act now".split())]


def _ctx(tmp_path):
    from shorts_engine.manifest import RunManifest
    from shorts_engine.runner import StageContext
    m = RunManifest.create("https://blog.hrsuindore.com/x.html", tmp_path)
    ws = Path(m.workspace)
    (ws / "shotlist.json").write_text(json.dumps(SHOTS), encoding="utf-8")
    (ws / "beats_audio.json").write_text(json.dumps(BEATS_AUDIO), encoding="utf-8")
    (ws / "word_timings.json").write_text(json.dumps(WORDS), encoding="utf-8")
    (ws / "post.json").write_text(json.dumps({"region": "eu"}), encoding="utf-8")
    AudioSegment.silent(duration=5500).export(str(ws / "voiceover.mp3"),
                                              format="mp3", bitrate="128k")
    # pre-rendered shots dir as VISUALS would leave it
    from shorts_engine.stages import visuals
    (ws / "shots").mkdir()
    for s in SHOTS["shots"]:
        visuals.RENDERERS[s["type"]](s["payload"], s["duration_s"],
                                     ws / "shots" / f"shot_{s['id']}.mp4")
    return StageContext(manifest=m, workspace=ws, flags={})


class TestAssembleRun:
    def test_duration_law_and_artifacts(self, tmp_path):
        from shorts_engine.stages import assemble
        from shorts_engine.cards import encoder
        from shorts_engine import config
        ctx = _ctx(tmp_path)
        arts = assemble.run(ctx)
        ws = Path(ctx.workspace)
        video = ws / arts["video"]
        assert video.exists()
        voice = encoder.probe_duration(ws / "voiceover.mp3")
        vd = encoder.probe_duration(video)
        assert vd >= voice + config.AUDIO_COMPLETENESS_MARGIN_S
        assert abs(vd - (voice + config.END_CARD_HOLD_S)) <= 0.35
        assert (ws / arts["captions"]).exists()
        report = json.loads((ws / arts["assemble_report"]).read_text(encoding="utf-8"))
        assert report["video_duration_s"] >= report["voice_total_s"] + 1.4
        assert len(report["shots"]) == 2

    def test_video_has_audio_stream(self, tmp_path):
        import subprocess
        from shorts_engine.stages import assemble
        ctx = _ctx(tmp_path)
        arts = assemble.run(ctx)
        ws = Path(ctx.workspace)
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0",
             str(ws / arts["video"])], capture_output=True, text=True)
        assert "audio" in res.stdout

    def test_no_shortest_in_final_mux(self):
        import inspect
        from shorts_engine.stages import assemble
        src = inspect.getsource(assemble.run) + inspect.getsource(assemble._final_mux)
        assert "-shortest" not in src

    def test_dark_ribbon_fix_raises_bottom_strip_luma_above_threshold(self, tmp_path):
        """Regression: video_agent's dark-ribbon heuristic (reused from a
        different pipeline) structurally false-positives on shorts_engine's
        navy-branded cards -- confirmed live (bottom-strip luma ~19-20,
        floor is 24). ctx.flags["dark_ribbon_fix"] must make the REAL
        produced video's bottom strip clear that exact threshold, not just
        assert the filter string looks right."""
        import subprocess
        import numpy as np
        from PIL import Image
        from shorts_engine.stages import assemble
        from shorts_engine.cards import encoder
        from shorts_engine import config as se_config
        from video_agent.config import (VERIFY_DARK_RIBBON_STRIP_PX,
                                        VERIFY_DARK_RIBBON_LUMA_MAX)

        ctx = _ctx(tmp_path)
        ctx.flags["dark_ribbon_fix"] = True
        arts = assemble.run(ctx)
        ws = Path(ctx.workspace)
        video = ws / arts["video"]

        mid_t = encoder.probe_duration(video) / 2
        frame_png = tmp_path / "check.png"
        res = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{mid_t:.3f}",
             "-i", str(video), "-frames:v", "1", str(frame_png)],
            capture_output=True, text=True)
        assert res.returncode == 0 and frame_png.exists()

        img = Image.open(frame_png).convert("L")
        arr = np.asarray(img)
        strip = arr[-VERIFY_DARK_RIBBON_STRIP_PX:, :]
        assert strip.mean() > VERIFY_DARK_RIBBON_LUMA_MAX, (
            f"accent band did not raise bottom-strip luma above the real "
            f"heuristic's floor: got {strip.mean():.1f}")

    def test_rerender_only_beyond_epsilon(self, tmp_path):
        from shorts_engine.stages import assemble
        ctx = _ctx(tmp_path)
        arts = assemble.run(ctx)
        ws = Path(ctx.workspace)
        report = json.loads((ws / arts["assemble_report"]).read_text(encoding="utf-8"))
        for s in report["shots"]:
            assert s["rerendered"] == (abs(s["reflow_delta_s"]) > 0.05)
