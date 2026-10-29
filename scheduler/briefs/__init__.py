"""Brief-driven video types: shared helpers (facts tiers, etc.)."""
import sys
from pathlib import Path

# The briefs package imports `shorts_engine` in-process (web search, text-model adapters); the engine
# lives in _shorts_engine_impl and is otherwise only put on PYTHONPATH for subprocess calls.
_ENGINE_PATH = str(Path(__file__).resolve().parents[2] / "_shorts_engine_impl")
if _ENGINE_PATH not in sys.path:
    sys.path.insert(0, _ENGINE_PATH)
