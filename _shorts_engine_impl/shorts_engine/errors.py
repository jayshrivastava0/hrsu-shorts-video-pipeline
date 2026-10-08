"""
Exception types for shorts_engine.

Hierarchy:
  - EngineError (base)
    - EngineConfigError (configuration issues)
    - EngineLLMError (LLM interaction failures)
    - GateFailure (quality gate violations)
    - HoldForReview (stage stopped on purpose; run ends in hold_for_review)
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class EngineError(Exception):
    """Base exception for shorts_engine errors."""

    pass


class EngineConfigError(EngineError):
    """Raised when configuration is invalid or missing."""

    pass


class EngineLLMError(EngineError):
    """Raised when LLM interaction fails (connection, retry exhausted, etc.)."""

    pass


class GateFailure(EngineError):
    """Raised when one or more deterministic quality gates reject a script.

    Args:
        errors: Human-readable gate failure messages, e.g.
            ["numbers[proof]: '150' in narration does not trace to any
            referenced fact"]. Stored on the exception as `.errors` for
            programmatic access (e.g. CLI reporting); `str(exc)` is the
            messages joined with "; ".
    """

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


class HoldForReview(EngineError):
    """A stage stopped on purpose: nothing crashed, but the content cannot ship
    (too few verified steps, final script below the quality bar...). The run
    ends in the non-publishing status "hold_for_review" with these reasons.

    Args:
        reasons: Human-readable reasons; `str(exc)` joins them with "; ".
    """

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        super().__init__("; ".join(reasons))
