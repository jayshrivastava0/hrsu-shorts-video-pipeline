"""Integration smoke: make_video voice stage builds VoiceSegments from storyboard."""
from unittest.mock import patch, MagicMock
from pathlib import Path
from video_agent.storyboard import (
    Storyboard, Scene, VisualConcept, HeroClaim, CriticNotes, Cinematography,
)
from video_agent.voiceover import VoiceSegment


def _make_sb_with_prosody():
    sb = Storyboard(version="2.2", blog={"region": "australia", "category": "mining"})
    sb.hero_claim = HeroClaim(stat="$2B", claim_text="Test claim")
    for beat, prosody in [("hook", "hook_emphasis"), ("mechanism", "conversational"),
                           ("cta", "warm_cta")]:
        cin = Cinematography()
        cin.voice_prosody = prosody
        s = Scene(
            index=len(sb.scenes), beat=beat,
            narration=f"{beat} narration text",
            on_screen_text="",
            duration_target_s=6.0,
            visual_concept=VisualConcept("test", "", "photo", "problem"),
            critic_notes=CriticNotes(),
            cinematography=cin,
        )
        sb.scenes.append(s)
    return sb


def test_build_voice_segments_from_storyboard():
    """build_voice_segments returns one VoiceSegment per scene with the right prosody."""
    from scripts.make_video import build_voice_segments
    sb = _make_sb_with_prosody()
    segs = build_voice_segments(sb)
    assert len(segs) == 3
    assert segs[0].text == "hook narration text"
    assert segs[0].prosody == "hook_emphasis"
    assert segs[1].prosody == "conversational"
    assert segs[2].prosody == "warm_cta"


def test_build_voice_segments_fallback_no_cinematography():
    """Scene with cinematography=None falls back to 'conversational' prosody."""
    from scripts.make_video import build_voice_segments
    sb = _make_sb_with_prosody()
    sb.scenes[1].cinematography = None
    segs = build_voice_segments(sb)
    assert segs[1].prosody == "conversational"


def test_build_voice_segments_fallback_empty_prosody():
    """Scene with voice_prosody='' falls back to 'conversational'."""
    from scripts.make_video import build_voice_segments
    sb = _make_sb_with_prosody()
    sb.scenes[0].cinematography.voice_prosody = ""
    segs = build_voice_segments(sb)
    assert segs[0].prosody == "conversational"
