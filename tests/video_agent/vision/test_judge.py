"""Tests for vision judge (Task B-2)."""
from __future__ import annotations
from pathlib import Path
import pytest

from video_agent.vision.judge import judge_image, VisionVerdict


def test_judge_image_maps_verdict(monkeypatch, tmp_path):
    """judge_image maps a well-formed dict to VisionVerdict with clamped values."""
    fake_image = tmp_path / "frame.jpg"
    fake_image.write_bytes(b"fake")

    monkeypatch.setattr(
        "video_agent.vision.judge.call_vision_json",
        lambda prompt, image_path, model, timeout_s: {
            "score": 8,
            "reason": "shows industrial plant",
            "focus_x": 0.6,
            "focus_y": 0.4,
            "subject_fills_frame": False,
        },
    )

    v = judge_image(fake_image, narration="Calcium nitrate treats H2S in pipes.")
    assert v is not None
    assert v.score == 8
    assert v.reason == "shows industrial plant"
    assert abs(v.focus_x - 0.6) < 1e-6
    assert abs(v.focus_y - 0.4) < 1e-6
    assert v.subject_fills_frame is False


def test_judge_image_clamps_score_above_10(monkeypatch, tmp_path):
    """score > 10 is clamped to 10."""
    fake_image = tmp_path / "frame.jpg"
    fake_image.write_bytes(b"fake")

    monkeypatch.setattr(
        "video_agent.vision.judge.call_vision_json",
        lambda *a, **kw: {"score": 13, "reason": "excellent", "focus_x": 0.5, "focus_y": 0.5},
    )
    v = judge_image(fake_image, narration="test")
    assert v is not None
    assert v.score == 10


def test_judge_image_clamps_focus_above_1(monkeypatch, tmp_path):
    """focus_x > 1.0 is clamped to 1.0."""
    fake_image = tmp_path / "frame.jpg"
    fake_image.write_bytes(b"fake")

    monkeypatch.setattr(
        "video_agent.vision.judge.call_vision_json",
        lambda *a, **kw: {"score": 7, "reason": "ok", "focus_x": 1.5, "focus_y": -0.2},
    )
    v = judge_image(fake_image, narration="test")
    assert v is not None
    assert v.focus_x == 1.0
    assert v.focus_y == 0.0


def test_judge_image_returns_none_when_call_returns_none(monkeypatch, tmp_path):
    """Returns None when call_vision_json returns None."""
    fake_image = tmp_path / "frame.jpg"
    fake_image.write_bytes(b"fake")

    monkeypatch.setattr(
        "video_agent.vision.judge.call_vision_json",
        lambda *a, **kw: None,
    )
    v = judge_image(fake_image, narration="test")
    assert v is None


def test_judge_image_returns_none_when_score_missing(monkeypatch, tmp_path):
    """Returns None when response dict has no 'score' key."""
    fake_image = tmp_path / "frame.jpg"
    fake_image.write_bytes(b"fake")

    monkeypatch.setattr(
        "video_agent.vision.judge.call_vision_json",
        lambda *a, **kw: {"reason": "interesting but no score"},
    )
    v = judge_image(fake_image, narration="test")
    assert v is None


def test_judge_image_returns_none_for_bad_score_type(monkeypatch, tmp_path):
    """Returns None when score cannot be converted to int."""
    fake_image = tmp_path / "frame.jpg"
    fake_image.write_bytes(b"fake")

    monkeypatch.setattr(
        "video_agent.vision.judge.call_vision_json",
        lambda *a, **kw: {"score": "not-a-number", "reason": "bad"},
    )
    v = judge_image(fake_image, narration="test")
    assert v is None


def test_judge_image_uses_defaults_for_missing_fields(monkeypatch, tmp_path):
    """Missing optional fields in response use safe defaults."""
    fake_image = tmp_path / "frame.jpg"
    fake_image.write_bytes(b"fake")

    monkeypatch.setattr(
        "video_agent.vision.judge.call_vision_json",
        lambda *a, **kw: {"score": 5},
    )
    v = judge_image(fake_image, narration="test")
    assert v is not None
    assert v.score == 5
    assert v.reason == ""
    assert v.focus_x == 0.5
    assert v.focus_y == 0.5
    assert v.subject_fills_frame is False
