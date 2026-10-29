"""Script writer for brief-driven videos: beat shape, rewrites, support files."""
import json

import pytest

from scheduler.briefs.facts import Facts
from scheduler.briefs.model import Brief
from scheduler.briefs.research import Source
from scheduler.briefs.types import get_recipe
from scheduler.briefs.writer import WriterFailed, write_script, write_support_files

CTA = "Sourcing calcium nitrate? Visit hrsuindore.com"
MKT_CTA = "Ask for a door quotation at hrsuindore.com"


def _facts(banned=None):
    return Facts(
        in_place=["HRSU supplies calcium nitrate from its Indore plant."],
        in_progress=["Steam reuse in all boilers is planned."],
        never_claimed=[],
        banned_claims=list(banned or []),
        cta_lines=[CTA],
    )


def _sources():
    return [Source(id="S1", title="Dosing study", url="https://www.epa.gov/x",
                   snippet="Calcium nitrate dosing reduces sulfide.", publisher="epa.gov")]


def _brief(type_name="explainer"):
    return Brief(id="b1", text="How does calcium nitrate cut H2S in sewers?", type=type_name)


def _beat(narration, kind="fact", purpose="mechanism", card="Card"):
    return {
        "beat": purpose, "purpose": purpose, "fact_ids": [], "narration": narration,
        "card_text": card, "broll_wish": "Plant footage", "kind": kind,
    }


def _good_beats(cta=CTA):
    return [
        _beat("Sewer H2S corrodes concrete, and a dosing choice decides how fast.", "opinion", "hook"),
        _beat("Calcium nitrate suppresses sulfide production in the sewer biofilm."),
        _beat("HRSU supplies calcium nitrate from its Indore plant."),
        _beat("Dosing is set by the sewer flow and the sulfide load."),
        _beat("Operators should trial a dose before a full-scale change.", "opinion"),
        _beat(f"Plan the next trial with HRSU. {cta}", "opinion", "cta"),
    ]


class FakeLlm:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, system, prompt, schema):
        self.calls.append((system, prompt, schema))
        return self.replies.pop(0)


def test_first_beat_is_hook_and_last_has_cta():
    llm = FakeLlm([{"thesis": "Calcium nitrate suppresses sulfide production in the sewer biofilm.",
                    "beats": _good_beats()}])
    out = write_script(_brief(), get_recipe("explainer"), _facts(), _sources(), llm)

    assert out["beats"][0]["purpose"] == "hook"
    assert out["beats"][-1]["narration"].endswith(CTA)
    assert out["attempts"] == 1
    assert out["rewrites"] == 0
    assert out["critique"] == {}
    assert len(out["beats"]) == 6
    fact_narrations = [b["narration"] for b in out["beats"] if b["kind"] == "fact"]
    assert out["thesis"] in fact_narrations


def test_marketing_story_beats_allowed():
    beats = [
        _beat("Our team starts every shift with a safety walk.", "story", "hook"),
        _beat("We run the plant on calcium nitrate for water treatment.", "fact"),
        _beat("Customers tell us the quoting is quick.", "story"),
        _beat("Our operators keep the dosing lines clean.", "opinion"),
        _beat(f"Tell us your volumes. Ask for a door quotation at hrsuindore.com", "opinion", "cta"),
    ]
    llm = FakeLlm([{"thesis": "We run the plant on calcium nitrate for water treatment.", "beats": beats}])
    out = write_script(_brief("marketing"), get_recipe("marketing"), _facts(), _sources(), llm)

    kinds = {b["kind"] for b in out["beats"]}
    assert {"story", "opinion"} <= kinds
    assert out["thesis"] is None
    assert out["beats"][-1]["narration"].endswith(MKT_CTA) or out["beats"][-1]["narration"].endswith(
        "hrsuindore.com")


def test_marketing_beat_with_banned_claim_rejected_and_rewritten():
    dirty = _good_beats()
    dirty[2] = _beat("HRSU calcium nitrate is certified for every market.")
    clean = _good_beats()
    llm = FakeLlm([{"beats": dirty}, {"beats": clean}])
    out = write_script(_brief("marketing"), get_recipe("marketing"), _facts(banned=["certified"]),
                       _sources(), llm)

    assert out["attempts"] == 2
    assert out["rewrites"] == 1
    assert all("certified" not in b["narration"].lower() for b in out["beats"])
    assert "certified" in llm.calls[1][1]


def test_in_progress_fact_never_in_narration():
    dirty = _good_beats()
    dirty[1] = _beat("Electric pumps now dose the sewer line automatically.")
    llm = FakeLlm([{"beats": dirty}, {"beats": _good_beats()}])
    out = write_script(_brief(), get_recipe("explainer"), _facts(), _sources(), llm)

    assert out["attempts"] == 2
    assert all("electric" not in b["narration"].lower() for b in out["beats"])
    assert "electric" in llm.calls[1][1]


def test_in_progress_keyword_in_card_text_triggers_rewrite():
    dirty = _good_beats()
    dirty[3] = dict(dirty[3], card_text="Steam reuse in all boilers")
    llm = FakeLlm([{"beats": dirty}, {"beats": _good_beats()}])
    out = write_script(_brief(), get_recipe("explainer"), _facts(), _sources(), llm)
    assert out["attempts"] == 2


def test_persistent_violation_raises_after_three_attempts():
    dirty = _good_beats()
    dirty[1] = _beat("Calcium nitrate in all boilers cuts sulfide.")
    llm = FakeLlm([{"beats": dirty}] * 3)
    with pytest.raises(WriterFailed) as exc:
        write_script(_brief(), get_recipe("explainer"), _facts(), _sources(), llm)
    assert len(llm.calls) == 3
    assert "all boilers" in str(exc.value)
    assert "all boilers" in exc.value.phrases


def test_prompt_carries_in_place_facts_and_sources_only():
    llm = FakeLlm([{"beats": _good_beats()}])
    write_script(_brief(), get_recipe("explainer"), _facts(), _sources(), llm)
    system, prompt, schema = llm.calls[0]
    assert "HRSU supplies calcium nitrate from its Indore plant." in prompt
    assert "Steam reuse in all boilers is planned." not in prompt
    assert "Calcium nitrate dosing reduces sulfide." in prompt
    assert "epa.gov" in prompt
    assert CTA in prompt
    assert schema["type"] == "object"


def test_support_files_written(tmp_path):
    llm = FakeLlm([{"beats": _good_beats()}])
    script = write_script(_brief(), get_recipe("explainer"), _facts(), _sources(), llm)
    run = tmp_path / "run"
    write_support_files(run, _brief(), script, _sources())

    s = json.loads((run / "script.json").read_text(encoding="utf-8"))
    assert s["beats"][0]["purpose"] == "hook"

    refs = json.loads((run / "references.json").read_text(encoding="utf-8"))
    assert {"claims", "sources", "post_kind", "chips"} <= refs.keys()
    assert refs["post_kind"] == "explainer"
    assert refs["chips"] == ["epa.gov"]
    assert refs["sources"][0]["url"] == "https://www.epa.gov/x"

    desc = (run / "video_description.txt").read_text(encoding="utf-8")
    assert "Sources" in desc
    assert "epa.gov" in desc

    plan = json.loads((run / "explanation_plan.json").read_text(encoding="utf-8"))
    assert plan == {"question": "How does calcium nitrate cut H2S in sewers?", "post_kind": "explainer"}
