"""Beat-aware transition selector. Phase 4 only chooses the kind; the
composer applies it (xfade / fade / hard cut)."""


def transition_between(prev_beat: str, curr_beat: str) -> str:
    """Select transition type between two beats.

    Args:
        prev_beat: The previous beat name (e.g., "hook", "stakes")
        curr_beat: The current beat name (e.g., "proof", "cta")

    Returns:
        The transition type: "cut", "whip_pan", or "fade"
    """
    if prev_beat == curr_beat:
        return "cut"
    if (prev_beat, curr_beat) == ("hook", "stakes"):
        return "slow_fade"
    if prev_beat == "proof" and curr_beat in ("brand", "cta"):
        return "dissolve_flash"
    return "fade"
