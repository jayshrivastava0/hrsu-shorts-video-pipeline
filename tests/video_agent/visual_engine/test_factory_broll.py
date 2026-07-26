import json
from pathlib import Path
from video_agent.visual_engine import factory_broll as fb


def _setup(tmp_path, monkeypatch, manifest, files):
    fdir = tmp_path / "factory"
    fdir.mkdir()
    for name in files:
        (fdir / name).write_bytes(b"\x00\x00\x00\x18ftypmp42")
    (fdir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(fb, "FACTORY_DIR", fdir)
    monkeypatch.setattr(fb, "MANIFEST_PATH", fdir / "manifest.json")
    fb.reset_cache()


def test_returns_none_when_no_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(fb, "FACTORY_DIR", tmp_path / "empty")
    monkeypatch.setattr(fb, "MANIFEST_PATH", tmp_path / "empty" / "manifest.json")
    fb.reset_cache()
    assert fb.find_footage({"narration": "anything"}) is None


def test_picks_clip_by_category_match(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch,
           manifest=[
               {"id": "tank01", "filename": "tank01.mp4",
                "tags": ["tank", "wastewater"],
                "categories": ["wastewater_treatment"],
                "description": "clarifier tanks"},
               {"id": "mine01", "filename": "mine01.mp4",
                "tags": ["mine", "haul"],
                "categories": ["mining"],
                "description": "open pit mine"},
           ],
           files=["tank01.mp4", "mine01.mp4"])
    scene = {"index": 0, "visual_type": "stock",
             "category": "wastewater_treatment",
             "narration": "Calcium nitrate cuts H2S",
             "on_screen_text": "WASTEWATER", "visual_spec": {}}
    match = fb.find_footage(scene)
    assert match is not None
    assert match["id"] == "tank01"


def test_returns_none_below_min_score(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch,
           manifest=[{"id": "x", "filename": "x.mp4",
                      "tags": ["zzz"], "categories": ["unrelated"],
                      "description": "irrelevant"}],
           files=["x.mp4"])
    scene = {"index": 0, "visual_type": "stock",
             "narration": "wastewater treatment",
             "on_screen_text": "", "visual_spec": {}}
    assert fb.find_footage(scene) is None


def test_skips_manifest_entries_with_missing_files(tmp_path, monkeypatch):
    fdir = tmp_path / "factory"
    fdir.mkdir()
    (fdir / "manifest.json").write_text(
        json.dumps([{"id": "ghost", "filename": "ghost.mp4",
                     "tags": ["wastewater"], "categories": ["wastewater_treatment"]}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(fb, "FACTORY_DIR", fdir)
    monkeypatch.setattr(fb, "MANIFEST_PATH", fdir / "manifest.json")
    fb.reset_cache()
    assert fb.find_footage({"narration": "wastewater",
                            "category": "wastewater_treatment",
                            "visual_spec": {}}) is None


def test_deterministic_tiebreak_by_id(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch,
           manifest=[
               {"id": "z_clip", "filename": "z.mp4",
                "tags": ["wastewater"], "categories": ["wastewater_treatment"]},
               {"id": "a_clip", "filename": "a.mp4",
                "tags": ["wastewater"], "categories": ["wastewater_treatment"]},
           ],
           files=["z.mp4", "a.mp4"])
    scene = {"index": 0, "visual_type": "stock",
             "category": "wastewater_treatment",
             "narration": "wastewater plant", "visual_spec": {}}
    match = fb.find_footage(scene)
    assert match["id"] == "a_clip"
