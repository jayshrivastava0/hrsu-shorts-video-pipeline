"""Tests for the AUDIO stage."""
from __future__ import annotations
import json
from pathlib import Path
import pytest

BEATS = [{"beat": b, "purpose": b, "narration": ("word " * n).strip(), "fact_ids": [],
          "card_text": "c", "broll_wish": ""}
         for b, n in (("hook", 8), ("stakes", 13), ("mechanism", 26),
                      ("proof", 21), ("cta", 18))]  # 86 words ≈ 50.6s est at 1.7 w/s


def _fake_synth_factory(seconds_per_word=1 / 1.7, garbage=()):
    """Writes a real silent mp3 sized to the narration and returns metadata."""
    def fake(segments, output_path, region, voice_override=None):
        from pydub import AudioSegment
        text = segments[0].text
        dur_ms = int(len(text.split()) * seconds_per_word * 1000)
        AudioSegment.silent(duration=max(dur_ms, 120)).export(
            str(output_path), format="mp3", bitrate="128k")
        if Path(output_path).name in garbage:
            Path(output_path).write_bytes(b"")  # simulate F10: 0-byte file
        return {"audio_path": Path(output_path), "duration_s": dur_ms / 1000,
                "voice_used": "test", "engine_used": "fake", "fell_back": False}
    return fake


def _fake_transcribe(audio_path, narration_hint=None, multilingual=False):
    words = (narration_hint or "a b c").split()
    return [{"word": w, "start": i * 0.4, "end": i * 0.4 + 0.35}
            for i, w in enumerate(words)]


def _ctx(tmp_path):
    from shorts_engine.manifest import RunManifest
    from shorts_engine.runner import StageContext
    m = RunManifest.create("https://blog.hrsuindore.com/x.html", tmp_path)
    ws = Path(m.workspace)
    (ws / "script.json").write_text(json.dumps({"beats": BEATS}), encoding="utf-8")
    (ws / "post.json").write_text(json.dumps({"region": "eu"}), encoding="utf-8")
    return StageContext(manifest=m, workspace=ws, flags={})


