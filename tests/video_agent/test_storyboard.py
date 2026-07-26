# tests/video_agent/test_storyboard.py
import json
from pathlib import Path
from video_agent.storyboard import (
    Storyboard, HeroClaim, Beat, Scene, VisualConcept,
    load_storyboard, save_storyboard,
)


def test_storyboard_round_trip(tmp_path):
    sb = Storyboard(
        version="2.0",
        blog={"id": "b1", "url": "u", "title": "t", "region": "australia",
              "category": "mining", "persona": "procurement"},
        hero_claim=HeroClaim(stat="90%", claim_text="cuts H2S 90%",
                             source_quote="..."),
        arc=[Beat(index=0, beat="hook", purpose="hook", duration_target_s=3.5)],
        supporting_facts=[],
        scenes=[Scene(
            index=0, beat="hook",
            narration="Are wastewater costs rising?",
            on_screen_text="90% H2S CUT",
            visual_concept=VisualConcept(
                subject="wastewater plant", modifier="aerial",
                type="photo", mood="problem", style_hint="documentary"),
            duration_target_s=3.5, transition_in="cut",
        )],
    )
    path = tmp_path / "storyboard.json"
    save_storyboard(sb, path)
    sb2 = load_storyboard(path)
    assert sb2.hero_claim.stat == "90%"
    assert sb2.scenes[0].visual_concept.subject == "wastewater plant"
    assert sb2.version == "2.0"


def test_scene_defaults():
    s = Scene(index=0, beat="hook", narration="n", on_screen_text="t",
              visual_concept=VisualConcept(subject="x", modifier="y",
                                           type="photo", mood="problem",
                                           style_hint=""),
              duration_target_s=3.0, transition_in="cut")
    assert s.asset_candidates == []
    assert s.chosen_asset is None
    assert s.degraded is False


def test_cinematography_defaults():
    from video_agent.storyboard import Cinematography
    c = Cinematography()
    assert c.color_grade == "neutral_doc"
    assert c.transition_in == "cut"
    assert c.motion == "slow_push"
    assert c.voice_prosody == "conversational"
    assert c.reasoning == ""

def test_scene_has_optional_cinematography_field():
    from video_agent.storyboard import Scene, VisualConcept
    s = Scene(
        index=0, beat="hook", narration="x", on_screen_text="",
        visual_concept=VisualConcept(subject="", modifier="", type="photo",
                                     mood="problem"),
        duration_target_s=3.0,
    )
    assert s.cinematography is None

def test_storyboard_has_narrative_thread():
    from video_agent.storyboard import Storyboard
    sb = Storyboard(version="2.2", blog={})
    assert sb.narrative_thread == []

def test_storyboard_roundtrip_includes_cinematography(tmp_path):
    from video_agent.storyboard import (
        Storyboard, Scene, VisualConcept, Cinematography,
        save_storyboard, load_storyboard,
    )
    sb = Storyboard(version="2.2", blog={"region": "usa"})
    sb.narrative_thread = [["pipe", "scaling"], ["calcium", "ions"]]
    sb.scenes = [Scene(
        index=0, beat="hook", narration="n", on_screen_text="",
        visual_concept=VisualConcept(subject="", modifier="",
                                     type="photo", mood="problem"),
        duration_target_s=3.0,
        cinematography=Cinematography(color_grade="red_tension",
                                      transition_in="hard_cut",
                                      motion="fast_zoom",
                                      voice_prosody="hook_emphasis",
                                      reasoning="dramatic open"),
    )]
    path = tmp_path / "sb.json"
    save_storyboard(sb, path)
    loaded = load_storyboard(path)
    assert loaded.narrative_thread == [["pipe", "scaling"], ["calcium", "ions"]]
    assert loaded.scenes[0].cinematography.color_grade == "red_tension"
    assert loaded.scenes[0].cinematography.voice_prosody == "hook_emphasis"
