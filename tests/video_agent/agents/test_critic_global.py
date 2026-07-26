from unittest.mock import patch
from video_agent.agents.critic_global import GlobalDirector
from video_agent.storyboard import (
    Storyboard, HeroClaim, Beat, Scene, VisualConcept,
)


def _fix():
    sb = Storyboard(version="2.0",
                    blog={"id": "b", "url": "u", "title": "t",
                          "region": "australia", "category": "mining",
                          "persona": "procurement"},
                    hero_claim=HeroClaim(stat="90%", claim_text="cuts H2S 90%"),
                    arc=[Beat(index=i, beat=b, purpose="",
                              duration_target_s=4.0)
                         for i, b in enumerate(["hook", "stakes", "mechanism",
                                                "proof", "cta"])],
                    scenes=[Scene(index=i, beat=b, narration=f"n{i}",
                                  on_screen_text=f"t{i}",
                                  visual_concept=VisualConcept(
                                      subject="x", modifier="",
                                      type="photo", mood="problem",
                                      style_hint=""),
                                  duration_target_s=4.0,
                                  transition_in="cut")
                            for i, b in enumerate(["hook", "stakes",
                                                   "mechanism", "proof", "cta"])])
    return sb


def test_director_populates_director_notes():
    sb = _fix()
    fake = {"arc_quality": 7, "hero_claim_supported": True,
            "weakest_beat": 2, "missing": ["regional anchor in proof"],
            "redundant": [], "ending_strength": 8,
            "revision_for_strategist": "Add Hunter Valley reference to proof"}
    with patch("video_agent.agents.critic_global.OllamaClient") as M:
        M.return_value.generate_json.return_value = fake
        GlobalDirector().run(sb)
    assert sb.director_notes.arc_quality == 7
    assert sb.director_notes.weakest_beat == 2
    assert "regional anchor in proof" in sb.director_notes.missing
