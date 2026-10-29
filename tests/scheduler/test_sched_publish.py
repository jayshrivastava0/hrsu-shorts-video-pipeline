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


MAIN = "https://hrsuindore.com"
GENERIC_CTA = "Sourcing calcium nitrate? Visit hrsuindore.com"


def _write_refs(run, post_kind):
    (run / "references.json").write_text(json.dumps({"post_kind": post_kind}))


def test_package_without_blog_url_has_no_full_article_line(run_dir):
    pkg = publish.build_package(run_dir, None, "public", cta_line="Ask us at hrsuindore.com")
    assert "Full article" not in pkg.description
    assert pkg.description.endswith(f"\n\nAsk us at hrsuindore.com\nHRSU Indore: {MAIN}/\n")


def test_package_with_blog_url_unchanged(run_dir):
    pkg = publish.build_package(run_dir, "https://hrsuindore.com/blog/x/", "public")
    assert pkg.description == (
        "Sources:\n[1] Publisher A"
        f"\n\nFull article: https://hrsuindore.com/blog/x/\nHRSU Indore: {MAIN}/\n")
    assert pkg.tags == ["calcium nitrate", "HRSU", "chemical procurement", "explainer",
                        "water treatment", "eu"]


def test_package_uses_cta_line(run_dir):
    pkg = publish.build_package(run_dir, None, "public", cta_line="CTA X")
    assert pkg.description.endswith(f"\n\nCTA X\nHRSU Indore: {MAIN}/\n")


def test_no_blog_url_defaults_cta_and_tag_from_recipe(run_dir):
    from scheduler.briefs.types import get_recipe
    _write_refs(run_dir, "marketing")
    pkg = publish.build_package(run_dir, None, "public")
    assert f"\n\n{get_recipe('marketing').cta_line}\nHRSU Indore: {MAIN}/\n" in pkg.description
    assert "marketing" in pkg.tags


def test_unknown_post_kind_falls_back_to_generic_cta(run_dir):
    _write_refs(run_dir, "not_a_type")
    pkg = publish.build_package(run_dir, None, "public")
    assert f"\n\n{GENERIC_CTA}\nHRSU Indore: {MAIN}/\n" in pkg.description
    assert "not_a_type" not in pkg.tags


def test_missing_references_json_falls_back_to_generic_cta(run_dir):
    assert not (run_dir / "references.json").exists()
    pkg = publish.build_package(run_dir, None, "public")
    assert f"\n\n{GENERIC_CTA}\nHRSU Indore: {MAIN}/\n" in pkg.description


def test_blog_driven_run_ignores_recipe_lookup(run_dir, monkeypatch):
    import scheduler.briefs.types as types_mod

    def boom(name):
        raise AssertionError("recipe lookup must not run for blog-driven runs")
    monkeypatch.setattr(types_mod, "get_recipe", boom)
    _write_refs(run_dir, "technical")
    pkg = publish.build_package(run_dir, "https://hrsuindore.com/blog/x/", "public")
    assert pkg.description.endswith(
        f"\n\nFull article: https://hrsuindore.com/blog/x/\nHRSU Indore: {MAIN}/\n")
    assert pkg.tags == ["calcium nitrate", "HRSU", "chemical procurement", "explainer",
                        "water treatment", "eu"]


def test_publish_video_passes_cta_line_through(run_dir, tmp_path, monkeypatch):
    import scheduler.thumbnail as thumb_mod
    monkeypatch.delenv(publish.PRIVACY_ENV, raising=False)
    monkeypatch.setattr(thumb_mod, "make_thumbnail", lambda video, out: None)
    cap = {}
    publish.publish_video(run_dir, tmp_path / "v.mp4", None, cta_line="CTA Y",
                          base=_auth_dir(tmp_path), publisher=make_publisher(cap),
                          service_factory=lambda: FakeService("public"))
    assert cap["package"].description.endswith(f"\n\nCTA Y\nHRSU Indore: {MAIN}/\n")
