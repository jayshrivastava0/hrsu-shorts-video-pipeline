"""
shorts_engine: Platform for generating short-form video scripts with LLM guidance.

Package exports:
  - config: Configuration constants and paths
  - errors: Exception hierarchy (EngineError, EngineConfigError, EngineLLMError, GateFailure)
  - llm: LLM interface and routing
  - stages: Processing pipeline stages

IMPORTANT BOUNDARY RULE:
  This package does NOT export anything from video_agent to upstream callers.
  video_agent.config is consumed internally by shorts_engine.config only.
"""
from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)

# ── Guard: Prevent accidental video_agent leakage ──────────────────────────
# If someone tries to access video_agent via shorts_engine, raise a clear error.
def __getattr__(name: str):
    """Raise error if caller tries to access forbidden modules."""
    if name.startswith("video_agent"):
        raise AttributeError(
            f"shorts_engine does not export '{name}'. "
            "Import from video_agent directly if needed, but shorts_engine is "
            "designed to NOT re-export video_agent to maintain package boundaries."
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ── Public API ─────────────────────────────────────────────────────────────
# Expose only the subpackages/modules that form the public API.
# Individual exports are handled by their __init__.py files.

from . import config, errors, llm, manifest, stages

__all__ = ["config", "errors", "llm", "manifest", "stages"]

logger.debug("shorts_engine initialized with package boundary guard")
