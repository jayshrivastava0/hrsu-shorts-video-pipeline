"""
Test package boundaries and forbidden imports for shorts_engine.

Tests verify:
1. shorts_engine.config has required constants
2. shorts_engine.errors has required exception classes
3. Forbidden imports are prevented at package boundaries
"""
from __future__ import annotations

import logging
import sys
import pytest

logger = logging.getLogger(__name__)


class TestConfigConstants:
    """Test that shorts_engine.config exposes required constants."""

    def test_config_beat_template(self):
        """BEAT_TEMPLATE is the locked five-beat procurement template
        (spec §4 Stage 3): hook 2-4s, stakes 4-6s, mechanism 8-12s,
        proof 6-10s, cta 6-8s, in that exact order."""
        from shorts_engine import config
        assert hasattr(config, "BEAT_TEMPLATE")
        assert isinstance(config.BEAT_TEMPLATE, list)
        beats = [b["beat"] for b in config.BEAT_TEMPLATE]
        assert beats == ["hook", "stakes", "mechanism", "proof", "cta"]
        assert config.BEAT_TEMPLATE[0] == {"beat": "hook", "min_s": 2.0, "max_s": 4.0}
        assert config.BEAT_TEMPLATE[2] == {"beat": "mechanism", "min_s": 8.0, "max_s": 12.0}
        assert config.BEAT_TEMPLATE[4] == {"beat": "cta", "min_s": 6.0, "max_s": 8.0}

    def test_config_word_budget(self):
        """Word budget and tolerance constants exist."""
        from shorts_engine import config
        assert hasattr(config, "WORDS_PER_SECOND")
        assert hasattr(config, "WORD_BUDGET_TOLERANCE")
        assert isinstance(config.WORDS_PER_SECOND, (int, float))
        assert isinstance(config.WORD_BUDGET_TOLERANCE, (int, float))

    def test_config_script_filters(self):
        """Script filtering and domain constants exist."""
        from shorts_engine import config
        assert hasattr(config, "FEAR_FILLER_PATTERNS")
        assert hasattr(config, "PAPER_DOMAINS")
        assert hasattr(config, "STANDARD_DOMAINS")
        assert hasattr(config, "LLM_MAX_RETRIES")
        assert isinstance(config.FEAR_FILLER_PATTERNS, (list, tuple, set))
        assert isinstance(config.PAPER_DOMAINS, (list, tuple, set))
        assert isinstance(config.STANDARD_DOMAINS, (list, tuple, set))
        assert isinstance(config.LLM_MAX_RETRIES, int)

    def test_config_paths(self):
        """Output base and paths constants exist."""
        from shorts_engine import config
        assert hasattr(config, "OUTPUT_BASE")
        assert hasattr(config, "BRAND_FACTS_PATH")
        assert hasattr(config, "PROJECT_ROOT")


class TestErrorClasses:
    """Test that shorts_engine.errors exposes required exceptions."""

    def test_engine_error_exists(self):
        """EngineError base exception exists."""
        from shorts_engine import errors
        assert hasattr(errors, "EngineError")
        assert issubclass(errors.EngineError, Exception)

    def test_config_error_exists(self):
        """EngineConfigError exception exists."""
        from shorts_engine import errors
        assert hasattr(errors, "EngineConfigError")
        assert issubclass(errors.EngineConfigError, errors.EngineError)

    def test_llm_error_exists(self):
        """EngineLLMError exception exists."""
        from shorts_engine import errors
        assert hasattr(errors, "EngineLLMError")
        assert issubclass(errors.EngineLLMError, errors.EngineError)

    def test_gate_failure_exists(self):
        """GateFailure exception exists."""
        from shorts_engine import errors
        assert hasattr(errors, "GateFailure")
        assert issubclass(errors.GateFailure, errors.EngineError)


class TestForbiddenImports:
    """Test that forbidden imports are caught at package boundaries."""

    def test_shorts_engine_init_no_video_agent_import(self):
        """shorts_engine/__init__.py does not import video_agent."""
        # Read the init file and verify it doesn't directly import video_agent
        # (excluding docstring/comment lines)
        from pathlib import Path
        init_file = Path(__file__).parent.parent.parent / "shorts_engine" / "__init__.py"
        content = init_file.read_text()

        # Check only actual code lines (not docstrings/comments)
        lines = content.split("\n")
        code_lines = [
            l.strip()
            for l in lines
            if l.strip() and not l.strip().startswith("#")
            and not l.strip().startswith('"""') and not l.strip().startswith("'''")
        ]
        code_text = "\n".join(code_lines)

        # Forbidden patterns that would break boundaries
        assert "import video_agent" not in code_text
        assert "from video_agent import" not in code_text
        logger.info("✓ shorts_engine.__init__.py has no video_agent imports")

    def test_config_imports_video_agent_carefully(self):
        """shorts_engine/config.py consumes video_agent.config without re-exporting."""
        # Config can import from video_agent for values, but should not expose it
        from pathlib import Path
        config_file = Path(__file__).parent.parent.parent / "shorts_engine" / "config.py"
        content = config_file.read_text()

        # Must have a way to import from video_agent.config
        assert "video_agent.config" in content or "from video_agent import config" in content
        logger.info("✓ shorts_engine.config properly consumes video_agent.config")


class TestForbiddenTransitiveImports:
    FORBIDDEN = [
        "video_agent.orchestrator", "video_agent.storyboard",
        "video_agent.script_builder", "video_agent.composer",
        "video_agent.run_stage", "video_agent.harness.runner",
        "video_agent.harness.rubric", "video_agent.harness.revise_router",
        "video_agent.harness.verify_vision", "video_agent.sources.scoring",
    ]

    def test_no_stage_module_imports_forbidden_modules(self):
        """Spec §3.2 CI guard: importing every shorts_engine stage must not
        pull any superseded video_agent module into sys.modules."""
        import importlib, sys
        for mod in ("ingest", "facts", "script", "shotlist", "audio",
                    "visuals", "assemble", "verify", "package", "publish"):
            importlib.import_module(f"shorts_engine.stages.{mod}")
        loaded = set(sys.modules)
        hits = [f for f in self.FORBIDDEN if f in loaded]
        assert hits == [], f"forbidden modules imported: {hits}"
