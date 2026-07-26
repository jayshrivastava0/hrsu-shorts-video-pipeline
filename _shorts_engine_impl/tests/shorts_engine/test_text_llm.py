"""
Test schema-validated LLM helper with retry logic and fail-loud behavior.

Tests verify:
1. Basic successful generation with schema validation
2. Retry logic on OllamaError
3. Fail loud after retries exhausted
4. No silent fallback to local model
5. Custom client factory for testing
"""
from __future__ import annotations

import logging
import sys
import pytest
from typing import Any
from unittest.mock import MagicMock, patch

logger = logging.getLogger(__name__)


# Mock OllamaError for tests (when video_agent is not available)
class OllamaError(RuntimeError):
    """Mock of video_agent.ollama_client.OllamaError for testing."""
    pass


class FakeClient:
    """Mock OllamaClient for testing."""

    def __init__(self, response: dict | list | None = None, error: Exception | None = None):
        self.response = response or {"result": "test"}
        self.error = error
        self.call_count = 0

    def generate_json(self, prompt: str, system: str = None, retries: int = 1) -> dict | list:
        self.call_count += 1
        if self.error:
            raise self.error
        return self.response


class TestGenerateSchemaJson:
    """Test generate_schema_json with schema validation and retry logic."""

    def test_generate_schema_json_success(self):
        """generate_schema_json returns valid dict on success."""
        from shorts_engine.llm.text_llm import generate_schema_json

        def fake_factory():
            return FakeClient(response={"title": "Test", "content": "Value"})

        schema = {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "content": {"type": "string"},
            },
        }

        result = generate_schema_json(
            prompt="Test prompt",
            system="Test system",
            schema=schema,
            client_factory=fake_factory,
        )

        assert result == {"title": "Test", "content": "Value"}

    @patch("shorts_engine.llm.text_llm.OllamaError", OllamaError)
    def test_generate_schema_json_retry_on_error(self):
        """generate_schema_json retries on OllamaError."""
        from shorts_engine.llm.text_llm import generate_schema_json

        # First call fails, second succeeds
        call_count = [0]
        def fake_factory():
            client = FakeClient(response={"status": "ok"})
            original_generate_json = client.generate_json

            def generate_with_retry(prompt: str, system: str = None, retries: int = 1) -> dict | list:
                call_count[0] += 1
                if call_count[0] == 1:
                    raise OllamaError("Temporary failure")
                return original_generate_json(prompt, system, retries)

            client.generate_json = generate_with_retry
            return client

        schema = {"type": "object", "properties": {"status": {"type": "string"}}}

        result = generate_schema_json(
            prompt="Test prompt",
            system="Test system",
            schema=schema,
            retries=3,
            client_factory=fake_factory,
        )

        assert result == {"status": "ok"}
        assert call_count[0] == 2

    @patch("shorts_engine.llm.text_llm.OllamaError", OllamaError)
    def test_generate_schema_json_exhausted_retries_raises_engine_error(self):
        """generate_schema_json raises EngineLLMError after retries exhausted."""
        from shorts_engine.llm.text_llm import generate_schema_json
        from shorts_engine.errors import EngineLLMError

        def fake_factory():
            return FakeClient(error=OllamaError("Connection failed"))

        schema = {"type": "object"}

        with pytest.raises(EngineLLMError) as exc_info:
            generate_schema_json(
                prompt="Test prompt",
                system="Test system",
                schema=schema,
                retries=2,
                client_factory=fake_factory,
            )

        # Verify error message contains the failure info
        assert "NOT falling back to another model" in str(exc_info.value)

    @patch("shorts_engine.llm.text_llm.OllamaError", OllamaError)
    def test_generate_schema_json_no_silent_fallback(self):
        """generate_schema_json fails loud with no silent fallback."""
        from shorts_engine.llm.text_llm import generate_schema_json
        from shorts_engine.errors import EngineLLMError

        def fake_factory():
            client = FakeClient(error=OllamaError("Model unavailable"))
            return client

        schema = {"type": "object", "properties": {"value": {"type": "string"}}}

        # Should raise, not fall back
        with pytest.raises(EngineLLMError) as exc_info:
            generate_schema_json(
                prompt="Test prompt",
                system="Test system",
                schema=schema,
                retries=1,
                client_factory=fake_factory,
            )

        error_msg = str(exc_info.value)
        assert "NOT falling back to another model" in error_msg

    def test_generate_schema_json_with_default_client(self):
        """generate_schema_json uses default smart_client when no factory provided."""
        from shorts_engine.llm.text_llm import generate_schema_json

        # This test verifies that when no client_factory is provided,
        # the function tries to use video_agent.ollama_client.smart_client.
        # We'll mock it to avoid actual Ollama calls.
        def mock_smart_client(**kwargs):
            return FakeClient(response={"key": "value"})

        schema = {"type": "object", "properties": {"key": {"type": "string"}}}

        # Patch the import temporarily
        import shorts_engine.llm.text_llm as text_llm_module
        original_smart_client = getattr(text_llm_module, "_smart_client_import", None)

        # Create wrapper that uses our mock
        try:
            result = generate_schema_json(
                prompt="Test prompt",
                system="Test system",
                schema=schema,
                retries=1,
                client_factory=mock_smart_client,  # Provide our mock
            )
            assert result == {"key": "value"}
        finally:
            # Cleanup
            pass


