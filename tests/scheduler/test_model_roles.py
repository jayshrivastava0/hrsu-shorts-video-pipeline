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


def test_heavy_roles_on_claude_haiku_checkers_on_open_source(monkeypatch):
    from video_agent.claude_client import claude_model_for, role_uses_claude
    for k in ("HRSU_LLM_BACKEND", "HRSU_BACKEND_WRITER", "HRSU_BACKEND_VERIFIER", "HRSU_CLAUDE_MODEL",
              "HRSU_CLAUDE_MODEL_WRITER"):
        monkeypatch.delenv(k, raising=False)
    assert role_uses_claude("planner") and role_uses_claude("writer")
    assert not role_uses_claude("verifier") and not role_uses_claude("critic")
    assert claude_model_for("writer") == "claude-haiku-5-5"


def test_backend_per_role_override_and_global_ollama(monkeypatch):
    from video_agent.claude_client import role_uses_claude
    monkeypatch.delenv("HRSU_LLM_BACKEND", raising=False)
    monkeypatch.setenv("HRSU_BACKEND_WRITER", "ollama")
    assert not role_uses_claude("writer")
    monkeypatch.delenv("HRSU_BACKEND_WRITER")
    monkeypatch.setenv("HRSU_LLM_BACKEND", "ollama")
    assert not role_uses_claude("writer")
