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
