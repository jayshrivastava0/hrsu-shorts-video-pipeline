from __future__ import annotations
from pathlib import Path
import pytest

GOOD_DESC = {
    "description": ("A wide industrial photograph showing rows of circular "
                    "clarifier tanks at a municipal wastewater treatment "
                    "plant, with walkways, railings and aeration equipment "
                    "visible under an overcast sky."),
    "visible_text": "",
    "quality_notes": "sharp, well lit",
}


class TestVerifyDescription:
    def test_good_description_passes(self):
        from shorts_engine.llm import vision_judge as vj
        assert vj.verify_description(GOOD_DESC, "Describe exactly") is None

    def test_none_and_short_and_refusal_rejected(self):
        from shorts_engine.llm import vision_judge as vj
        assert vj.verify_description(None, "p") == "describe_failed"
        short = dict(GOOD_DESC, description="a tank")
        assert vj.verify_description(short, "p") == "description_too_short"
        refusal = dict(GOOD_DESC, description="I am unable to see the image " + "x" * 120)
        assert vj.verify_description(refusal, "p") == "refusal_phrase"

    def test_prompt_echo_rejected(self):
        from shorts_engine.llm import vision_judge as vj
        prompt = "Describe exactly what this image shows: subjects, setting, any visible text"
        echo = dict(GOOD_DESC, description=prompt + " " + "y" * 80)
        assert vj.verify_description(echo, prompt) == "prompt_echo"

    def test_watermark_term_in_visible_text_rejected(self):
        from shorts_engine.llm import vision_judge as vj
        wm = dict(GOOD_DESC, visible_text="shutterstock 12345")
        assert vj.verify_description(wm, "p") == "watermark_text"


class TestJudge:
    def test_describe_failure_can_never_pass(self, tmp_path, monkeypatch):
        from shorts_engine.llm import vision_judge as vj
        img = tmp_path / "i.png"; img.write_bytes(b"x")
        monkeypatch.setattr(vj, "_describe_call", lambda *a, **k: None)
        out = vj.judge(img, "clarifier tanks", "narration")
        assert out["accepted_score"] == 0
        assert out["reject_reason"] == "describe_failed"

    def test_full_protocol_happy_path(self, tmp_path, monkeypatch):
        from shorts_engine.llm import vision_judge as vj
        img = tmp_path / "i.png"; img.write_bytes(b"x")
        seen = {}
        monkeypatch.setattr(vj, "_describe_call", lambda *a, **k: GOOD_DESC)
        def fake_match(prompt, system, schema, **kw):
            seen["prompt"] = prompt
            return {"score": 8, "reason": "matches", "focal_hint": "center"}
        monkeypatch.setattr(vj, "_match_call", fake_match)
        out = vj.judge(img, "clarifier tanks at plant", "dosing narration")
        assert out["accepted_score"] == 8
        assert out["focal_hint"] == "center"
        assert out["reject_reason"] is None
        # the MATCH call sees the DESCRIPTION, never the raw image
        assert "clarifier tanks" in seen["prompt"]
        assert GOOD_DESC["description"][:40] in seen["prompt"]

    def test_describe_retries_then_succeeds(self, tmp_path, monkeypatch):
        from shorts_engine.llm import vision_judge as vj
        img = tmp_path / "i.png"; img.write_bytes(b"x")
        attempts = []
        def flaky(*a, **k):
            attempts.append(1)
            return None if len(attempts) < 3 else GOOD_DESC
        monkeypatch.setattr(vj, "_describe_call", flaky)
        monkeypatch.setattr(vj, "_sleep", lambda s: None)  # no real backoff in tests
        monkeypatch.setattr(vj, "_match_call",
                            lambda *a, **k: {"score": 6, "reason": "r", "focal_hint": "top"})
        out = vj.judge(img, "w", "n")
        assert len(attempts) == 3 and out["accepted_score"] == 6
