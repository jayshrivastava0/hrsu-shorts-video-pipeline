import pytest

from shorts_engine import config


def test_writer_override_via_env(monkeypatch):
    monkeypatch.setenv("HRSU_MODEL_WRITER", "qwen3:8b")
    assert config.model_for_role("writer") == "qwen3:8b"


def test_planner_override_via_env(monkeypatch):
    monkeypatch.setenv("HRSU_MODEL_PLANNER", "qwen3:8b")
    assert config.model_for_role("planner") == "qwen3:8b"


def test_same_family_writer_and_verifier_is_refused(monkeypatch):
    monkeypatch.setenv("HRSU_MODEL_WRITER", "qwen3:8b")
    monkeypatch.setenv("HRSU_MODEL_VERIFIER", "qwen3:4b")
    with pytest.raises(config.EngineConfigError):
        config.check_role_independence("verifier")


def test_different_family_is_allowed(monkeypatch):
    monkeypatch.setenv("HRSU_MODEL_WRITER", "qwen3:8b")
    monkeypatch.setenv("HRSU_MODEL_PLANNER", "qwen3:8b")
    monkeypatch.setenv("HRSU_MODEL_VERIFIER", "gemma3:4b")
    monkeypatch.setenv("HRSU_MODEL_CRITIC", "gemma3:4b")
    config.check_role_independence("verifier")
    config.check_role_independence("critic")
