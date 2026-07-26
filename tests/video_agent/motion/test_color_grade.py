from video_agent.motion.color_grade import grade_filter_for_palette, grade_filter_for_mood


def test_palette_red_tension_returns_filter():
    flt = grade_filter_for_palette("red_tension")
    assert flt and "colorchannelmixer" in flt

def test_palette_cold_blue_returns_filter():
    flt = grade_filter_for_palette("cold_blue")
    assert flt and "colorchannelmixer" in flt

def test_palette_warm_brand_returns_filter():
    flt = grade_filter_for_palette("warm_brand")
    assert flt

def test_palette_neutral_doc_returns_none():
    assert grade_filter_for_palette("neutral_doc") is None

def test_palette_urgent_amber_returns_filter():
    assert grade_filter_for_palette("urgent_amber")

def test_palette_clinical_white_returns_filter():
    assert grade_filter_for_palette("clinical_white")

def test_unknown_palette_returns_none():
    assert grade_filter_for_palette("pink_unicorn") is None

def test_mood_fallback_still_works():
    """Legacy mood-based call still produces a filter for 'problem'."""
    assert grade_filter_for_mood("problem") is not None
    assert grade_filter_for_mood("unknown_mood") is None
