# shorts_engine/stages/verify_claims.py
"""VERIFY stage -- explanation_plan.json (planned) to explanation_plan.json (verified).

Every factual claim is checked before it can reach the script:
  blog_stated   -> verbatim quote located in canonical.txt (+ number/unit gate)
  external_fact -> retrieved passages judged by an independent verifier model
  reasoning     -> logic check against already-supported premises
  illustrative  -> no numbers allowed
A step survives only with a supported non-illustrative `mechanism` claim (its
why/how) and, unless it is the first step, a supported reasoning `link` claim
(how it follows from the previous step). A link is checked against premises from
EARLIER steps only. A failed claim, or a step missing its mechanism/link, is
repaired by the planner (config.PLAN_MAX_REPAIR_ROUNDS rounds) and only then
dropped, never guessed. When a step is dropped, the reasoning claims of later
steps are re-checked against the surviving premises ("re-link"), so no link rests
on a dropped step. Empty/failed web search just means the claim is unsupported; it
never stops the run. If fewer than config.MIN_VERIFIED_STEPS steps survive, the
plan is saved with status "held" and HoldForReview is raised.
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

MIN_QUOTE_WORDS = 4

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
_ILLUSTRATIVE_SYSTEM = (
    "You are a strict editor. You get ONE sentence labelled as an illustration. Answer "
    "'supported' ONLY if it is a pure analogy or illustration that makes no factual assertion "
    "about the subject chemical, product, company, regulation or process. Otherwise answer "
    "'unsupported'. Set passage_index to null."
)
_REPAIR_SYSTEM = (
    "You repair one step of an explainer plan that teaches from first principles. The step "
    "is missing a verified mechanism (its why/how), a verified link (how it follows from the "
    "previous step), or has claims that failed verification. Return NEW claims (same schema) "
    "that fix exactly what is missing or failed; the supported claims are kept as they are, "
    "do not repeat them. Re-explain a missing mechanism or link from the article's own text, "
    "or from a fact a reference source will confirm, stated plainly the way a textbook "
    "would. Set `role` on every claim: mechanism (what physically or chemically happens), "
    "link (kind reasoning, 'because <previous step>, so <this step>'), or fact. Set `kind` on "
    "every claim; blog_stated claims must copy a verbatim sentence from the article into "
    "`quote`, other kinds leave `quote` empty. Drop any number you cannot support with its "
    "unit. An empty list is allowed only if the step truly cannot be explained honestly."
)


def _result(claim: dict, verdict: str, reason: str, support: dict | None = None) -> dict:
    out = copy.deepcopy(claim)
    out["verdict"] = verdict
    out["reason"] = reason
    if support is not None:
        out["support"] = support
    return out


def _valid_index(idx, n: int) -> bool:
    return isinstance(idx, int) and not isinstance(idx, bool) and 0 <= idx < n


def verify_claim(claim: dict, *, step: dict, premises: list[str], canonical: str,
                 retriever, local_only: bool = False) -> dict:
    """Return a copy of `claim` with `verdict`, `reason` and (if sourced) `support` set."""
    kind, text, terms = claim["kind"], claim["text"], step.get("terms", [])

    if kind == "blog_stated":
        quote = claim["support"]["quote"]
        if not quote or locate_verbatim(quote, canonical) is None:
            return _result(claim, "unsupported", "quote not found verbatim in the article")
        if len(quote.split()) < MIN_QUOTE_WORDS:
            return _result(claim, "unsupported",
                           f"quote too short (need at least {MIN_QUOTE_WORDS} words)")
        errs = explanation.gate_claim_numbers(text, quote, terms)
        if errs:
            return _result(claim, "unsupported", "; ".join(errs))
        res = text_llm.generate_schema_json(
            f"Claim: {text}\n\nPassages:\n[0] (article) {quote}", _VERIFIER_SYSTEM,
            VERDICT_SCHEMA, local_only=local_only, role="verifier")
        if res["verdict"] != "supported" or not _valid_index(res.get("passage_index"), 1):
            verdict = res["verdict"] if res["verdict"] != "supported" else "unsupported"
            return _result(claim, verdict, "claim not supported by its quote")
        return _result(claim, "supported", "verbatim quote found and supports the claim")

    if kind == "illustrative":
        if explanation.has_number(text):
            return _result(claim, "unsupported", "illustrative claims must not contain numbers")
        res = text_llm.generate_schema_json(
            f"Sentence: {text}", _ILLUSTRATIVE_SYSTEM, VERDICT_SCHEMA,
            local_only=local_only, role="verifier")
        if res["verdict"] != "supported":
            return _result(claim, "unsupported", "illustrative claim asserts a fact")
        return _result(claim, "supported", "pure illustration, no numbers or facts")

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
        if not _valid_index(idx, len(passages)):
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
        return _result(claim, "supported", "follows from verified premises",
                       {"type": None, "quote": "", "url": None})

    return _result(claim, "unsupported", f"unknown claim kind {kind!r}")


def _role(claim: dict) -> str:
    return claim.get("role") or "fact"   # plans stored before roles existed


def _is_premise(claim: dict) -> bool:
    return claim.get("verdict") == "supported" and claim["kind"] != "illustrative"


def verify_plan(plan: dict, *, canonical: str, retriever, local_only: bool = False) -> None:
    """Verify every not-yet-verified claim, in chain order (mutates `plan`).

    Premises are supported non-illustrative claims. A `link` claim is checked against
    the premises of EARLIER steps only (it must follow from the previous step, not
    restate its own); any other claim also sees the earlier claims of its own step.
    """
    earlier: list[str] = []
    for step in plan["steps"]:
        own: list[str] = []
        for i, claim in enumerate(step["claims"]):
            if claim.get("verdict") is None:
                premises = earlier if _role(claim) == "link" else earlier + own
                step["claims"][i] = verify_claim(
                    claim, step=step, premises=list(premises), canonical=canonical,
                    retriever=retriever, local_only=local_only)
            if _is_premise(step["claims"][i]):
                own.append(step["claims"][i]["text"])
        earlier += own


def _missing(step: dict, first: bool) -> list[str]:
    """What a step lacks to survive: a verified mechanism, and (unless first) a link."""
    sup = [c for c in step["claims"] if c.get("verdict") == "supported"]
    out = []
    if not any(_role(c) == "mechanism" and c["kind"] != "illustrative" for c in sup):
        out.append("no verified mechanism")
    if not first and not any(_role(c) == "link" and c["kind"] == "reasoning" for c in sup):
        out.append("no verified link")
    return out


def _failing_steps(plan: dict) -> list[tuple[dict, list[str]]]:
    """Steps that need a repair round, with what each lacks. A failed link on the first
    step is ignored (it has no previous step to follow from; prune drops the claim)."""
    out = []
    for pos, step in enumerate(plan["steps"]):
        first = pos == 0
        failed = [c for c in step["claims"] if c.get("verdict") != "supported"
                  and not (first and _role(c) == "link")]
        missing = _missing(step, first)
        if failed or missing:
            out.append((step, missing))
    return out


def _repair_step(plan: dict, step: dict, missing: list[str], round_no: int,
                 local_only: bool, canonical: str) -> None:
    failed = [c for c in step["claims"] if c["verdict"] != "supported"]
    kept = [c for c in step["claims"] if c["verdict"] == "supported"]
    idx = next(i for i, s in enumerate(plan["steps"]) if s is step)
    prev = plan["steps"][idx - 1] if idx > 0 else None
    problems = []
    if "no verified mechanism" in missing:
        problems.append("MISSING MECHANISM: no verified claim with role 'mechanism' explains "
                        "why/how this step happens. Add one (kind blog_stated, external_fact "
                        "or reasoning; never illustrative).")
    if "no verified link" in missing:
        problems.append("MISSING LINK: no verified claim with role 'link' (kind reasoning) "
                        "says how this step follows from the previous step. Add one, "
                        "'because <previous step>, so <this step>'. It is checked ONLY "
                        "against what earlier steps established (the previous step is shown "
                        "above): its 'so' part must follow from that alone; put any new fact in a mechanism or fact "
                        "claim instead.")
    if failed:
        problems.append("FAILED CLAIMS: the claims listed below failed verification; "
                        "replace them with claims a source or the article supports, or "
                        "leave them out.")
    prev_block = ""
    if prev is not None:
        prev_block = (f"Previous step (this step must follow from it): {prev['claim_text']}\n"
                      + "\n".join(f"- {c['text']}" for c in prev["claims"]
                                  if c.get("verdict") == "supported") + "\n")
    prompt = (
        f"Article:\n{canonical}\n\n"
        f"Question: {plan['question']}\n{prev_block}Step goal: {step['claim_text']}\n"
        "What is wrong with this step:\n" + "\n".join(f"- {p}" for p in problems)
        + "\nSupported claims (keep as they are):\n"
        + "\n".join(f"- [{_role(c)}] {c['text']}" for c in kept)
        + "\nFailed claims:\n"
        + "\n".join(f"- [{_role(c)}] {c['text']} ({c['kind']}) -> {c['verdict']}: "
                    f"{c.get('reason', '')}" for c in failed)
        + "\nReturn only the new claims, each with `role`, `kind` and `quote` set; an "
          "empty list is allowed only if the step truly cannot be explained.")
    res = text_llm.generate_schema_json(prompt, _REPAIR_SYSTEM, REPAIR_SCHEMA,
                                        local_only=local_only, role="planner")
    plan["dropped_claims"].extend(failed)
    new = []
    for n, raw in enumerate(res["claims"], start=1):
        raw = dict(raw, id=f"{step['step_id']}_r{round_no}_{n}")
        new.append(explanation.finalize_claim(raw))
    step["claims"] = kept + new


def _prune(plan: dict) -> bool:
    """Drop unsupported claims, then every step without a verified mechanism or (unless
    it is the first surviving step) a verified link. Returns True if a step was dropped."""
    surviving = []
    for step in plan["steps"]:
        ok = [c for c in step["claims"] if c["verdict"] == "supported"]
        plan["dropped_claims"].extend(c for c in step["claims"] if c["verdict"] != "supported")
        step["claims"] = ok
        missing = _missing(step, first=not surviving)
        if missing:
            plan["dropped_steps"].append(dict(step, reason="; ".join(missing)))
        else:
            surviving.append(step)
    dropped = len(surviving) < len(plan["steps"])
    plan["steps"] = surviving
    return dropped


def _prune_and_relink(plan: dict, *, canonical: str, retriever, local_only: bool) -> None:
    """Prune; whenever a step is dropped, re-check the reasoning claims of the surviving
    steps that came after it against the surviving premises only, then prune again.
    (The repair budget is spent by then: a step is only dropped once its repair rounds
    are exhausted, so a re-checked claim that fails is dropped, not repaired.)"""
    while True:
        order = list(plan["steps"])
        if not _prune(plan):
            return
        alive = {id(s) for s in plan["steps"]}
        first_drop = next(i for i, s in enumerate(order) if id(s) not in alive)
        later = {id(s) for s in order[first_drop + 1:]} & alive
        reset = False
        for step in plan["steps"]:
            if id(step) in later:
                for c in step["claims"]:
                    if c["kind"] == "reasoning":
                        c["verdict"] = None
                        reset = True
        if not reset:
            return
        verify_plan(plan, canonical=canonical, retriever=retriever, local_only=local_only)


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
        failing = _failing_steps(plan)
        if not failing:
            break
        for step, missing in failing:
            _repair_step(plan, step, missing, round_no, local_only, canonical)
        verify_plan(plan, canonical=canonical, retriever=retriever, local_only=local_only)
    _prune_and_relink(plan, canonical=canonical, retriever=retriever, local_only=local_only)

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
