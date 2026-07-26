from unittest.mock import patch, MagicMock
from pathlib import Path
from video_agent.agents.sourcer import Sourcer
from video_agent.sources.base import RawCandidate
from video_agent.storyboard import (
    Storyboard, Scene, VisualConcept, HeroClaim, Beat,
)


def _scene(idx, subject="industrial water"):
    return Scene(
        index=idx, beat="hook",
        narration="industrial water treatment plant aerial view",
        on_screen_text="Industrial Water",
        visual_concept=VisualConcept(subject=subject, modifier="aerial",
                                     type="photo", mood="problem",
                                     style_hint=""),
        duration_target_s=4.0, transition_in="cut",
    )


def _sb(scenes):
    return Storyboard(version="2.0",
                      blog={"id": "b", "url": "u", "title": "t",
                            "region": "australia", "category": "mining",
                            "persona": "procurement"},
                      hero_claim=HeroClaim(stat="90%", claim_text="x"),
                      arc=[Beat(index=i, beat="hook", purpose="x",
                                duration_target_s=4.0) for i in range(len(scenes))],
                      scenes=scenes)


def test_sourcer_picks_best_candidate(tmp_path):
    cands = [
        RawCandidate(source="unsplash", url="https://u/good.jpg",
                     caption="industrial water plant aerial",
                     width=1920, height=1080, file_size=120_000),
        RawCandidate(source="google_images", url="https://g/bad.jpg",
                     caption="cat", width=1920, height=1080, file_size=60_000),
    ]
    fake_src = MagicMock()
    fake_src.name = "unsplash"
    fake_src.search.return_value = cands
    with patch("video_agent.agents.sourcer.Sourcer._download_candidate",
               return_value=tmp_path / "downloaded.jpg") as mock_dl, \
         patch("video_agent.agents.sourcer.Sourcer._is_dup", return_value=False):
        # write a dummy file so the path exists
        (tmp_path / "downloaded.jpg").write_bytes(b"\xff\xd8\xff\xd9")
        s = _scene(0)
        sb = _sb([s])
        Sourcer(sources=[fake_src], cache_root=tmp_path / "cache",
                download_root=tmp_path / "dl").run(sb)
    assert sb.scenes[0].chosen_asset is not None
    assert sb.scenes[0].chosen_asset.source == "unsplash"
    assert sb.scenes[0].degraded is False


def test_sourcer_marks_degraded_when_no_candidate(tmp_path):
    fake_src = MagicMock()
    fake_src.name = "unsplash"
    fake_src.search.return_value = []
    s = _scene(0)
    sb = _sb([s])
    Sourcer(sources=[fake_src], cache_root=tmp_path / "cache",
            download_root=tmp_path / "dl").run(sb)
    assert sb.scenes[0].chosen_asset is None
    assert sb.scenes[0].degraded is True


def test_sourcer_builds_narrative_thread(monkeypatch):
    """Sourcer.run() calls _build_narrative_thread and stores result on storyboard."""
    from video_agent.storyboard import (
        Storyboard, Scene, VisualConcept, HeroClaim,
    )
    from video_agent.agents.sourcer import Sourcer
    from pathlib import Path

    sb = Storyboard(version="2.2", blog={"category": "wastewater_treatment"})
    sb.hero_claim = HeroClaim(stat="40%", claim_text="cuts H2S 40%")
    vc = VisualConcept(subject="x", modifier="", type="photo", mood="problem")
    sb.scenes = [
        Scene(index=0, beat="hook", narration="pipe scaling problem",
              on_screen_text="", visual_concept=vc, duration_target_s=3),
        Scene(index=1, beat="proof", narration="calcium ions raise pH",
              on_screen_text="", visual_concept=vc, duration_target_s=3),
    ]

    src = Sourcer(sources=[], cache_root=Path("/tmp/c"),
                  download_root=Path("/tmp/d"))
    monkeypatch.setattr(src, "_source_scene", lambda *a, **k: None)
    monkeypatch.setattr(src, "_build_narrative_thread",
                        lambda sb_: [["pipe", "scaling"], ["calcium", "ph"]])
    src.run(sb)
    assert sb.narrative_thread == [["pipe", "scaling"], ["calcium", "ph"]]


def test_source_scene_passes_thread_to_context_match(monkeypatch):
    """When the storyboard has a narrative_thread, _source_scene threads it
    into the context_match_score call for the current scene."""
    from video_agent.storyboard import (
        Storyboard, Scene, VisualConcept, HeroClaim,
    )
    from video_agent.sources.base import RawCandidate
    from video_agent.agents.sourcer import Sourcer
    from pathlib import Path

    sb = Storyboard(version="2.2", blog={"category": "wastewater_treatment"})
    sb.hero_claim = HeroClaim(stat="40%", claim_text="cuts H2S 40%")
    vc = VisualConcept(subject="industrial pipe", modifier="scaling",
                       type="photo", mood="problem")
    sb.scenes = [
        Scene(index=0, beat="hook", narration="calcium ions raise pH",
              on_screen_text="", visual_concept=vc, duration_target_s=3),
    ]
    sb.narrative_thread = [["pipe", "scaling", "equipment"]]

    src = Sourcer(sources=[], cache_root=Path("/tmp/c"),
                  download_root=Path("/tmp/d"))
    monkeypatch.setattr(src, "_search_all_sources", lambda q: [
        RawCandidate(source="bing", url="http://a", caption="pipe scaling buildup",
                     width=1600, height=900),
        RawCandidate(source="bing", url="http://b", caption="river algae bloom",
                     width=1600, height=900),
    ])
    captured = []
    import video_agent.agents.sourcer as src_mod
    real_cms = src_mod.context_match_score
    def spy_cms(caption, narration, thread_keywords=None, hero_claim=None):
        captured.append((caption, thread_keywords))
        return real_cms(caption, narration, thread_keywords=thread_keywords,
                        hero_claim=hero_claim)
    monkeypatch.setattr(src_mod, "context_match_score", spy_cms)
    monkeypatch.setattr(src, "_download_candidate", lambda c, i: None)
    src._source_scene(sb.scenes[0], blog_category="wastewater_treatment",
                      narrative_thread=sb.narrative_thread,
                      hero_claim=sb.hero_claim.claim_text)
    assert all(t == ["pipe", "scaling", "equipment"] for _, t in captured)