class TestAudioStage:
    def test_artifacts_written(self, tmp_path, monkeypatch):
        from shorts_engine.stages import audio
        monkeypatch.setattr(audio, "_synthesize", _fake_synth_factory())
        monkeypatch.setattr(audio, "_transcribe", _fake_transcribe)
        ctx = _ctx(tmp_path)
        arts = audio.run(ctx)
        ws = Path(ctx.workspace)
        assert (ws / arts["voice"]).stat().st_size > 1024
        timings = json.loads((ws / arts["word_timings"]).read_text(encoding="utf-8"))
        assert timings and {"word", "start", "end"} <= set(timings[0])
        beats = json.loads((ws / arts["beats_audio"]).read_text(encoding="utf-8"))
        assert [b["beat"] for b in beats] == ["hook", "stakes", "mechanism",
                                              "proof", "cta"]
        assert beats[1]["start_s"] > beats[0]["duration_s"] - 1e-6  # gap included
        for i in range(5):
            assert (ws / f"voice_beat_{i:02d}.mp3").exists()

    def test_zero_byte_segment_fails_loudly(self, tmp_path, monkeypatch):
        from shorts_engine.stages import audio
        from shorts_engine.errors import EngineError
        monkeypatch.setattr(audio, "_synthesize",
                            _fake_synth_factory(garbage=("voice_beat_02.mp3",)))
        monkeypatch.setattr(audio, "_transcribe", _fake_transcribe)
        with pytest.raises(EngineError, match="mechanism"):
            audio.run(_ctx(tmp_path))

    def test_duration_drift_fails_loudly(self, tmp_path, monkeypatch):
        from shorts_engine.stages import audio
        from shorts_engine.errors import EngineError
        monkeypatch.setattr(audio, "_synthesize",
                            _fake_synth_factory(seconds_per_word=1.2))  # ~2x too slow, past ±65%
        monkeypatch.setattr(audio, "_transcribe", _fake_transcribe)
        with pytest.raises(EngineError, match="duration"):
            audio.run(_ctx(tmp_path))

    def test_prosody_mapping_used(self, tmp_path, monkeypatch):
        from shorts_engine.stages import audio
        seen = []
        base = _fake_synth_factory()
        def spy(segments, output_path, region, voice_override=None):
            seen.append(segments[0].prosody)
            return base(segments, output_path, region, voice_override)
        monkeypatch.setattr(audio, "_synthesize", spy)
        monkeypatch.setattr(audio, "_transcribe", _fake_transcribe)
        audio.run(_ctx(tmp_path))
        assert seen == ["hook_emphasis", "urgent_problem", "conversational",
                        "matter_of_fact", "warm_cta"]

    def test_prosody_keyed_by_purpose_not_free_form_beat_name(self, tmp_path, monkeypatch):
        # Regression: since the 2026-08-26 creative-flow redesign, `beat` is a free-form
        # LLM-chosen string with no fixed vocabulary (e.g. "the cold snap"), while `purpose`
        # keeps the fixed hook/stakes/mechanism/proof/cta enum PROSODY_BY_BEAT is keyed on.
        # Keying off `beat` (the old behavior) meant PROSODY_BY_BEAT almost never matched and
        # every beat silently fell through to the "conversational" default.
        from shorts_engine.stages import audio
        free_form_beats = [
            {"beat": "the cold snap", "purpose": "hook",
             "narration": ("word " * 8).strip(), "fact_ids": [], "card_text": "c", "broll_wish": ""},
            {"beat": "why it matters", "purpose": "stakes",
             "narration": ("word " * 13).strip(), "fact_ids": [], "card_text": "c", "broll_wish": ""},
            {"beat": "how it works", "purpose": "mechanism",
             "narration": ("word " * 26).strip(), "fact_ids": [], "card_text": "c", "broll_wish": ""},
            {"beat": "the field result", "purpose": "proof",
             "narration": ("word " * 21).strip(), "fact_ids": [], "card_text": "c", "broll_wish": ""},
            {"beat": "reach out", "purpose": "cta",
             "narration": ("word " * 18).strip(), "fact_ids": [], "card_text": "c", "broll_wish": ""},
        ]
        seen = []
        base = _fake_synth_factory()

        def spy(segments, output_path, region, voice_override=None):
            seen.append(segments[0].prosody)
            return base(segments, output_path, region, voice_override)
        monkeypatch.setattr(audio, "_synthesize", spy)
        monkeypatch.setattr(audio, "_transcribe", _fake_transcribe)

        from shorts_engine.manifest import RunManifest
        from shorts_engine.runner import StageContext
        m = RunManifest.create("https://blog.hrsuindore.com/x.html", tmp_path)
        ws = Path(m.workspace)
        (ws / "script.json").write_text(json.dumps({"beats": free_form_beats}), encoding="utf-8")
        (ws / "post.json").write_text(json.dumps({"region": "eu"}), encoding="utf-8")
        audio.run(StageContext(manifest=m, workspace=ws, flags={}))

        assert seen == ["hook_emphasis", "urgent_problem", "conversational",
                        "matter_of_fact", "warm_cta"]

    def test_prosody_falls_back_to_conversational_for_missing_purpose(self, tmp_path, monkeypatch):
        from shorts_engine.stages import audio
        beats = [{"beat": "some beat", "purpose": "other",
                  "narration": ("word " * 8).strip(), "fact_ids": [], "card_text": "c",
                  "broll_wish": ""}]
        seen = []
        base = _fake_synth_factory()

        def spy(segments, output_path, region, voice_override=None):
            seen.append(segments[0].prosody)
            return base(segments, output_path, region, voice_override)
        monkeypatch.setattr(audio, "_synthesize", spy)
        monkeypatch.setattr(audio, "_transcribe", _fake_transcribe)

        from shorts_engine.manifest import RunManifest
        from shorts_engine.runner import StageContext
        m = RunManifest.create("https://blog.hrsuindore.com/x.html", tmp_path)
        ws = Path(m.workspace)
        (ws / "script.json").write_text(json.dumps({"beats": beats}), encoding="utf-8")
        (ws / "post.json").write_text(json.dumps({"region": "eu"}), encoding="utf-8")
        audio.run(StageContext(manifest=m, workspace=ws, flags={}))

        assert seen == ["conversational"]


class TestTranscribeWordsExtension:
    def test_transcribe_words_exists_with_signature(self):
        import inspect
        from video_agent import subtitles
        sig = inspect.signature(subtitles.transcribe_words)
        assert list(sig.parameters) == ["audio_path", "narration_hint", "multilingual"]
