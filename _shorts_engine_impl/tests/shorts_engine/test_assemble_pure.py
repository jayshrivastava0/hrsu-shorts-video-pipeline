"""Tests for pure functions in the ASSEMBLE stage."""
from __future__ import annotations
from pathlib import Path
from shorts_engine import config

BEATS_AUDIO = [
    {"beat": "hook", "start_s": 0.0, "duration_s": 3.0},
    {"beat": "stakes", "start_s": 3.3, "duration_s": 5.0},
    {"beat": "mechanism", "start_s": 8.6, "duration_s": 10.0},
    {"beat": "proof", "start_s": 18.9, "duration_s": 8.0},
    {"beat": "cta", "start_s": 27.2, "duration_s": 7.0},
]
VOICE_TOTAL = 34.2

SHOTS = (
    [{"id": "s00", "beat": "hook", "type": "HEADLINE_CARD", "duration_s": 3.0,
      "narration_span": "", "payload": {}, "fallback": None}] +
    [{"id": "s01", "beat": "stakes", "type": "STAT_CARD", "duration_s": 4.6,
      "narration_span": "", "payload": {}, "fallback": None}] +
    [{"id": f"s0{i}", "beat": "mechanism", "type": "DIAGRAM", "duration_s": 3.4,
      "narration_span": "", "payload": {}, "fallback": None} for i in (2, 3, 4)] +
    [{"id": "s05", "beat": "proof", "type": "STAT_CARD", "duration_s": 4.0,
      "narration_span": "", "payload": {}, "fallback": None},
     {"id": "s06", "beat": "proof", "type": "QUOTE_CARD", "duration_s": 4.0,
      "narration_span": "", "payload": {}, "fallback": None},
     {"id": "s07", "beat": "cta", "type": "LOGO_CTA", "duration_s": 7.0,
      "narration_span": "", "payload": {}, "fallback": None}]
)


class TestBeatSpans:
    def test_spans_cover_voice_plus_hold(self):
        from shorts_engine.stages import assemble
        spans = assemble.beat_spans(BEATS_AUDIO, VOICE_TOTAL)
        assert abs(sum(s["span_s"] for s in spans)
                   - (VOICE_TOTAL + config.END_CARD_HOLD_S)) < 1e-6
        assert spans[0]["span_s"] == 3.3
        assert abs(spans[-1]["span_s"] - (34.2 - 27.2 + 1.5)) < 1e-6


