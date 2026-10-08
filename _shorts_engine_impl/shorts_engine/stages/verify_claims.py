# shorts_engine/stages/verify_claims.py
"""VERIFY stage -- explanation_plan.json (planned) to explanation_plan.json (verified).

Every factual claim is checked before it can reach the script:
  blog_stated   -> verbatim quote located in canonical.txt (+ number/unit gate)
  external_fact -> retrieved passages judged by an independent verifier model
  reasoning     -> logic check against already-supported premises
  illustrative  -> no numbers allowed
Failed claims are repaired by the planner (bounded rounds) and otherwise dropped,
never guessed. A step survives only with at least one supported non-illustrative
claim. Empty/failed web search just means the claim is unsupported; it never stops
the run. If fewer than config.MIN_VERIFIED_STEPS steps survive, the plan is saved
with status "held" and HoldForReview is raised.
"""
from __future__ import annotations

import copy
import json
import logging
from pathlib import Path

from shorts_engine import config, explanation
from shorts_engine.errors import HoldForReview
from shorts_engine.llm import text_llm
from shorts_engine.retrieval import Retriever
from shorts_engine.stages.facts import locate_verbatim

logger = logging.getLogger(__name__)

VERDICT_SCHEMA: dict = {
    "type": "object",
    "properties": {"verdict": {"enum": list(explanation.VERDICTS)},
                   "passage_index": {"type": ["integer", "null"]}},
    "required": ["verdict", "passage_index"],
    "additionalProperties": False,
}

REPAIR_SCHEMA: dict = {
    "type": "object",
    "properties": {"claims": {"type": "array", "items": explanation.CLAIM_ITEM_SCHEMA}},
    "required": ["claims"],
    "additionalProperties": False,
}

_VERIFIER_SYSTEM = (
    "You are a strict fact-checker. You get ONE claim and numbered source passages. Answer "
    "'supported' only if the passages themselves state or directly entail the claim, "
    "'contradicted' if they state the opposite, 'unsupported' if they do not address it. Do "
    "not use outside knowledge. passage_index is the passage you relied on (null if none)."
)
_LOGIC_SYSTEM = (
    "You are a strict logic checker. You get verified premises and ONE conclusion. Answer "
    "'supported' only if the conclusion follows from the premises alone, 'contradicted' if "
    "the premises imply the opposite, otherwise 'unsupported'. Set passage_index to null."
)
_REPAIR_SYSTEM = (
    "You repair one step of an explainer plan. Some of its claims failed verification. "
    "Return replacement claims (same schema) that convey the step using only facts you are "
    "confident a reference source or the article states; drop any number you cannot support "
    "with its unit. Return an empty list if the step cannot be supported honestly. "
    "blog_stated claims must copy a verbatim sentence from the article into `quote`."
)


def _result(claim: dict, verdict: str, reason: str, support: dict | None = None) -> dict:
    out = copy.deepcopy(claim)
    out["verdict"] = verdict
    out["reason"] = reason
    if support is not None:
        out["support"] = support
    return out


def verify_claim(claim: dict, *, step: dict, premises: list[str], canonical: str,
                 retriever, local_only: bool = False) -> dict:
    """Return a copy of `claim` with `verdict`, `reason` and (if sourced) `support` set."""
    kind, text, terms = claim["kind"], claim["text"], step.get("terms", [])

    if kind == "blog_stated":
        quote = claim["support"]["quote"]
        if not quote or locate_verbatim(quote, canonical) is None:
            return _result(claim, "unsupported", "quote not found verbatim in the article")
        errs = explanation.gate_claim_numbers(text, quote, terms)
        if errs:
            return _result(claim, "unsupported", "; ".join(errs))
        return _result(claim, "supported", "verbatim quote found")

    if kind == "illustrative":
        if explanation.has_number(text):
            return _result(claim, "unsupported", "illustrative claims must not contain numbers")
        return _result(claim, "supported", "illustrative, no numbers")

    if kind == "external_fact":
        passages = retriever.retrieve(text)
        if not passages:
            return _result(claim, "unsupported", "no_retrieval")
        listing = "\n".join(f"[{i}] ({p.url}) {p.text}" for i, p in enumerate(passages))
        res = text_llm.generate_schema_json(
            f"Claim: {text}\n\nPassages:\n{listing}", _VERIFIER_SYSTEM, VERDICT_SCHEMA,
            local_only=local_only, role="verifier")
        if res["verdict"] != "supported":
            return _result(claim, res["verdict"], "verifier: not supported by retrieved passages")
        idx = res.get("passage_index")
        if not isinstance(idx, int) or not 0 <= idx < len(passages):
            return _result(claim, "unsupported", "verifier cited no valid passage")
        passage = passages[idx]
        errs = explanation.gate_claim_numbers(text, passage.text, terms)
        if errs:
            return _result(claim, "unsupported", "; ".join(errs))
        return _result(claim, "supported", "supported by retrieved passage",
                       {"type": "source", "quote": passage.text, "url": passage.url})

    if kind == "reasoning":
        if not premises:
            return _result(claim, "unsupported", "no verified premises to reason from")
        listing = "\n".join(f"- {p}" for p in premises)
        res = text_llm.generate_schema_json(
            f"Premises:\n{listing}\n\nConclusion: {text}", _LOGIC_SYSTEM, VERDICT_SCHEMA,
            local_only=local_only, role="verifier")
        if res["verdict"] != "supported":
            return _result(claim, res["verdict"], "does not follow from the verified premises")
        errs = explanation.gate_claim_numbers(text, " ".join(premises), terms)
        if errs:
            return _result(claim, "unsupported", "; ".join(errs))
        return _result(claim, "supported", "follows from verified premises")

    return _result(claim, "unsupported", f"unknown claim kind {kind!r}")


