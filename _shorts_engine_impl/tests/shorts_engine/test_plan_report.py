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


def test_report_shows_claim_roles_and_drop_reasons():
    plan = {
        "question": "Q?", "status": "held",
        "steps": [{"step_id": "s1", "claims": [
            {"text": "Ions attack the film.", "kind": "external_fact", "role": "mechanism",
             "verdict": "supported"}]}],
        "dropped_claims": [{"text": "Because x, y.", "kind": "reasoning", "role": "link",
                            "verdict": "unsupported",
                            "reason": "does not follow from the verified premises"}],
        "dropped_steps": [{"step_id": "s2", "reason": "no verified link"}, {"step_id": "s3"}],
    }
    out = format_plan(plan)
    assert "[mechanism] [supported] Ions attack the film." in out
    assert "[link] [unsupported] Because x, y. -- does not follow from the verified premises" in out
    assert "DROPPED step s2 -- no verified link" in out
    assert "DROPPED step s3" in out and "DROPPED step s3 --" not in out
    out.encode("ascii")  # report scaffolding is ASCII-safe
