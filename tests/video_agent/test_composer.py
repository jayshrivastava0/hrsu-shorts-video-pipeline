import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from video_agent.composer import (
    _redistribute_durations, _pick_music,
    _srt_to_ass, _srt_timestamp_to_ass,
    ComposerError, compose_short, compose_short_v2,
)
from video_agent.storyboard import (
    Storyboard, Scene, VisualConcept, Motion,
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



def test_srt_timestamp_to_ass_conversion():
    assert _srt_timestamp_to_ass("00:00:01,460") == "0:00:01.46"
    assert _srt_timestamp_to_ass("01:23:45,670") == "1:23:45.67"
    assert _srt_timestamp_to_ass("00:00:00,000") == "0:00:00.00"


def test_srt_to_ass_emits_valid_header(tmp_path):
    srt = tmp_path / "s.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,460\nHello world\n\n", encoding="utf-8")
    ass = tmp_path / "s.ass"
    _srt_to_ass(srt, ass, 1080, 1920)
    content = ass.read_text(encoding="utf-8")
    assert "PlayResX: 1080" in content
    assert "PlayResY: 1920" in content
    assert "WrapStyle: 2" in content
    assert "Style: Default,Poppins,62" in content


def test_srt_to_ass_converts_cues(tmp_path):
    srt = tmp_path / "s.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,460\nHello world\n\n"
        "2\n00:00:01,500 --> 00:00:03,000\nSecond line\n\n",
        encoding="utf-8",
    )
    ass = tmp_path / "s.ass"
    _srt_to_ass(srt, ass, 1080, 1920)
    content = ass.read_text(encoding="utf-8")
    assert "Dialogue:" in content
    assert "Hello world" in content
    assert "Second line" in content
    assert "0:00:00.00" in content
    assert "0:00:01.46" in content


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


# ─── Helpers for compose_short_v2 CTA tests ────────────────────────────────

def _make_storyboard(n_scenes: int = 4, duration_each: float = 8.0) -> Storyboard:
    """Create a minimal Storyboard with n_scenes scenes."""
    scenes = [
        Scene(
            index=i,
            beat="hook",
            narration=f"Scene {i} narration text.",
            on_screen_text=f"Scene {i}",
            visual_concept=VisualConcept(
                subject="water", modifier="blue", type="photo", mood="problem"
            ),
            duration_target_s=duration_each,
            motion=Motion(),
        )
        for i in range(n_scenes)
    ]
    return Storyboard(
        version="2",
        blog={"region": "usa", "title": "Test Blog"},
        scenes=scenes,
    )


