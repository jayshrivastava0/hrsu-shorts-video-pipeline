from __future__ import annotations

import pytest

from shorts_engine import config
from shorts_engine.errors import EngineConfigError
from shorts_engine.llm import text_llm


def test_every_role_has_a_model():
    for role in ("planner", "writer", "verifier", "critic"):
        assert config.model_for_role(role)


def test_unknown_role_raises():
    with pytest.raises(EngineConfigError):
        config.model_for_role("nope")


def test_env_override(monkeypatch):
    monkeypatch.setenv("HRSU_MODEL_VERIFIER", "nemotron-3-ultra:cloud")
    assert config.model_for_role("verifier") == "nemotron-3-ultra:cloud"


def test_model_family():
    assert config.model_family("gemma4:31b-cloud") == "gemma"
    assert config.model_family("glm-5.2:cloud") == "glm"
    assert config.model_family("nemotron-3-ultra:cloud") == "nemotron"
    assert config.model_family("org/gemma-x:7b") == "gemma"
    assert config.model_family("hf.co/org/Gemma-3:4b") == "gemma"


def _clear_role_env(monkeypatch):
    for role in ("PLANNER", "WRITER", "VERIFIER", "CRITIC"):
        monkeypatch.delenv(f"HRSU_MODEL_{role}", raising=False)


def test_default_verifier_and_critic_models(monkeypatch):
    _clear_role_env(monkeypatch)
    assert config.model_for_role("verifier") == "nemotron-3-ultra:cloud"
    assert config.model_for_role("critic") == "nemotron-3-ultra:cloud"


def test_default_verifier_and_critic_differ_from_writer_family(monkeypatch):
    _clear_role_env(monkeypatch)
    assert config.model_family(config.model_for_role("verifier")) != config.model_family(
        config.model_for_role("writer"))
    config.check_role_independence("verifier")
    config.check_role_independence("critic")


def test_independence_check_rejects_same_family(monkeypatch):
    _clear_role_env(monkeypatch)
    monkeypatch.setenv("HRSU_MODEL_VERIFIER", "gemma3:4b")
    with pytest.raises(EngineConfigError, match="same model family"):
        config.check_role_independence("verifier")


def test_independence_check_rejects_planner_family(monkeypatch):
    # the verifier grades the PLANNER's claims, so it must differ from the planner too
    _clear_role_env(monkeypatch)
    monkeypatch.setenv("HRSU_MODEL_PLANNER", "nemotron-3-ultra:cloud")
    with pytest.raises(EngineConfigError, match="same model family"):
        config.check_role_independence("verifier")
    with pytest.raises(EngineConfigError, match="same model family"):
        config.check_role_independence("critic")


class _FakeOllamaClient:
    made: list = []

    def __init__(self, model=None, **kw):
        self.model = model
        self.sdk_calls = []
        _FakeOllamaClient.made.append(self)

    def generate(self, prompt, system=None, **kw):
        return "default-generate"

    def _generate_via_sdk(self, prompt, system=None):
        self.sdk_calls.append((prompt, system))
        return "sdk-generate"


def _install_fake_client(monkeypatch):
    import video_agent.ollama_client as oc
    _FakeOllamaClient.made = []
    monkeypatch.setattr(oc, "OllamaClient", _FakeOllamaClient)


def test_role_client_routes_cloud_models_through_sdk(monkeypatch):
    _clear_role_env(monkeypatch)
    _install_fake_client(monkeypatch)
    monkeypatch.setenv("HRSU_MODEL_VERIFIER", "nemotron-3-ultra:cloud")
    client = text_llm._get_role_client("verifier")
    assert client.model == "nemotron-3-ultra:cloud"
    assert client.generate("p", system="s") == "sdk-generate"
    assert client.sdk_calls == [("p", "s")]


def test_role_client_keeps_default_generate_for_local_models(monkeypatch):
    _clear_role_env(monkeypatch)
    _install_fake_client(monkeypatch)
    monkeypatch.setenv("HRSU_MODEL_WRITER", "gemma3:4b")
    client = text_llm._get_role_client("writer")
    assert client.model == "gemma3:4b"
    assert client.generate("p", system="s") == "default-generate"
    assert client.sdk_calls == []


def test_role_client_enforces_independence_before_constructing(monkeypatch):
    _clear_role_env(monkeypatch)
    _install_fake_client(monkeypatch)
    monkeypatch.setenv("HRSU_MODEL_VERIFIER", "gemma3:4b")
    with pytest.raises(EngineConfigError, match="same model family"):
        text_llm._get_role_client("verifier")
    assert _FakeOllamaClient.made == []


def test_generate_schema_json_uses_role_client(monkeypatch):
    seen = {}

    class FakeClient:
        def generate_json(self, prompt, system=None, retries=1):
            return {"ok": True}

    def fake_role_client(role):
        seen["role"] = role
        return FakeClient()

    monkeypatch.setattr(text_llm, "_get_role_client", fake_role_client)
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}},
              "required": ["ok"]}
    out = text_llm.generate_schema_json("p", "s", schema, role="verifier")
    assert out == {"ok": True}
    assert seen["role"] == "verifier"