def verify_plan(plan: dict, *, canonical: str, retriever, local_only: bool = False) -> None:
    """Verify every not-yet-verified claim, in chain order (mutates `plan`)."""
    premises: list[str] = []
    for step in plan["steps"]:
        for i, claim in enumerate(step["claims"]):
            if claim.get("verdict") is None:
                step["claims"][i] = verify_claim(
                    claim, step=step, premises=premises, canonical=canonical,
                    retriever=retriever, local_only=local_only)
            if step["claims"][i]["verdict"] == "supported":
                premises.append(step["claims"][i]["text"])


def _repair_step(plan: dict, step: dict, round_no: int, local_only: bool) -> None:
    failed = [c for c in step["claims"] if c["verdict"] != "supported"]
    kept = [c for c in step["claims"] if c["verdict"] == "supported"]
    prompt = (
        f"Question: {plan['question']}\nStep goal: {step['claim_text']}\n"
        "Supported claims (keep as they are):\n"
        + "\n".join(f"- {c['text']}" for c in kept)
        + "\nFailed claims:\n"
        + "\n".join(f"- {c['text']} ({c['kind']}) -> {c['verdict']}: {c.get('reason', '')}"
                    for c in failed))
    res = text_llm.generate_schema_json(prompt, _REPAIR_SYSTEM, REPAIR_SCHEMA,
                                        local_only=local_only, role="planner")
    plan["dropped_claims"].extend(failed)
    new = []
    for n, raw in enumerate(res["claims"], start=1):
        raw = dict(raw, id=f"{step['step_id']}_r{round_no}_{n}")
        new.append(explanation.finalize_claim(raw))
    step["claims"] = kept + new


def _prune(plan: dict) -> None:
    surviving = []
    for step in plan["steps"]:
        ok = [c for c in step["claims"] if c["verdict"] == "supported"]
        plan["dropped_claims"].extend(c for c in step["claims"] if c["verdict"] != "supported")
        step["claims"] = ok
        if any(c["kind"] != "illustrative" for c in ok):
            surviving.append(step)
        else:
            plan["dropped_steps"].append(step)
    plan["steps"] = surviving


def run(ctx) -> dict[str, str]:
    """Run the VERIFY stage; rewrites explanation_plan.json as verified (or held).

    Raises:
        HoldForReview: fewer than config.MIN_VERIFIED_STEPS steps survive verification.
    """
    ws = Path(ctx.workspace)
    path = ws / "explanation_plan.json"
    plan = json.loads(path.read_text(encoding="utf-8"))
    canonical = (ws / "canonical.txt").read_text(encoding="utf-8")
    post = json.loads((ws / "post.json").read_text(encoding="utf-8"))
    local_only = bool(ctx.flags.get("local_only", False))
    retriever = Retriever([c["url"] for c in post.get("citations", []) if c.get("url")])

    verify_plan(plan, canonical=canonical, retriever=retriever, local_only=local_only)
    for round_no in range(1, config.PLAN_MAX_REPAIR_ROUNDS + 1):
        failing = [s for s in plan["steps"]
                   if any(c["verdict"] != "supported" for c in s["claims"])]
        if not failing:
            break
        for step in failing:
            _repair_step(plan, step, round_no, local_only)
        verify_plan(plan, canonical=canonical, retriever=retriever, local_only=local_only)
    _prune(plan)

    n_claims = sum(len(s["claims"]) for s in plan["steps"])
    logger.info(f"verify_claims: {len(plan['steps'])} steps / {n_claims} claims survived, "
                f"{len(plan['dropped_claims'])} claims and {len(plan['dropped_steps'])} steps dropped")
    if len(plan["steps"]) < config.MIN_VERIFIED_STEPS:
        plan["status"] = "held"
        plan["hold_reasons"] = [
            f"only {len(plan['steps'])} verified step(s) survived; need at least "
            f"{config.MIN_VERIFIED_STEPS}"]
        path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
        raise HoldForReview(plan["hold_reasons"])
    plan["status"] = "verified"
    path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"explanation_plan": "explanation_plan.json"}
