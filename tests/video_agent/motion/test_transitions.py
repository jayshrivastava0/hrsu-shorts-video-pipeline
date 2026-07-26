from video_agent.motion.transitions import transition_between


def test_hook_to_stakes_is_slow_fade():
    assert transition_between("hook", "stakes") == "slow_fade"


def test_proof_to_cta_is_dissolve_flash():
    assert transition_between("proof", "cta") == "dissolve_flash"


def test_proof_to_brand_is_dissolve_flash():
    assert transition_between("proof", "brand") == "dissolve_flash"


def test_within_same_beat_is_cut():
    assert transition_between("mechanism", "mechanism") == "cut"
