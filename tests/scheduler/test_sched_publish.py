import json
import logging
from types import SimpleNamespace

import pytest

from scheduler import publish


@pytest.fixture
def run_dir(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "explanation_plan.json").write_text(json.dumps({"question": "How does calcium nitrate cut H2S?"}))
    (run / "post.json").write_text(json.dumps({"title": "T", "category": "water_treatment", "region": "eu"}))
    (run / "video_description.txt").write_text("Sources:\n[1] Publisher A")
    return run


class FakeService:
    def __init__(self, status):
        self.status = status

    def videos(self):
        return self

    def list(self, part, id):
        self.id = id
        return self

    def execute(self):
        return {"items": [{"status": {"privacyStatus": self.status}}]}


def make_publisher(captured):
    def pub(package, video_path, workspace, dry_run=False):
        captured["package"] = package
        return SimpleNamespace(video_id="VID1", url="https://youtube.com/watch?v=VID1")
    return pub


def test_privacy_default_public_and_env_override(monkeypatch):
    monkeypatch.delenv(publish.PRIVACY_ENV, raising=False)
    assert publish.get_privacy() == "public"
    monkeypatch.setenv(publish.PRIVACY_ENV, "Unlisted")
    assert publish.get_privacy() == "unlisted"
    monkeypatch.setenv(publish.PRIVACY_ENV, "bogus")
    with pytest.raises(ValueError):
        publish.get_privacy()


def test_video_agent_default_unchanged():
    from video_agent.config import YOUTUBE_DEFAULT_PRIVACY
    assert YOUTUBE_DEFAULT_PRIVACY == "unlisted"


def test_package_description_and_title(run_dir):
    pkg = publish.build_package(run_dir, "https://hrsuindore.com/blog/x/", "public")
    assert pkg.privacy_status == "public"
    assert pkg.title == "How does calcium nitrate cut H2S?"
    assert pkg.description.startswith("Sources:")
    assert "https://hrsuindore.com/blog/x/" in pkg.description and "hrsuindore.com/" in pkg.description


def test_missing_token_fails_fast(tmp_path):
    (tmp_path / "client_secrets.json").write_text("{}")
    with pytest.raises(publish.YouTubeAuthMissing, match="scheduler.youtube_auth"):
        publish.preflight_auth(tmp_path)


def test_missing_secrets_fails_fast(tmp_path):
    with pytest.raises(publish.YouTubeAuthMissing, match="client_secrets"):
        publish.preflight_auth(tmp_path)


def _auth_dir(tmp_path):
    d = tmp_path / "auth"
    d.mkdir()
    (d / "client_secrets.json").write_text("{}")
    (d / "youtube_token.json").write_text("{}")
    return d


def test_publish_public_ok(run_dir, tmp_path, monkeypatch, caplog):
    monkeypatch.delenv(publish.PRIVACY_ENV, raising=False)
    cap = {}
    with caplog.at_level(logging.WARNING):
        out = publish.publish_video(run_dir, tmp_path / "v.mp4", "https://hrsuindore.com/blog/x/",
                                    base=_auth_dir(tmp_path), publisher=make_publisher(cap),
                                    service_factory=lambda: FakeService("public"))
    assert cap["package"].privacy_status == "public"
    assert out["privacy_actual"] == "public" and out["video_id"] == "VID1"
    assert "PRIVACY MISMATCH" not in caplog.text


def test_forced_private_warns_and_is_recorded(run_dir, tmp_path, monkeypatch, caplog):
    monkeypatch.delenv(publish.PRIVACY_ENV, raising=False)
    with caplog.at_level(logging.WARNING):
        out = publish.publish_video(run_dir, tmp_path / "v.mp4", "u", base=_auth_dir(tmp_path),
                                    publisher=make_publisher({}),
                                    service_factory=lambda: FakeService("private"))
    assert out["privacy_actual"] == "private"
    assert "PRIVACY MISMATCH" in caplog.text and "audit" in caplog.text


def test_dry_run_needs_no_auth(run_dir, tmp_path):
    out = publish.publish_video(run_dir, tmp_path / "v.mp4", "u", dry_run=True, base=tmp_path / "nothing")
    assert out["video_id"] is None
