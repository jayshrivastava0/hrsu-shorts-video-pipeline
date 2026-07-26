import json
import pytest
from pathlib import Path
from unittest.mock import patch
from video_agent import history


@pytest.fixture
def tmp_history(tmp_path, monkeypatch):
    p = tmp_path / "video_history.json"
    monkeypatch.setattr(history, "HISTORY_PATH", p)
    return p


def test_load_returns_default_when_missing(tmp_history):
    data = history.load()
    assert data == {"videos": []}


def test_save_atomic_creates_file(tmp_history):
    history.save_atomic({"videos": [{"blog_id": "1"}]})
    assert tmp_history.exists()
    assert json.loads(tmp_history.read_text())["videos"][0]["blog_id"] == "1"


def test_append_video_persists(tmp_history):
    history.append_video({"blog_id": "abc", "video_path": "x.mp4"})
    history.append_video({"blog_id": "def", "video_path": "y.mp4"})
    data = history.load()
    assert len(data["videos"]) == 2
    assert data["videos"][1]["blog_id"] == "def"


def test_find_by_blog_id(tmp_history):
    history.append_video({"blog_id": "abc", "video_path": "x.mp4"})
    assert history.find_by_blog_id("abc")["video_path"] == "x.mp4"
    assert history.find_by_blog_id("missing") is None


def test_save_atomic_no_corruption_on_crash(tmp_history, monkeypatch):
    history.save_atomic({"videos": [{"blog_id": "ok"}]})
    # Simulate crash mid-write: tempfile rename should be atomic.
    original = tmp_history.read_text()
    real_replace = Path.replace
    def boom(self, target):
        raise OSError("disk full")
    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError):
        history.save_atomic({"videos": [{"blog_id": "new"}]})
    # Original file untouched.
    assert tmp_history.read_text() == original


def test_stats_counts_recent(tmp_history):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=45)).isoformat()
    recent = (now - timedelta(days=2)).isoformat()
    history.append_video({"blog_id": "1", "created_at": old})
    history.append_video({"blog_id": "2", "created_at": recent})
    s = history.stats(days=30)
    assert s["count"] == 1
