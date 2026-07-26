"""Tests for C-3: SCRIPT_BANNED_PHRASES contains new bite-rule phrases."""
from __future__ import annotations
from video_agent.config import SCRIPT_BANNED_PHRASES


def test_new_banned_phrases_present():
    """C-3 spec adds 5 new banned phrases to SCRIPT_BANNED_PHRASES."""
    new_phrases = ["in conclusion", "let's dive in", "unlock", "game-changer", "in today's"]
    for phrase in new_phrases:
        assert phrase in SCRIPT_BANNED_PHRASES, (
            f"Expected banned phrase {phrase!r} not in SCRIPT_BANNED_PHRASES"
        )


def test_original_banned_phrases_still_present():
    """Original phrases from before C-3 must remain."""
    original = ["as an ai", "in this video", "thanks for watching",
                "hope you enjoyed", "i don't have", "as of my last update"]
    for phrase in original:
        assert phrase in SCRIPT_BANNED_PHRASES, (
            f"Original banned phrase {phrase!r} was removed"
        )
