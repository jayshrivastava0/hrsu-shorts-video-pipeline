from __future__ import annotations
from pathlib import Path


FACTSHEET = {"facts": [
    {"id": "f1", "verbatim_quote": "dosage range of 1.5 to 3 kg per cubic meter",
     "value": "1.5 to 3", "unit": "kg/m3", "claim_summary": "dosing window",
     "citation_marker": 1},
    {"id": "f2", "verbatim_quote": "no citation here", "citation_marker": None},
]}
POST = {"citations": [{"marker": 1, "url": "https://example.com/paper.pdf"}]}


def _judge_result(score, reject_reason=None, focal_hint="center"):
    return {"accepted_score": score, "description": "d",
            "focal_hint": focal_hint, "reject_reason": reject_reason}


class TestAcquireFigure:
    def test_torture_mode_short_circuits(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import figure_acquisition as fa
        result = fa.acquire_figure("f1", FACTSHEET, POST, tmp_path, torture=True)
        assert result["image_path"] is None
        assert result["provenance"]["reason"] == "torture_mode"

    def test_unknown_fact_id(self, tmp_path):
        from shorts_engine.sourcing import figure_acquisition as fa
        result = fa.acquire_figure("nope", FACTSHEET, POST, tmp_path)
        assert result["image_path"] is None
        assert result["provenance"]["reason"] == "unknown_fact_id"

    def test_fact_with_no_citation(self, tmp_path):
        from shorts_engine.sourcing import figure_acquisition as fa
        result = fa.acquire_figure("f2", FACTSHEET, POST, tmp_path)
        assert result["image_path"] is None
        assert result["provenance"]["reason"] == "no_citation"

    def test_no_candidates_fetched(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import figure_acquisition as fa
        monkeypatch.setattr(fa, "_fetch_candidates", lambda url, ws, t: [])
        result = fa.acquire_figure("f1", FACTSHEET, POST, tmp_path)
        assert result["image_path"] is None
        assert result["provenance"]["reason"] == "no_pages_fetched"

    def test_accepts_first_candidate_over_threshold(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import figure_acquisition as fa
        p1, p2 = tmp_path / "p0.png", tmp_path / "p1.png"
        p1.touch(); p2.touch()
        monkeypatch.setattr(fa, "_fetch_candidates", lambda url, ws, t: [p1, p2])
        judged = []
        def fake_judge(path, wish, narration_span):
            judged.append(path)
            return _judge_result(8 if path == p1 else 0)
        monkeypatch.setattr(fa, "_judge", fake_judge)
        result = fa.acquire_figure("f1", FACTSHEET, POST, tmp_path)
        assert result["image_path"] == str(p1)
        assert judged == [p1]  # stops at first acceptance

    def test_below_threshold_candidates_all_rejected(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import figure_acquisition as fa
        p1 = tmp_path / "p0.png"; p1.touch()
        monkeypatch.setattr(fa, "_fetch_candidates", lambda url, ws, t: [p1])
        monkeypatch.setattr(fa, "_judge", lambda path, wish, span: _judge_result(3))
        result = fa.acquire_figure("f1", FACTSHEET, POST, tmp_path)
        assert result["image_path"] is None
        assert result["provenance"]["reason"] == "no_acceptance"
        assert result["provenance"]["candidates"][0]["score"] == 3

    def test_reject_reason_blocks_acceptance_even_at_high_score(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import figure_acquisition as fa
        p1 = tmp_path / "p0.png"; p1.touch()
        monkeypatch.setattr(fa, "_fetch_candidates", lambda url, ws, t: [p1])
        monkeypatch.setattr(fa, "_judge",
                            lambda path, wish, span: _judge_result(9, reject_reason="describe_failed"))
        result = fa.acquire_figure("f1", FACTSHEET, POST, tmp_path)
        assert result["image_path"] is None
        assert result["provenance"]["reason"] == "no_acceptance"
