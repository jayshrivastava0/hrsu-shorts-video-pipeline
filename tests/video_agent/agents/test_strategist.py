from unittest.mock import patch
from video_agent.agents.strategist import Strategist
from video_agent.storyboard import Storyboard


_FAKE_OLLAMA = {
    "hero": {"stat": "90%", "claim_text": "Calcium nitrate cuts H2S by 90%",
             "source_quote": "Field trials at Hunter Valley showed 90% removal"},
    "arc": [
        {"beat": "hook", "purpose": "Hook with the 90% stat", "duration_target_s": 3.5},
        {"beat": "stakes", "purpose": "Cost of untreated H2S", "duration_target_s": 6.0},
        {"beat": "mechanism", "purpose": "How CaN oxidises sulfide", "duration_target_s": 10.0},
        {"beat": "proof", "purpose": "Hunter Valley case study", "duration_target_s": 10.0},
        {"beat": "cta", "purpose": "HRSU spec sheet CTA", "duration_target_s": 5.0},
    ],
    "supporting_facts": [
        {"value": "50", "unit": "mg/L", "claim": "WHO drinking-water nitrate limit"}
    ],
}


def test_strategist_populates_hero_arc_supporting():
    facts = [{"value": "90", "unit": "%", "claim": "..."},
             {"value": "50", "unit": "mg/L", "claim": "..."}]
    blog = {"id": "b", "url": "u", "title": "Lime Neutralization",
            "region": "australia", "category": "mining", "persona": "procurement"}
    sb = Storyboard(version="2.0", blog=blog)
    with patch("video_agent.agents.strategist.OllamaClient") as mock_cls:
        mock_cls.return_value.generate_json.return_value = _FAKE_OLLAMA
        Strategist().run(sb, facts, "<html>full blog text</html>")
    assert sb.hero_claim.stat == "90%"
    assert len(sb.arc) == 5
    assert [b.beat for b in sb.arc] == ["hook", "stakes", "mechanism", "proof", "cta"]
    assert len(sb.supporting_facts) == 1
