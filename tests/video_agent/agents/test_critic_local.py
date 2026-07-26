from unittest.mock import patch
from video_agent.agents.critic_local import LocalCritic
from video_agent.storyboard import (
    Storyboard, HeroClaim, Beat, Scene, VisualConcept, AssetCandidate,
)


def _fixture():
    sb = Storyboard(version="2.0",
                    blog={"id": "b", "url": "u", "title": "t",
                          "region": "australia", "category": "mining",
                          "persona": "procurement"},
                    hero_claim=HeroClaim(stat="90%", claim_text="cuts H2S 90%"),
                    arc=[Beat(index=0, beat="hook", purpose="x",
                              duration_target_s=4.0)],
                    scenes=[Scene(
                        index=0, beat="hook",
                        narration="Are wastewater costs rising?",
                        on_screen_text="ARE WASTEWATER COSTS RISING",
                        visual_concept=VisualConcept(
                            subject="cat", modifier="", type="photo",
                            mood="problem", style_hint=""),
                        duration_target_s=4.0, transition_in="cut",
                        chosen_asset=AssetCandidate(
                            source="g", url="u", score=70,
                            local_path="x.jpg",
                            caption="cat playing piano"),
                    )])
    return sb


def test_local_critic_flags_text_duplicates_voice():
    sb = _fixture()
    fake_resp = {
        "alignment_score": 5,
        "flags": ["text_duplicates_voice", "voice_visual_mismatch"],
        "revision": "Replace on-screen text with the hero stat (90%)."
    }
    with patch("video_agent.agents.critic_local.OllamaClient") as M:
        M.return_value.generate_json.return_value = fake_resp
        LocalCritic().run(sb)
    notes = sb.scenes[0].critic_notes
    assert notes.alignment_score == 5
    assert "text_duplicates_voice" in notes.flags
