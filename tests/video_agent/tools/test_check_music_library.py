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
