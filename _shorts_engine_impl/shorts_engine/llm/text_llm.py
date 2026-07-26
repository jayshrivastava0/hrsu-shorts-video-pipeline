"""
Schema-validated LLM helper with retry logic and fail-loud behavior.

This module provides:
- `generate_schema_json()`: Generate text via LLM with schema validation
- Retry logic with exponential backoff
- Fail-loud behavior: raises EngineLLMError on failure, never silently falls back
- No fallback to local model — on error, raises with message "NOT falling back to another model"

The schema is both communicated to the model (folded into the system
message so it knows the exact required shape) and enforced on the way back
(jsonschema.validate against the parsed result). A response that doesn't
conform -- wrong top-level type, missing required keys, wrong field types --
is treated exactly like an OllamaError: retried, then fail-loud. Small/cloud
models routinely ignore prose-only shape instructions (e.g. returning a bare
JSON array when a `{"facts": [...]}` wrapper object was required); without
this, callers indexing the result (e.g. `result["facts"]`) crash with a
TypeError instead of getting a clear, retried, fail-loud error.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

import jsonschema

logger = logging.getLogger(__name__)

# Try importing OllamaError; allow test mocking
try:
    from video_agent.ollama_client import OllamaError
except ImportError:
    # Allow tests to define their own OllamaError
    class OllamaError(RuntimeError):  # type: ignore
        pass

# Lazy imports to avoid circular dependencies
_smart_client = None


def _get_smart_client(**kwargs):
    """Lazy-load smart_client from video_agent.ollama_client."""
    global _smart_client
    if _smart_client is None:
        try:
            from video_agent.ollama_client import smart_client
            _smart_client = smart_client
        except ImportError as e:
            logger.error(f"Could not import smart_client from video_agent.ollama_client: {e}")
            raise
    return _smart_client(**kwargs)


def generate_schema_json(
    prompt: str,
    system: str,
    schema: dict,
    *,
    retries: int = 3,
    local_only: bool = False,
    client_factory: Callable | None = None,
) -> dict:
    """Generate structured JSON text via schema-validated LLM.

    Args:
        prompt: The user prompt requesting JSON output
        system: System prompt for context/instructions
        schema: JSON Schema dict for output validation (currently used for type hints)
        retries: Number of retries on OllamaError (default: 3)
        local_only: If True, only use local model; currently unused (reserved for future)
        client_factory: Test hook; callable returning client with .generate_json() method.
                       If None, uses video_agent.ollama_client.smart_client()

    Returns:
        Parsed dict from model response, validated against `schema`.

    Raises:
        EngineLLMError: On any failure with retry exhausted (including a
                       response that never conforms to `schema`); includes
                       message "NOT falling back to another model" to
                       emphasize fail-loud.
    """
    from shorts_engine.config import LLM_RETRY_DELAY_S
    from shorts_engine.errors import EngineLLMError

    # Get client
    if client_factory is not None:
        client = client_factory()
    else:
        client = _get_smart_client()

    system_with_schema = (
        f"{system}\n\nYour entire response MUST be a single JSON value that "
        f"validates against this JSON Schema exactly -- correct top-level "
        f"type, every required key present, no extra keys:\n"
        f"{json.dumps(schema)}"
    )

    last_error = None
    validation_feedback = ""
    for attempt in range(1, retries + 1):
        try:
            logger.debug(f"Attempt {attempt}/{retries}: Generating JSON via LLM")
            result = client.generate_json(
                prompt, system=system_with_schema + validation_feedback, retries=1
            )
            jsonschema.validate(instance=result, schema=schema)
            logger.debug(f"LLM generation successful")
            return result
        except OllamaError as e:
            last_error = e
            logger.warning(
                f"LLM error on attempt {attempt}/{retries}: {e}"
            )
            if attempt < retries:
                # Exponential backoff: 2s, 4s, 8s, etc.
                delay = LLM_RETRY_DELAY_S * (2 ** (attempt - 1))
                logger.debug(f"Retrying in {delay}s...")
                time.sleep(delay)
        except jsonschema.ValidationError as e:
            # Content problem, not a transport problem -- echo the SPECIFIC
            # failure into the next attempt's system message (same pattern
            # script.py's gate-retry already uses for gate errors) rather
            # than blindly resending an identical prompt. A live run showed
            # blind retries never self-correct a model's tendency to invent
            # its own field name instead of using the one the schema names.
            last_error = e
            where = "/".join(str(p) for p in e.absolute_path) or "top level"
            validation_feedback = (
                f"\n\nYour previous response FAILED schema validation at "
                f"'{where}': {e.message}. Fix this and respond again with "
                f"ONLY valid JSON matching the schema."
            )
            logger.warning(
                f"Schema validation failed on attempt {attempt}/{retries}: {e.message}"
            )
            if attempt < retries:
                delay = LLM_RETRY_DELAY_S * (2 ** (attempt - 1))
                logger.debug(f"Retrying in {delay}s...")
                time.sleep(delay)
        except Exception as e:
            # Catch other errors and wrap them
            last_error = e
            logger.error(f"Unexpected error on attempt {attempt}/{retries}: {e}")
            if attempt < retries:
                delay = LLM_RETRY_DELAY_S * (2 ** (attempt - 1))
                logger.debug(f"Retrying in {delay}s...")
                time.sleep(delay)

    # All retries exhausted: fail loud, no fallback
    error_msg = (
        f"LLM schema validation failed after {retries} retries: {last_error} — "
        "NOT falling back to another model"
    )
    logger.error(error_msg)
    raise EngineLLMError(error_msg) from last_error
