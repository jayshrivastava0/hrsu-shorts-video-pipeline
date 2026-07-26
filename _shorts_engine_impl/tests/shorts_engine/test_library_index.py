from __future__ import annotations
import json
from pathlib import Path
from PIL import Image


def _setup_lib(tmp_path, monkeypatch):
    from shorts_engine.sourcing import library_index as li
    lib = tmp_path / "asset_library"
    (lib / "factory").mkdir(parents=True)
    (lib / "footage").mkdir(parents=True)
    Image.new("RGB", (1600, 900), (50, 50, 50)).save(lib / "factory" / "plant.jpg")
    Image.new("RGB", (1600, 900), (90, 90, 90)).save(lib / "footage" / "tanks.png")
    monkeypatch.setattr(li, "_library_root", lambda: lib)
    return lib


class TestBuildIndex:
    def test_indexes_new_files_and_persists(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import library_index as li
        lib = _setup_lib(tmp_path, monkeypatch)
        descs = {
            "plant.jpg": {"description": "wide shot of the HRSU calcium nitrate "
                          "production floor with bagging line and granulation "
                          "equipment, workers in safety gear visible throughout",
                          "visible_text": "", "quality_notes": "sharp"},
            "tanks.png": {"description": "circular clarifier tanks at a municipal "
                          "wastewater treatment plant seen from a walkway with "
                          "railings and aeration equipment in operation",
                          "visible_text": "", "quality_notes": "sharp"},
        }
        monkeypatch.setattr(li, "_describe", lambda p: descs[Path(p).name])
        idx = li.build_index()
        assert len(idx) == 2
        assert json.loads(li.index_path().read_text(encoding="utf-8")) == idx

    def test_incremental_skips_already_indexed(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import library_index as li
        _setup_lib(tmp_path, monkeypatch)
        calls = []
        monkeypatch.setattr(li, "_describe", lambda p: (calls.append(p) or {
            "description": "x" * 130, "visible_text": "", "quality_notes": ""}))
        li.build_index()
        assert len(calls) == 2
        li.build_index()          # second run: nothing new
        assert len(calls) == 2

    def test_describe_failure_recorded_not_fatal(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import library_index as li
        _setup_lib(tmp_path, monkeypatch)
        monkeypatch.setattr(li, "_describe", lambda p: None)
        idx = li.build_index()
        assert all(v.get("failed") for v in idx.values())


class TestQuery:
    def test_query_ranks_by_token_overlap(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import library_index as li
        _setup_lib(tmp_path, monkeypatch)
        idx = {
            "factory/plant.jpg": {"description": "calcium nitrate production floor bagging line"},
            "footage/tanks.png": {"description": "clarifier tanks wastewater treatment plant walkway"},
        }
        monkeypatch.setattr(li, "_load_index", lambda: idx)
        out = li.query("wastewater treatment clarifier tanks")
        assert out and Path(out[0]["path"]).name == "tanks.png"
        assert out[0]["score_hint"] >= 3

    def test_query_no_overlap_returns_empty(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import library_index as li
        _setup_lib(tmp_path, monkeypatch)
        monkeypatch.setattr(li, "_load_index", lambda: {
            "factory/plant.jpg": {"description": "bagging line equipment"}})
        assert li.query("ocean sunset beach") == []
