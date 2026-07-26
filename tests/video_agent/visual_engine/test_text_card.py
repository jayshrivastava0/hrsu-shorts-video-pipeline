from pathlib import Path
from PIL import Image
from video_agent.visual_engine.text_card import render_text_card


def test_hook_card_resolution(tmp_path):
    out = tmp_path / "hook.png"
    render_text_card(out, layout="hook", text="H₂S CORROSION")
    assert Image.open(out).size == (1080, 1920)


def test_cta_card_resolution(tmp_path):
    out = tmp_path / "cta.png"
    render_text_card(out, layout="cta", text="HRSUINDORE.COM")
    assert Image.open(out).size == (1080, 1920)


def test_long_text_does_not_crash(tmp_path):
    out = tmp_path / "long.png"
    render_text_card(out, layout="hook",
                     text="A VERY LONG TITLE THAT EXCEEDS EIGHT WORDS EASILY")
    assert out.exists()


def test_custom_resolution(tmp_path):
    out = tmp_path / "v.png"
    render_text_card(out, layout="hook", text="X", resolution=(720, 1280))
    assert Image.open(out).size == (720, 1280)
