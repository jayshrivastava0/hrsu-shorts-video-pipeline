# tests/video_agent/test_composer_v2.py
from pathlib import Path
from PIL import Image
from video_agent.composer import compose_short_v2
from video_agent.storyboard import (
    Storyboard, HeroClaim, Beat, Scene, VisualConcept, AssetCandidate,
)


def _fixture_storyboard(tmp_path) -> Storyboard:
    # Make a valid landscape source image
    src = tmp_path / "src.jpg"
    Image.new("RGB", (1920, 1080), "navy").save(src)

    sb = Storyboard(version="2.0",
                    blog={"id": "b", "url": "u", "title": "t",
                          "region": "atlantis",          # no music = skip
                          "category": "mining",
                          "persona": "procurement"},
                    hero_claim=HeroClaim(stat="90%", claim_text="x"))
    sb.arc = [Beat(index=i, beat=b, purpose="",
                   duration_target_s=2.0)
              for i, b in enumerate(["hook", "stakes", "mechanism",
                                     "proof", "cta"])]
    sb.scenes = []
    for i, beat in enumerate(["hook", "stakes", "mechanism", "proof", "cta"]):
        sb.scenes.append(Scene(
            index=i, beat=beat, narration=f"scene {i}",
            on_screen_text=f"BEAT {i}",
            visual_concept=VisualConcept(subject="x", modifier="",
                                         type="photo", mood=beat,
                                         style_hint=""),
            duration_target_s=2.0, transition_in="cut",
            chosen_asset=AssetCandidate(
                source="test", url="x", score=70,
                local_path=str(src), caption="test",
                width=1920, height=1080),
        ))
    return sb


def test_composer_v2_produces_valid_mp4(tmp_path):
    sb = _fixture_storyboard(tmp_path)
    voice = tmp_path / "voice.mp3"
    # Generate 10s of silence as test audio
    import subprocess
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
                    "-t", "10", str(voice)], check=True)
    srt = tmp_path / "subs.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:10,000\nhello\n",
                   encoding="utf-8")
    out = tmp_path / "video.mp4"
    compose_short_v2(sb, voice_path=voice, subtitle_path=srt,
                     output_path=out, workspace=tmp_path)
    assert out.exists() and out.stat().st_size > 10_000


def test_render_scene_clip_uses_cinematography_palette(monkeypatch, tmp_path):
    """When scene.cinematography is set, apply_grade is called with the palette,
    not the legacy mood."""
    from video_agent.storyboard import (
        Scene, VisualConcept, Cinematography, AssetCandidate,
    )
    from video_agent import composer

    grade_calls = []
    monkeypatch.setattr(composer, "apply_grade",
                        lambda clip, palette: grade_calls.append(palette) or clip)
    out = tmp_path / "scene_clips" / "scene_00.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"\x00" * 100)
    monkeypatch.setattr(composer, "_render_letterbox_image",
                        lambda *a, **k: out)
    monkeypatch.setattr(composer, "render_motion_clip",
                        lambda *a, **k: out)
    monkeypatch.setattr(composer, "_overlay_on_screen_text",
                        lambda clip, scene, fps=30, skip_band=False: clip)
    monkeypatch.setattr(composer, "plan_ken_burns",
                        lambda *a, **k: None)
    img = tmp_path / "fake.jpg"
    from PIL import Image as _Img
    _Img.new("RGB", (1280, 720), "black").save(img)

    scene = Scene(
        index=0, beat="hook", narration="n", on_screen_text="",
        visual_concept=VisualConcept(subject="", modifier="",
                                     type="photo", mood="problem"),
        duration_target_s=3.0,
        cinematography=Cinematography(color_grade="cold_blue"),
        chosen_asset=AssetCandidate(
            source="x", url="x", score=80,
            local_path=str(img), caption="", width=1280, height=720,
        ),
    )
    composer._render_scene_clip(scene, tmp_path, fps=30)
    assert grade_calls == ["cold_blue"]


