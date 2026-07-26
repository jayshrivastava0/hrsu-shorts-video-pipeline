"""Spec §6.3: SDK-first vision transport + the ANSI parser fix (no char
duplication). These test video_agent/vision/ollama_vision.py directly — the
root suite must also stay green (run it in Step 5)."""
from __future__ import annotations
from pathlib import Path
from unittest.mock import patch

# Side effect: puts PROJECT_ROOT (the blog root) on sys.path so video_agent
# is importable when this file runs standalone (same mechanism the rest of
# the workspace suite relies on — see test_config_phase2.py).
import shorts_engine.config  # noqa: F401


class TestParserFix:
    def test_no_character_duplication_on_wrapped_cli_output(self):
        from video_agent.vision.ollama_vision import _parse_json_from_cli
        # Captured-style CLI noise: cursor-move ANSI in the middle of a word.
        raw = 'noise \x1b[25l\x1b[2K{"description": "bra\x1b[1Gnded factory floor", "visible_text": ""}\x1b[25h'
        out = _parse_json_from_cli(raw)
        assert out is not None
        assert out["description"] == "branded factory floor"
        assert "brabra" not in out["description"]

    def test_plain_json_still_parses(self):
        from video_agent.vision.ollama_vision import _parse_json_from_cli
        assert _parse_json_from_cli('{"a": 1}') == {"a": 1}

    def test_json_after_thinking_marker(self):
        from video_agent.vision.ollama_vision import _parse_json_from_cli
        raw = 'blah blah ...done thinking. {"score": 7}'
        assert _parse_json_from_cli(raw) == {"score": 7}


class TestSdkTransport:
    def test_sdk_call_parses_reply(self, tmp_path):
        import video_agent.vision.ollama_vision as ov
        img = tmp_path / "x.png"
        img.write_bytes(b"fake")

        class FakeMsg:
            content = '```json\n{"description": "a factory"}\n```'
        class FakeResp:
            message = FakeMsg()

        with patch.object(ov, "_sdk_chat", return_value=FakeResp()):
            out = ov.call_vision_json_sdk("describe", img, "m", 30)
        assert out == {"description": "a factory"}

    def test_sdk_failure_returns_none(self, tmp_path):
        import video_agent.vision.ollama_vision as ov
        img = tmp_path / "x.png"
        img.write_bytes(b"fake")
        with patch.object(ov, "_sdk_chat", side_effect=RuntimeError("boom")):
            assert ov.call_vision_json_sdk("d", img, "m", 30) is None


class TestAutoTransport:
    def test_auto_prefers_sdk_then_remembers(self, tmp_path):
        import video_agent.vision.ollama_vision as ov
        img = tmp_path / "x.png"
        img.write_bytes(b"fake")
        ov._TRANSPORT = None  # reset
        calls = []
        with patch.object(ov, "call_vision_json_sdk",
                          side_effect=lambda *a, **k: (calls.append("sdk"), {"ok": 1})[1]), \
             patch.object(ov, "call_vision_json",
                          side_effect=lambda *a, **k: (calls.append("cli"), {"ok": 2})[1]):
            assert ov.call_vision_auto("p", img, "m", 30) == {"ok": 1}
            assert ov.call_vision_auto("p", img, "m", 30) == {"ok": 1}
        assert calls == ["sdk", "sdk"] and ov._TRANSPORT == "sdk"
        ov._TRANSPORT = None

    def test_auto_falls_back_to_cli_and_remembers(self, tmp_path):
        import video_agent.vision.ollama_vision as ov
        img = tmp_path / "x.png"
        img.write_bytes(b"fake")
        ov._TRANSPORT = None
        with patch.object(ov, "call_vision_json_sdk", return_value=None), \
             patch.object(ov, "call_vision_json", return_value={"ok": 2}):
            assert ov.call_vision_auto("p", img, "m", 30) == {"ok": 2}
        assert ov._TRANSPORT == "cli"
        ov._TRANSPORT = None
