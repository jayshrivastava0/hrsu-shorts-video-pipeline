from unittest.mock import patch
from video_agent.agents.storyboarder import Storyboarder
from video_agent.storyboard import Storyboard, HeroClaim, Beat


_FAKE = {
    "scenes": [
        {"narration": "Are wastewater costs rising?",
         "on_screen_text": "90% H2S CUT",
         "visual_concept": {"subject": "wastewater plant", "modifier": "aerial",
                            "type": "photo", "mood": "problem",
                            "style_hint": "documentary"}},
        {"narration": "Untreated H2S corrodes pipes.",
         "on_screen_text": "$5K/MONTH PIPE LOSS",
         "visual_concept": {"subject": "corroded steel pipe", "modifier": "rust",
                            "type": "photo", "mood": "problem",
                            "style_hint": "documentary"}},
        {"narration": "Calcium nitrate oxidises sulfide to sulfate.",
         "on_screen_text": "S²⁻ → SO₄²⁻",
         "visual_concept": {"subject": "sulfide oxidation chemical equation",
                            "modifier": "diagram", "type": "diagram",
                            "mood": "mechanism", "style_hint": "scientific"}},
        {"narration": "At Hunter Valley, 98% sulfide removal.",
         "on_screen_text": "HUNTER VALLEY: 98%",
         "visual_concept": {"subject": "australian mine site",
                            "modifier": "aerial drone",
                            "type": "photo", "mood": "proof",
                            "style_hint": "documentary"}},
        {"narration": "HRSU supplies REACH-grade calcium nitrate.",
         "on_screen_text": "REACH-GRADE",
         "visual_concept": {"subject": "calcium nitrate bag stockpile",
                            "modifier": "industrial", "type": "photo",
                            "mood": "brand", "style_hint": "branded"}},
    ]
}


def test_storyboarder_creates_one_scene_per_beat():
    sb = Storyboard(version="2.0",
                    blog={"id": "b", "url": "u", "title": "t",
                          "region": "australia", "category": "mining",
                          "persona": "procurement"})
    sb.hero_claim = HeroClaim(stat="90%", claim_text="cuts H2S 90%")
    sb.arc = [
        Beat(index=0, beat="hook", purpose="hook", duration_target_s=3.5),
        Beat(index=1, beat="stakes", purpose="stakes", duration_target_s=6.0),
        Beat(index=2, beat="mechanism", purpose="mech", duration_target_s=10.0),
        Beat(index=3, beat="proof", purpose="proof", duration_target_s=10.0),
        Beat(index=4, beat="cta", purpose="cta", duration_target_s=5.0),
    ]
    with patch("video_agent.agents.storyboarder.OllamaClient") as mock_cls:
        mock_cls.return_value.generate_json.return_value = _FAKE
        Storyboarder().run(sb)
    assert len(sb.scenes) == 5
    assert [s.beat for s in sb.scenes] == ["hook", "stakes", "mechanism",
                                            "proof", "cta"]
    assert sb.scenes[2].visual_concept.type == "diagram"
    assert sb.scenes[0].on_screen_text == "90% H2S CUT"