def _mock_compose_short_v2_deps(monkeypatch, voice_dur: float, muxed_dur: float):
    """Patch all heavy I/O in compose_short_v2 so tests run without ffmpeg."""
    probe_values = [voice_dur, muxed_dur, muxed_dur]  # step 0, guard, step 8
    call_idx = [0]

    def fake_probe(path: Path) -> float:
        val = probe_values[min(call_idx[0], len(probe_values) - 1)]
        call_idx[0] += 1
        return val

    monkeypatch.setattr("video_agent.composer._probe_audio_duration", fake_probe)
    monkeypatch.setattr(
        "video_agent.composer._render_scene_clip",
        lambda s, ws, fps: ws / f"scene_{s.index}.mp4",
    )
    monkeypatch.setattr(
        "video_agent.composer._concat_with_transitions",
        lambda clips, scenes, ws: ws / "concat.mp4",
    )
    monkeypatch.setattr(
        "video_agent.composer.mix_music_under_voice",
        lambda vp, op, region: op,
    )
    monkeypatch.setattr(
        "video_agent.composer._srt_to_ass",
        lambda srt, ass, w, h: ass.write_text("[Script Info]\n", encoding="utf-8"),
    )
    monkeypatch.setattr("video_agent.composer.subprocess.run", lambda *a, **kw: None)
    monkeypatch.setattr(
        "video_agent.composer._validate_safe_zone",
        lambda p: [],
    )
    monkeypatch.setattr(
        "video_agent.composer._write_quality_report",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr("video_agent.composer.shutil.copy2", lambda src, dst: None)


class TestComposShortV2CtaDuration:
    """A-3: CTA card duration and truncation guard."""

    def test_total_duration_equals_voice_plus_cta_tail(self, tmp_path, monkeypatch):
        """After step 0, sum(scene durations) == voice_duration + 1.2 (±0.01)."""
        voice_dur = 38.0
        sb = _make_storyboard(n_scenes=4, duration_each=8.0)
        _mock_compose_short_v2_deps(monkeypatch, voice_dur=voice_dur, muxed_dur=voice_dur + 1.5)

        voice_path = tmp_path / "voice.mp3"
        voice_path.write_bytes(b"ID3")
        srt_path = tmp_path / "s.srt"
        srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nHELLO\n\n", encoding="utf-8")
        out = tmp_path / "out.mp4"
        (tmp_path / "_with_subs.mp4").write_bytes(b"fake")
        out.write_bytes(b"fake mp4")

        compose_short_v2(sb, voice_path, srt_path, out, tmp_path)

        total = sum(s.duration_target_s for s in sb.scenes)
        assert total == pytest.approx(voice_dur + 1.2, abs=0.01), (
            f"Expected {voice_dur + 1.2:.2f}s total, got {total:.2f}s"
        )

    def test_last_scene_gets_cta_tail_added(self, tmp_path, monkeypatch):
        """The last scene's duration must be its proportional share + 1.2s."""
        voice_dur = 40.0
        n = 4
        duration_each = 10.0
        sb = _make_storyboard(n_scenes=n, duration_each=duration_each)
        _mock_compose_short_v2_deps(monkeypatch, voice_dur=voice_dur, muxed_dur=voice_dur + 1.5)

        voice_path = tmp_path / "voice.mp3"
        voice_path.write_bytes(b"ID3")
        srt_path = tmp_path / "s.srt"
        srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nHELLO\n\n", encoding="utf-8")
        out = tmp_path / "out.mp4"
        (tmp_path / "_with_subs.mp4").write_bytes(b"fake")
        out.write_bytes(b"fake mp4")

        compose_short_v2(sb, voice_path, srt_path, out, tmp_path)

        # Proportional share: voice_dur / n = 10.0 each; last gets +1.2
        last_dur = sb.scenes[-1].duration_target_s
        assert last_dur == pytest.approx(voice_dur / n + 1.2, abs=0.01), (
            f"Last scene duration {last_dur:.2f}s should be {voice_dur/n + 1.2:.2f}s"
        )

    def test_no_scene_duration_below_zero(self, tmp_path, monkeypatch):
        """No scene should have duration <= 0 after redistribution."""
        voice_dur = 30.0
        sb = _make_storyboard(n_scenes=5, duration_each=6.0)
        _mock_compose_short_v2_deps(monkeypatch, voice_dur=voice_dur, muxed_dur=voice_dur + 1.5)

        voice_path = tmp_path / "voice.mp3"
        voice_path.write_bytes(b"ID3")
        srt_path = tmp_path / "s.srt"
        srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nHELLO\n\n", encoding="utf-8")
        out = tmp_path / "out.mp4"
        (tmp_path / "_with_subs.mp4").write_bytes(b"fake")
        out.write_bytes(b"fake mp4")

        compose_short_v2(sb, voice_path, srt_path, out, tmp_path)

        for s in sb.scenes:
            assert s.duration_target_s > 0, (
                f"Scene {s.index} has non-positive duration {s.duration_target_s}"
            )

    def test_truncation_guard_raises_when_muxed_shorter_than_voice(
        self, tmp_path, monkeypatch
    ):
        """Guard raises RuntimeError when muxed video is shorter than voice track."""
        voice_dur = 40.0
        muxed_dur = 35.0  # 5 seconds short — truncation detected
        sb = _make_storyboard(n_scenes=4, duration_each=10.0)
        _mock_compose_short_v2_deps(monkeypatch, voice_dur=voice_dur, muxed_dur=muxed_dur)

        voice_path = tmp_path / "voice.mp3"
        voice_path.write_bytes(b"ID3")
        srt_path = tmp_path / "s.srt"
        srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nHELLO\n\n", encoding="utf-8")
        out = tmp_path / "out.mp4"
        (tmp_path / "_with_subs.mp4").write_bytes(b"fake")
        out.write_bytes(b"fake mp4")

        with pytest.raises(RuntimeError, match="CTA-truncation guard"):
            compose_short_v2(sb, voice_path, srt_path, out, tmp_path)
