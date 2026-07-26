from video_agent.agents.storyboarder import _validate_on_screen_text


def test_text_too_long_pre_set_flag_returned():
    flag = _validate_on_screen_text(
        "This is a sentence with too many words to be a chart label",
        narration="Calcium nitrate works.")
    assert flag == "text_too_long"


def test_text_paraphrases_narration_flag():
    flag = _validate_on_screen_text(
        "WASTEWATER COSTS RISING",
        narration="Are wastewater costs rising for your plant?")
    assert flag == "text_duplicates_voice"


def test_text_with_number_passes():
    flag = _validate_on_screen_text(
        "90% H2S CUT",
        narration="Calcium nitrate makes a real difference.")
    assert flag is None


def test_text_with_brand_passes():
    flag = _validate_on_screen_text(
        "REACH-GRADE BY HRSU",
        narration="We supply industrial grade chemicals.")
    assert flag is None
