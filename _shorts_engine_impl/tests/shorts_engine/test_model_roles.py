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


def test_default_verifier_and_critic_differ_from_writer_family():
    assert config.model_family(config.model_for_role("verifier")) != config.model_family(
        config.model_for_role("writer"))
    config.check_role_independence("verifier")
    config.check_role_independence("critic")


def test_independence_check_rejects_same_family(monkeypatch):
    monkeypatch.setenv("HRSU_MODEL_VERIFIER", "gemma3:4b")
    with pytest.raises(EngineConfigError, match="same model family"):
        config.check_role_independence("verifier")


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
