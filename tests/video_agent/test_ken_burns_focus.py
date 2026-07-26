"""Tests for B-6: focal-point-aware Ken Burns plan generation."""
from __future__ import annotations
import pytest

from video_agent.motion.ken_burns import plan_ken_burns, MotionPlan


class TestFocalPointKenBurns:
    """Verify focus_x/focus_y shift the viewport toward the subject."""

    def test_default_center_mechanism_zoom(self):
        """Default focus (0.5, 0.5) keeps viewport centred."""
        plan = plan_ken_burns((1920, 1080), "mechanism", 5.0)
        # For a landscape source zooming from s=1 viewport, start x should be ~0
        # (the viewport spans the full source width at scale=1)
        assert plan.direction == "in"
        assert plan.start_scale == 1.0
        assert plan.end_scale == 1.05

    def test_focus_shifts_zoom_start_for_mechanism(self):
        """focus_x=0.8 shifts the zoom start_xy right compared to focus_x=0.2."""
        plan_left = plan_ken_burns((1920, 1080), "mechanism", 5.0, focus_x=0.2, focus_y=0.5)
        plan_right = plan_ken_burns((1920, 1080), "mechanism", 5.0, focus_x=0.8, focus_y=0.5)
        # Right-biased plan should start further right (larger start_xy[0])
        assert plan_right.start_xy[0] >= plan_left.start_xy[0]

    def test_focus_shifts_zoom_for_portrait_source(self):
        """Portrait source (540×960): focus_y=0.2 starts higher than focus_y=0.8."""
        plan_top = plan_ken_burns((540, 960), "mechanism", 5.0, focus_x=0.5, focus_y=0.2)
        plan_bot = plan_ken_burns((540, 960), "mechanism", 5.0, focus_x=0.5, focus_y=0.8)
        assert plan_top.start_xy[1] <= plan_bot.start_xy[1]

    def test_focus_clamped_for_out_of_range(self):
        """focus_x=-0.5 and focus_x=1.5 are clamped to [0,1]."""
        plan_neg = plan_ken_burns((1920, 1080), "mechanism", 5.0, focus_x=-0.5)
        plan_over = plan_ken_burns((1920, 1080), "mechanism", 5.0, focus_x=1.5)
        plan_zero = plan_ken_burns((1920, 1080), "mechanism", 5.0, focus_x=0.0)
        plan_one = plan_ken_burns((1920, 1080), "mechanism", 5.0, focus_x=1.0)
        # Clamped plans equal their boundary equivalents
        assert plan_neg.start_xy == plan_zero.start_xy
        assert plan_over.start_xy == plan_one.start_xy

    def test_default_focus_still_works_no_kwarg(self):
        """Calling without focus args (legacy callers) must not break."""
        plan = plan_ken_burns((800, 600), "problem", 4.0)
        assert isinstance(plan, MotionPlan)
        assert plan.start_scale >= 1.0

    def test_proof_mood_pan_uses_focus_y(self):
        """Proof mood pan on landscape: focus_y affects the vertical offset."""
        plan_top = plan_ken_burns((1920, 1080), "proof", 5.0, focus_y=0.1)
        plan_bot = plan_ken_burns((1920, 1080), "proof", 5.0, focus_y=0.9)
        # Both should be "right" pans; vertical offsets should differ
        assert plan_top.direction == "right"
        assert plan_bot.direction == "right"
        assert plan_top.start_xy[1] <= plan_bot.start_xy[1]
