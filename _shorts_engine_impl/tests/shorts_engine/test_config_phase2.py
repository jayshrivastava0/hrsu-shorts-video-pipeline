"""Task 1: Config Phase 2/3/4 constants and domain list fixes."""
from __future__ import annotations
from pathlib import Path


class TestPhase2Constants:
    def test_canvas_and_margins(self):
        from shorts_engine import config
        assert (config.CANVAS_W, config.CANVAS_H, config.FPS) == (1080, 1920, 30)
        assert config.SAFE_TOP_PX == 220
        assert config.SAFE_BOTTOM_PX == 420
        assert config.SAFE_SIDE_PX == 72

    def test_shot_bounds_and_duration_law(self):
        from shorts_engine import config
        assert (config.SHOT_MIN_S, config.SHOT_MAX_S) == (1.8, 4.5)
        assert (config.SHOT_TARGET_MIN_S, config.SHOT_TARGET_MAX_S) == (2.0, 3.5)
        assert config.LOGO_CTA_MAX_S == 10.0
        assert (config.TOTAL_MIN_S, config.TOTAL_MAX_S) == (35.0, 50.0)
        assert config.END_CARD_HOLD_S == 1.5
        assert config.AUDIO_COMPLETENESS_MARGIN_S == 1.4
        assert config.AUDIO_DURATION_TOLERANCE == 0.65
        assert config.MIN_SEGMENT_BYTES == 1024
        assert config.TRANSITION_FADE_S == 0.25

    def test_brand_colors(self):
        from shorts_engine import config
        assert config.BRAND_GOLD.lower() == "#d4af37"
        assert config.BRAND_DARK_NAVY.lower() == "#0a192f"
        assert config.BRAND_NAVY_2.lower() == "#0a1428"
        assert config.BRAND_TEXT_LIGHT.lower() == "#ccd6f6"

    def test_prosody_map_covers_all_beats(self):
        from shorts_engine import config
        assert set(config.PROSODY_BY_BEAT) == {"hook", "stakes", "mechanism", "proof", "cta"}
        assert config.PROSODY_BY_BEAT["cta"] == "warm_cta"

    def test_video_agent_import_actually_works(self):
        # Regression for the sys.path bug: PROJECT_ROOT (not its parent) must be
        # on sys.path so video_agent.config is importable.
        import sys
        from shorts_engine import config
        assert str(config.PROJECT_ROOT) in sys.path
        import video_agent.config as vac
        assert vac.SMART_TEXT_MODEL == config.SMART_TEXT_MODEL

    def test_domain_lists_match_spec(self):
        from shorts_engine import config
        for d in ("springer.com", "mdpi.com", "wiley.com", "arxiv.org", "doi.org",
                  "pubmed.ncbi.nlm.nih.gov", "sciencedirect.com"):
            assert d in config.PAPER_DOMAINS, d
        for d in ("europa.eu", "epa.gov", "iso.org"):
            assert d in config.STANDARD_DOMAINS, d
        # news sites are NOT standards bodies
        assert "bbc.com" not in config.STANDARD_DOMAINS
        assert "forbes.com" not in config.STANDARD_DOMAINS

    def test_logo_file_points_at_asset_library(self):
        from shorts_engine import config
        assert config.BRAND_LOGO_FILE == config.PROJECT_ROOT / "asset_library" / "brand" / "Logo.png"
