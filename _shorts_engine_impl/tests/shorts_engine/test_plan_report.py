from shorts_engine.review.plan_report import format_plan


def test_report_lists_question_steps_claims_verdicts_and_drops():
    plan = {
        "question": "Why model sand?", "status": "held", "hold_reasons": ["only 2 verified steps"],
        "steps": [{"step_id": "s1", "claim_text": "goal", "terms": [], "claims": [
            {"id": "c1", "text": "Sand drains.", "kind": "external_fact", "verdict": "supported",
             "support": {"type": "source", "quote": "q", "url": "https://a.gov"}}]}],
        "dropped_claims": [{"id": "cx", "text": "Bad claim.", "kind": "external_fact",
                            "verdict": "unsupported", "reason": "no_retrieval"}],
        "dropped_steps": [], "payoff": {"takeaway": "t", "differentiator_id": "b_purity"},
    }
    out = format_plan(plan)
    assert "Why model sand?" in out and "HELD" in out and "only 2 verified steps" in out
    assert "s1" in out and "[supported] Sand drains." in out and "https://a.gov" in out
    assert "DROPPED" in out and "no_retrieval" in out


def test_held_plan_with_empty_steps():
    out = format_plan({"question": "Q?", "status": "held", "hold_reasons": ["too few"], "steps": []})
    assert "Q?" in out and "HELD: too few" in out


def test_null_status_and_null_hold_reasons():
    out = format_plan({"question": "Q?", "status": None, "hold_reasons": None})
    assert "Status: " in out


def test_claim_without_support_or_kind():
    out = format_plan({"question": "Q?", "steps": [{"claims": [{"text": "T", "verdict": "x"}]}],
                       "dropped_steps": [{}]})
    assert "[x] T" in out and "Step ?" in out and "DROPPED step ?" in out