class TestReflow:
    def test_beat_sums_match_spans_exactly(self):
        from shorts_engine.stages import assemble
        out = assemble.reflow(SHOTS, BEATS_AUDIO, VOICE_TOTAL)
        spans = {s["beat"]: s["span_s"]
                 for s in assemble.beat_spans(BEATS_AUDIO, VOICE_TOTAL)}
        for beat, span in spans.items():
            got = sum(s["duration_s"] for s in out if s["beat"] == beat)
            assert abs(got - span) < 1e-6, beat

    def test_total_equals_voice_plus_hold(self):
        from shorts_engine.stages import assemble
        out = assemble.reflow(SHOTS, BEATS_AUDIO, VOICE_TOTAL)
        assert abs(sum(s["duration_s"] for s in out)
                   - (VOICE_TOTAL + config.END_CARD_HOLD_S)) < 1e-6

    def test_non_last_shots_respect_bounds(self):
        from shorts_engine.stages import assemble
        out = assemble.reflow(SHOTS, BEATS_AUDIO, VOICE_TOTAL)
        by_beat: dict[str, list] = {}
        for s in out:
            by_beat.setdefault(s["beat"], []).append(s)
        for beat, group in by_beat.items():
            for s in group[:-1]:
                assert config.SHOT_MIN_S - 1e-6 <= s["duration_s"] \
                       <= config.SHOT_MAX_S + 1e-6

    def test_delta_recorded(self):
        from shorts_engine.stages import assemble
        out = assemble.reflow(SHOTS, BEATS_AUDIO, VOICE_TOTAL)
        assert all("reflow_delta_s" in s for s in out)

    def test_large_scale_up_still_sums_exactly_and_clamps_correctly(self):
        """Regression: a live run on real technical B2B content (multi-
        syllable vocabulary) measured actual voice duration at 1.47x the
        script estimate -- AUDIO_DURATION_TOLERANCE was widened to accept
        this and let reflow() absorb the gap, since reflow is the mechanism
        specifically designed to reconcile estimate-vs-actual drift. This
        pins that a ~1.5x scale-up (not just the near-1.0x scale factors the
        other tests use) still sums exactly to the real span, still clamps
        non-last shots to SHOT_MAX_S, and lets the last shot in the beat
        absorb the residual beyond its own normal cap."""
        from shorts_engine.stages import assemble
        beats_audio = [{"beat": "stakes", "start_s": 0.0, "duration_s": 6.4}]
        # single beat, no next beat -> span = voice_total_s - start_s + hold
        voice_total = 6.4
        shots = [
            {"id": "s00", "beat": "stakes", "type": "STAT_CARD",
             "duration_s": 3.2, "narration_span": "", "payload": {},
             "fallback": None},
            {"id": "s01", "beat": "stakes", "type": "HEADLINE_CARD",
             "duration_s": 3.2, "narration_span": "", "payload": {},
             "fallback": None},
        ]
        out = assemble.reflow(shots, beats_audio, voice_total)
        span = voice_total - 0.0 + config.END_CARD_HOLD_S  # 6.4 + 1.5 = 7.9
        assert abs(sum(s["duration_s"] for s in out) - span) < 1e-6
        # scale = 7.9/6.4 ~= 1.234; non-last shot: 3.2*1.234 ~= 3.95, under
        # SHOT_MAX_S so NOT clamped here -- bump the scale further to force
        # a real clamp + residual-absorption case.
        beats_audio2 = [{"beat": "stakes", "start_s": 0.0, "duration_s": 9.6}]
        voice_total2 = 9.6
        out2 = assemble.reflow(shots, beats_audio2, voice_total2)
        span2 = voice_total2 + config.END_CARD_HOLD_S  # 11.1s, scale ~= 1.73x
        assert abs(sum(s["duration_s"] for s in out2) - span2) < 1e-6
        non_last, last = out2[0], out2[1]
        assert non_last["duration_s"] <= config.SHOT_MAX_S + 1e-6
        # last shot absorbs the residual and may exceed the normal cap
        assert last["duration_s"] == span2 - non_last["duration_s"]


class TestCaptions:
    WORDS = [{"word": f"w{i}", "start": i * 0.4, "end": i * 0.4 + 0.35}
             for i in range(10)]

    def test_cues_grouped_and_uppercase(self):
        from shorts_engine.stages import assemble
        cues = assemble.group_words_into_cues(self.WORDS)
        assert all(len(c["text"].split()) <= 3 for c in cues)
        assert all(c["end"] - c["start"] <= 1.5 + 1e-6 for c in cues)
        assert cues[0]["text"] == cues[0]["text"].upper()

    def test_ass_time_format(self):
        from shorts_engine.stages import assemble
        assert assemble.ass_time(0.0) == "0:00:00.00"
        assert assemble.ass_time(65.37) == "0:01:05.37"

    def test_build_ass_margins_and_style(self, tmp_path):
        from shorts_engine.stages import assemble
        p = assemble.build_ass(self.WORDS, tmp_path / "c.ass")
        text = Path(p).read_text(encoding="utf-8")
        assert "PlayResX: 1080" in text and "PlayResY: 1920" in text
        style = next(l for l in text.splitlines() if l.startswith("Style: Cap"))
        fields = style.split(",")
        # MarginV is the 3rd-to-last field (counting from Encoding at -1)
        margin_v = int(fields[-2])
        assert margin_v >= config.SAFE_BOTTOM_PX
        assert "Dialogue:" in text
        assert "&H00F6D6CC" in style.upper()