def test_concat_with_transitions_uses_cinematography(monkeypatch, tmp_path):
    """If scene[i].cinematography.transition_in is set, it overrides the
    legacy transition_between(beat, beat) lookup."""
    from video_agent.storyboard import Scene, VisualConcept, Cinematography
    from video_agent import composer

    c0 = tmp_path / "scene_clips" / "scene_00.mp4"
    c1 = tmp_path / "scene_clips" / "scene_01.mp4"
    c0.parent.mkdir(parents=True, exist_ok=True)
    c0.write_bytes(b"\x00"); c1.write_bytes(b"\x00")

    vc = VisualConcept(subject="", modifier="", type="photo", mood="problem")
    s0 = Scene(index=0, beat="hook", narration="", on_screen_text="",
               visual_concept=vc, duration_target_s=2.0)
    s1 = Scene(index=1, beat="proof", narration="", on_screen_text="",
               visual_concept=vc, duration_target_s=2.0,
               cinematography=Cinematography(transition_in="whip_pan"))

    captured = []
    def fake_run(cmd, *a, **k):
        if "-filter_complex" in cmd:
            captured.append(cmd[cmd.index("-filter_complex") + 1])
        out_path = cmd[-1]
        from pathlib import Path as _P
        _P(out_path).write_bytes(b"\x00")
        class _R:
            returncode = 0
            stdout = "2.0"  # Return duration string for ffprobe
            stderr = ""
        return _R()
    monkeypatch.setattr(composer.subprocess, "run", fake_run)

    composer._concat_with_transitions([c0, c1], [s0, s1], tmp_path)
    assert captured, "ffmpeg was never invoked"
    # whip_pan is a legacy value — it now falls back to a safe fade (0.25s)
    assert "transition=fade" in captured[0]
    assert "0.25" in captured[0]


def test_compose_short_v2_appends_silent_stinger(monkeypatch, tmp_path):
    """End-of-video should use the silent 2s stinger via _append_logo_stinger."""
    from video_agent import composer
    captured = {"stinger_called": False, "outro_cta_voice_called": False}

    monkeypatch.setattr(composer, "render_logo_stinger",
                        lambda **kw: captured.__setitem__("stinger_called", True)
                                       or kw["output_path"].write_bytes(b"\x00"))
    monkeypatch.setattr(composer, "_ffmpeg", lambda cmd: None)
    if hasattr(composer, "_add_cta_voiceover"):
        monkeypatch.setattr(composer, "_add_cta_voiceover",
                            lambda *a, **k: captured.__setitem__("outro_cta_voice_called", True))
    out = tmp_path / "stinger.mp4"
    (tmp_path / "main.mp4").write_bytes(b"\x00")
    composer._append_logo_stinger(
        main_mp4=tmp_path / "main.mp4", output_mp4=out,
    )
    assert captured["stinger_called"] is True
    assert captured["outro_cta_voice_called"] is False


def test_render_scene_clip_cta_uses_brand_card(monkeypatch, tmp_path):
    """When a scene's beat is 'cta', _render_scene_clip uses render_cta_scene."""
    from video_agent.storyboard import Scene, VisualConcept, Cinematography
    from video_agent import composer

    # Create a fake logo so Path(BRAND_LOGO_PATH).exists() is True
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"\x00")
    monkeypatch.setattr(composer, "BRAND_LOGO_PATH", str(logo))

    called = {"cta": False}

    def fake_render(**kw):
        called["cta"] = True
        kw["output_path"].write_bytes(b"\x00")
        return kw["output_path"]

    monkeypatch.setattr(composer, "render_cta_scene", fake_render)
    monkeypatch.setattr(composer, "_overlay_on_screen_text",
                        lambda c, s, fps=30, skip_band=False: c)
    monkeypatch.setattr(composer, "apply_grade", lambda c, p: c)

    scene = Scene(
        index=4, beat="cta", narration="visit hrsuindore",
        on_screen_text="", visual_concept=VisualConcept(
            subject="", modifier="", type="photo", mood="brand"),
        duration_target_s=4.0,
        cinematography=Cinematography(color_grade="warm_brand"),
    )
    composer._render_scene_clip(scene, tmp_path, fps=30)
    assert called["cta"] is True


def test_quality_report_includes_cinematography_and_thread(tmp_path):
    from video_agent.storyboard import (
        Storyboard, Scene, VisualConcept, Cinematography,
    )
    from video_agent.composer import _write_quality_report
    import json

    sb = Storyboard(version="2.2", blog={"region": "usa"})
    sb.narrative_thread = [["pipe", "scaling"], ["calcium", "ph"]]
    vc = VisualConcept(subject="", modifier="", type="photo", mood="problem")
    sb.scenes = [
        Scene(index=0, beat="hook", narration="n", on_screen_text="",
              visual_concept=vc, duration_target_s=3,
              cinematography=Cinematography(color_grade="red_tension",
                                             voice_prosody="hook_emphasis")),
        Scene(index=1, beat="proof", narration="n", on_screen_text="",
              visual_concept=vc, duration_target_s=3),
    ]
    _write_quality_report(sb, tmp_path, voice_duration=10.0,
                          video_duration=12.0, outro_concatenated=True)
    report = json.loads((tmp_path / "quality_report.json").read_text())
    assert report["narrative_thread"] == [["pipe", "scaling"], ["calcium", "ph"]]
    assert report["scenes"][0]["cinematography"]["color_grade"] == "red_tension"
    assert report["scenes"][0]["cinematography"]["voice_prosody"] == "hook_emphasis"
    assert report["scenes"][1]["cinematography"] is None
