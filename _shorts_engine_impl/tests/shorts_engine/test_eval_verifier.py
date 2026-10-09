# tests/shorts_engine/test_eval_verifier.py
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("eval_verifier", ROOT / "scripts" / "eval_verifier.py")
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)


def test_fixture_has_balanced_classes():
    cases = json.loads((Path(__file__).parent / "fixtures" / "claims_eval.json").read_text(encoding="utf-8"))
    for label in ("supported", "contradicted", "unsupported"):
        assert sum(c["expected"] == label for c in cases) >= 6
    assert all(c["passages"] for c in cases)


def test_score_model_reports_per_class_accuracy_and_false_support_rate():
    cases = [{"claim": "a", "passages": ["p"], "expected": "supported"},
             {"claim": "b", "passages": ["p"], "expected": "contradicted"},
             {"claim": "c", "passages": ["p"], "expected": "unsupported"},
             {"claim": "d", "passages": ["p"], "expected": "unsupported"}]
    answers = {"a": "supported", "b": "supported", "c": "unsupported", "d": "supported"}
    res = ev.score_model(cases, lambda claim, passages: answers[claim])
    assert res["per_class"]["supported"] == 1.0
    assert res["per_class"]["contradicted"] == 0.0
    assert res["per_class"]["unsupported"] == 0.5
    # false support = said "supported" when the truth was contradicted/unsupported
    assert res["false_support_rate"] == 2 / 3
