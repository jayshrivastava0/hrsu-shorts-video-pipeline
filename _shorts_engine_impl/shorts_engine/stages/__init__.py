"""Processing stages for shorts_engine.

This subpackage implements:
- Script generation and validation
- Beat/scene planning
- Quality gates and guardrails
- Output serialization
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