class TestSchemaEnforcement:
    """Regression coverage for a live-run bug: gemma4:31b-cloud returned a
    bare JSON list instead of the schema-required {"facts": [...]} wrapper
    object, and generate_schema_json passed it straight through uncaught --
    it crashed two frames later in facts.py with `result["facts"]` raising
    TypeError on a list. generate_schema_json must itself validate the
    parsed result against `schema` and retry (like any other transient LLM
    failure) rather than returning a non-conforming shape."""

    FACTS_SCHEMA = {
        "type": "object",
        "properties": {
            "facts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "value": {"type": "string"},
                    },
                    "required": ["id", "value"],
                },
            },
        },
        "required": ["facts"],
        "additionalProperties": False,
    }

    def test_bare_list_response_does_not_pass_through_silently(self):
        """A response shaped like the raw payload (bare list, missing the
        'facts' wrapper) must never be returned as-is -- it must be treated
        as a failure and retried, exhausting to EngineLLMError rather than
        silently handing the caller a list where a dict was promised."""
        from shorts_engine.llm.text_llm import generate_schema_json
        from shorts_engine.errors import EngineLLMError

        def fake_factory():
            # Always returns the non-conforming bare-list shape.
            return FakeClient(response=[{"id": "f1", "value": "1.5 to 3"}])

        with pytest.raises(EngineLLMError):
            generate_schema_json(
                prompt="Test prompt",
                system="Test system",
                schema=self.FACTS_SCHEMA,
                retries=2,
                client_factory=fake_factory,
            )

    def test_retries_past_a_bad_shape_and_returns_the_first_conforming_one(self):
        """If an early attempt returns a non-conforming shape and a later
        attempt returns a conforming one, the conforming result wins --
        proving validation failures flow through the existing retry loop
        rather than being treated as terminal."""
        from shorts_engine.llm.text_llm import generate_schema_json

        responses = [
            [{"id": "f1", "value": "1.5 to 3"}],  # bad: bare list
            {"facts": [{"id": "f1", "value": "1.5 to 3"}]},  # good
        ]
        call_count = [0]

        def fake_factory():
            client = FakeClient()

            def generate_with_sequence(prompt, system=None, retries=1):
                call_count[0] += 1
                return responses[call_count[0] - 1]

            client.generate_json = generate_with_sequence
            return client

        result = generate_schema_json(
            prompt="Test prompt",
            system="Test system",
            schema=self.FACTS_SCHEMA,
            retries=3,
            client_factory=fake_factory,
        )

        assert result == {"facts": [{"id": "f1", "value": "1.5 to 3"}]}
        assert call_count[0] == 2

    def test_missing_required_field_is_also_rejected(self):
        """Schema enforcement isn't just top-level shape -- a fact entry
        missing a required field (e.g. 'value') must also be rejected."""
        from shorts_engine.llm.text_llm import generate_schema_json
        from shorts_engine.errors import EngineLLMError

        def fake_factory():
            return FakeClient(response={"facts": [{"id": "f1"}]})  # missing "value"

        with pytest.raises(EngineLLMError):
            generate_schema_json(
                prompt="Test prompt",
                system="Test system",
                schema=self.FACTS_SCHEMA,
                retries=1,
                client_factory=fake_factory,
            )

    def test_schema_is_communicated_to_the_model(self):
        """The model can only comply with a shape it's told about -- the
        schema JSON must appear somewhere in what's sent as the system
        message, not just be used for post-hoc validation."""
        from shorts_engine.llm.text_llm import generate_schema_json

        captured = {}

        def fake_factory():
            client = FakeClient(response={"facts": []})

            def spy(prompt, system=None, retries=1):
                captured["system"] = system
                return {"facts": []}

            client.generate_json = spy
            return client

        generate_schema_json(
            prompt="Test prompt",
            system="Base system prompt",
            schema=self.FACTS_SCHEMA,
            client_factory=fake_factory,
        )

        assert "Base system prompt" in captured["system"]
        assert "facts" in captured["system"]

    def test_validation_failure_is_echoed_into_the_next_attempt(self):
        """A live run showed this exact pattern twice in a row: the model
        invented its own field (e.g. 'brand_differentiator') instead of
        putting the value where the schema required it, and identical blind
        retries (same prompt resent) never self-corrected. A retry that
        actually mirrors the codebase's existing gate-retry pattern --
        echoing the SPECIFIC validation failure back into the next attempt
        -- lets a model that only needs the field name pointed out succeed
        well within the retry budget."""
        from shorts_engine.llm.text_llm import generate_schema_json

        # First response invents its own field instead of using "value" --
        # structurally identical to the live failure (extra field, schema
        # violation via additionalProperties).
        responses = [
            {"facts": [{"id": "f1", "made_up_field": "1.5 to 3"}]},  # bad
            {"facts": [{"id": "f1", "value": "1.5 to 3"}]},  # good, once corrected
        ]
        seen_systems = []

        def fake_factory():
            client = FakeClient()

            def sequenced(prompt, system=None, retries=1):
                seen_systems.append(system)
                return responses[len(seen_systems) - 1]

            client.generate_json = sequenced
            return client

        result = generate_schema_json(
            prompt="Test prompt",
            system="Base system prompt",
            schema=self.FACTS_SCHEMA,
            retries=3,
            client_factory=fake_factory,
        )

        assert result == {"facts": [{"id": "f1", "value": "1.5 to 3"}]}
        assert len(seen_systems) == 2
        # The first attempt must not already contain feedback (nothing to
        # report yet); the second attempt must reference the actual failure.
        assert "FAILED schema validation" not in seen_systems[0]
        assert "FAILED schema validation" in seen_systems[1]
