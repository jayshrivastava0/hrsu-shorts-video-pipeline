from unittest.mock import patch, MagicMock
from pathlib import Path
from video_agent.orchestrator import build_storyboard
from video_agent.storyboard import Storyboard, HeroClaim, Beat, Scene, VisualConcept


def test_orchestrator_calls_each_stage_in_order(tmp_path):
    blog = {"id": "b", "url": "u", "title": "t", "region": "australia",
            "category": "mining", "persona": "procurement"}
    facts = [{"value": "90", "unit": "%", "claim": "..."}]

    def _fake_strategist_run(sb, facts, html):
        sb.hero_claim = HeroClaim(stat="90%", claim_text="x")
        sb.arc = [Beat(index=i, beat=b, purpose="", duration_target_s=4.0)
                  for i, b in enumerate(["hook", "stakes", "mechanism",
                                          "proof", "cta"])]
        return sb

    def _fake_sb_run(sb):
        sb.scenes = [Scene(index=i, beat=b.beat, narration="",
                           on_screen_text="",
                           visual_concept=VisualConcept(
                               subject="x", modifier="", type="photo",
                               mood="problem", style_hint=""),
                           duration_target_s=b.duration_target_s,
                           transition_in="cut")
                     for i, b in enumerate(sb.arc)]
        return sb

    fake_sourcer = MagicMock()
    fake_sourcer.run.side_effect = lambda sb: sb

    with patch("video_agent.orchestrator.Strategist") as MStrat, \
         patch("video_agent.orchestrator.Storyboarder") as MSB, \
         patch("video_agent.orchestrator._build_sourcer", return_value=fake_sourcer), \
         patch("video_agent.orchestrator.LocalCritic") as MLocalCritic, \
         patch("video_agent.orchestrator.GlobalDirector") as MGlobalDirector, \
         patch("video_agent.orchestrator.Reviser") as MReviser:
        MStrat.return_value.run.side_effect = _fake_strategist_run
        MSB.return_value.run.side_effect = _fake_sb_run
        MLocalCritic.return_value.run.side_effect = lambda sb: sb
        MGlobalDirector.return_value.run.side_effect = lambda sb: sb
        MReviser.return_value.run.side_effect = lambda sb: sb
        sb = build_storyboard(blog, facts, "<html/>", workspace=tmp_path)
    assert sb.hero_claim.stat == "90%"
    assert len(sb.scenes) == 5
    fake_sourcer.run.assert_called()  # Called twice now (sourcer + reviser's re-sourcer)


def test_orchestrator_runs_cinematographer_after_storyboarder(monkeypatch, tmp_path):
    """Cinematographer must run between Storyboarder and NarrationPolisher."""
    from video_agent import orchestrator
    calls = []

    class _Spy:
        name = "unset"
        def run(self, sb, *a, **k):
            calls.append(self.name)
            return sb

    def _make(name):
        s = _Spy(); s.name = name; return s

    monkeypatch.setattr(orchestrator, "Strategist",
                        lambda: _make("strategist"))
    monkeypatch.setattr(orchestrator, "Storyboarder",
                        lambda: _make("storyboarder"))
    monkeypatch.setattr(orchestrator, "Cinematographer",
                        lambda: _make("cinematographer"))
    monkeypatch.setattr(orchestrator, "NarrationPolisher",
                        lambda: _make("polisher"))
    monkeypatch.setattr(orchestrator, "_build_sourcer",
                        lambda *a, **k: _make("sourcer"))
    monkeypatch.setattr(orchestrator, "LocalCritic",
                        lambda: _make("local_critic"))
    monkeypatch.setattr(orchestrator, "GlobalDirector",
                        lambda: _make("global_director"))

    class _RevSpy:
        def __init__(self, **k): pass
        def run(self, sb): calls.append("reviser"); return sb

    monkeypatch.setattr(orchestrator, "Reviser", _RevSpy)

    orchestrator.build_storyboard(
        blog={"region": "usa"}, facts=[], blog_html="",
        workspace=tmp_path,
    )
    assert calls.index("cinematographer") > calls.index("storyboarder")
    assert calls.index("cinematographer") < calls.index("polisher")
