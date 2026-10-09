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
        "dropped_claims": [{"text": "So y.", "kind": "reasoning", "role": "fact",
                            "verdict": "unsupported",
                            "reason": "does not follow from the verified premises"}],
        "dropped_steps": [{"step_id": "s2", "reason": "depends on dropped claims"},
                          {"step_id": "s3"}],
    }
    out = format_plan(plan)
    assert "[mechanism] [supported] Ions attack the film." in out
    assert "[fact] [unsupported] So y. -- does not follow from the verified premises" in out
    assert "DROPPED step s2 -- depends on dropped claims" in out
    assert "DROPPED step s3" in out and "DROPPED step s3 --" not in out
    out.encode("ascii")  # report scaffolding is ASCII-safe


def test_report_shows_depends_on_and_transition_per_step():
    plan = {"question": "Q?", "status": "verified", "steps": [
        {"step_id": "s1", "depends_on": [], "transition": "", "claims": [
            {"id": "m1", "text": "Salt draws water.", "role": "mechanism", "verdict": "supported"}]},
        {"step_id": "s2", "depends_on": ["m1", "f1"],
         "transition": "With the surface damp, the next question is the dust.",
         "claims": [{"id": "m2", "text": "Damp dust binds.", "role": "mechanism",
                     "verdict": "supported"}]}]}
    out = format_plan(plan)
    s2 = out.split("Step s2", 1)[1]
    assert "builds on: m1, f1" in s2
    assert "transition: With the surface damp, the next question is the dust." in s2
    s1 = out.split("Step s1", 1)[1].split("Step s2", 1)[0]
    assert "builds on: (the question)" in s1
    assert "transition:" not in s1          # an empty transition is not printed
    out.encode("ascii")


def test_report_tolerates_steps_without_depends_on_or_transition():
    out = format_plan({"question": "Q?", "steps": [{"step_id": "s1", "claims": []},
                                                   {"step_id": "s2", "depends_on": None,
                                                    "transition": None, "claims": []}]})
    assert "Step s1" in out and "Step s2" in out
