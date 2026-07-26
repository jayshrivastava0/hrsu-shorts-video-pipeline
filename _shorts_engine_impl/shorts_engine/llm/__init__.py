"""LLM interface for shorts_engine.

This subpackage handles:
- Routing text/vision requests to the appropriate model
- Retry logic and timeout management
- Structured output parsing
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
