from unittest.mock import patch, MagicMock
from video_agent.agents.reviser import Reviser
from video_agent.storyboard import (
    Storyboard, HeroClaim, Beat, Scene, VisualConcept, CriticNotes, AssetCandidate,
)


def test_reviser_rewrites_only_flagged_scenes():
    sb = Storyboard(version="2.0",
                    blog={"id": "b", "url": "u", "title": "t",
                          "region": "australia", "category": "mining",
                          "persona": "procurement"},
                    hero_claim=HeroClaim(stat="90%", claim_text="x"),
                    arc=[Beat(index=0, beat="hook", purpose="",
                              duration_target_s=4.0)])
    good_scene = Scene(index=0, beat="hook", narration="good",
                       on_screen_text="GOOD", visual_concept=VisualConcept(
                           subject="x", modifier="", type="photo",
                           mood="problem", style_hint=""),
                       duration_target_s=4.0, transition_in="cut",
                       critic_notes=CriticNotes(alignment_score=9, flags=[]))
    bad_scene = Scene(index=1, beat="stakes", narration="bad",
                      on_screen_text="bad text",
                      visual_concept=VisualConcept(
                          subject="x", modifier="", type="photo",
                          mood="problem", style_hint=""),
                      duration_target_s=4.0, transition_in="cut",
                      critic_notes=CriticNotes(
                          alignment_score=4,
                          flags=["text_duplicates_voice"],
                          revision="Use the hero stat instead"))
    sb.scenes = [good_scene, bad_scene]
    fake_response = {"on_screen_text": "$5K/MO PIPE LOSS"}
    fake_sourcer = MagicMock()
    with patch("video_agent.agents.reviser.OllamaClient") as M:
        M.return_value.generate_json.return_value = fake_response
        Reviser(sourcer=fake_sourcer).run(sb)
    assert sb.scenes[0].on_screen_text == "GOOD"           # untouched
    assert sb.scenes[1].on_screen_text == "$5K/MO PIPE LOSS"
    fake_sourcer._source_scene.assert_not_called()                   # vis didn't change


def _make_mismatch_scene(idx=0, include_mismatch=True):
    flags = ["voice_visual_mismatch"] if include_mismatch else []
    s = Scene(
        index=idx, beat="proof", duration_target_s=5.0,
        narration="Test narration",
        on_screen_text="Test text",
        visual_concept=VisualConcept(subject="x", modifier="y",
                                     type="photo", mood="proof", style_hint=""),
        asset_candidates=[],
        critic_notes=CriticNotes(
            flags=flags, alignment_score=3, revision="visual mismatch detected"
        ),
    )
    s.chosen_asset = AssetCandidate(
        source="old_source", url="https://old.example/img.jpg", score=50,
        local_path="/tmp/old.jpg", caption="", width=1920, height=1080,
    )
    return s


def test_reviser_resources_on_voice_visual_mismatch():
    scene = _make_mismatch_scene()
    sb = Storyboard(version="2.0", blog={"category": "mining"}, scenes=[scene])

    def fake_re_source(scn, cat, exclude_urls, thread_keywords=None, hero_claim=""):
        scn.chosen_asset = AssetCandidate(
            source="new_source", url="https://new.example/img.jpg", score=80,
            local_path="/tmp/new.jpg", caption="", width=1920, height=1080,
        )

    mock_sourcer = MagicMock()
    mock_sourcer.re_source_scene.side_effect = fake_re_source

    with patch("video_agent.agents.reviser.OllamaClient"):
        Reviser(sourcer=mock_sourcer).run(sb)
    assert scene.chosen_asset.source == "new_source"
    assert mock_sourcer.re_source_scene.called


def test_reviser_no_resource_without_flag():
    scene = _make_mismatch_scene(include_mismatch=False)
    scene.critic_notes = CriticNotes(flags=[], alignment_score=3, revision="weak only")
    sb = Storyboard(version="2.0", blog={"category": "mining"}, scenes=[scene])
    mock_sourcer = MagicMock()
    with patch("video_agent.agents.reviser.OllamaClient"):
        Reviser(sourcer=mock_sourcer).run(sb)
    assert not mock_sourcer.re_source_scene.called
