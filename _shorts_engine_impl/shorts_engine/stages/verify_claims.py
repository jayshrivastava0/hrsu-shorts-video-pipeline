# shorts_engine/stages/verify_claims.py
"""VERIFY stage -- explanation_plan.json (planned) to explanation_plan.json (verified).

Every factual claim is checked before it can reach the script:
  blog_stated   -> verbatim quote located in canonical.txt (+ number/unit gate)
  external_fact -> retrieved passages judged by an independent verifier model
  reasoning     -> logic check against already-supported premises (earlier steps' plus
                   this step's own supported claims)
  illustrative  -> no numbers allowed
How a step follows from earlier ones is not a claim and is not LLM-verified: it is the
step's `depends_on` (ids of earlier claims), checked deterministically. A step survives
only with a supported non-illustrative `mechanism` claim (its why/how) and, unless it is
the plan's first step, a depends_on id naming a supported claim of an earlier SURVIVING
step. A failed claim, a missing mechanism or a missing/invalid depends_on is repaired by
the planner (config.PLAN_MAX_REPAIR_ROUNDS rounds; bound: max_repair_calls) and only then
dropped, never guessed; a dropped step takes the steps that depended only on it with it.
Empty/failed web search just means the claim is unsupported; it never stops the run. If
fewer than config.MIN_VERIFIED_STEPS steps survive, the plan is saved with status "held"
and HoldForReview is raised.
"""
from __future__ import annotations

import copy
import json
import logging
from pathlib import Path

from shorts_engine import config, corroboration, explanation
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
    "properties": {"claims": {"type": "array", "items": explanation.CLAIM_ITEM_SCHEMA},
                   # optional: a corrected depends_on / transition for the step
                   "depends_on": {"type": "array", "items": {"type": "string"}},
                   "transition": {"type": "string"}},
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
    "is missing a verified mechanism (its why/how), has no valid depends_on (the ids of the "
    "earlier verified claims it builds on), or has claims that failed verification. Return "
    "NEW claims (same schema) that fix exactly what is missing or failed; the supported "
    "claims are kept as they are, do not repeat them. Re-explain a missing mechanism from "
    "the article's own text, or from a fact a reference source will confirm, stated plainly "
    "the way a textbook would. Set `role` on every claim: mechanism (what physically or "
    "chemically happens) or fact (a supporting detail). Set `kind` on every claim; "
    "blog_stated claims must copy a verbatim sentence from the article into `quote`, other "
    "kinds leave `quote` empty. Drop any number you cannot support with its unit. When "
    "depends_on is the problem, also return `depends_on` (ids of verified claims of earlier "
    "steps that hold, exactly as listed) and a `transition`: one short sentence in plain "
    "words saying how those claims lead to this step -- no digits, no new fact, cause or "
    "condition (it is narration guidance, not a claim). An empty claims list is allowed "
    "when only depends_on needs fixing, or if the step truly cannot be explained honestly."
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
        errs = explanation.gate_claim_numbers(text, quote, terms, source_units=True)
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


def _is_premise(claim: dict) -> bool:
    return claim.get("verdict") == "supported" and claim["kind"] != "illustrative"


def verify_plan(plan: dict, *, canonical: str, retriever, local_only: bool = False) -> None:
    """Verify every not-yet-verified claim, in chain order (mutates `plan`).

    Premises are supported non-illustrative claims. Within a step the non-reasoning claims
    are verified first, then the reasoning claims (the stored order is not changed), so a
    reasoning claim is judged against the premises of earlier steps PLUS this step's own
    supported claims, whatever their stored order. A step currently missing a verified
    mechanism contributes NO premises to later steps: it may yet be dropped.
    """
    earlier: list[str] = []
    for step in plan["steps"]:
        claims = step["claims"]
        order = ([i for i, c in enumerate(claims) if c["kind"] != "reasoning"]
                 + [i for i, c in enumerate(claims) if c["kind"] == "reasoning"])
        own: list[str] = []
        for i in order:
            if claims[i].get("verdict") is None:
                claims[i] = verify_claim(
                    claims[i], step=step, premises=earlier + own, canonical=canonical,
                    retriever=retriever, local_only=local_only)
            if _is_premise(claims[i]):
                own.append(claims[i]["text"])
        if _has_mechanism(step):
            earlier += own


def _has_mechanism(step: dict) -> bool:
    return any(c.get("role") == "mechanism" and c["kind"] != "illustrative"
               and c.get("verdict") == "supported" for c in step["claims"])


NO_MECHANISM = "no verified mechanism"
NO_DEPENDS_ON = "no valid depends_on"
DEPENDS_ON_DROPPED = "depends on dropped claims"


def _chain_status(steps: list[dict], first) -> list[tuple[list[str], list[str]]]:
    """For each step, in order: (what it lacks to hold, its live depends_on ids).

    A step holds when it has a supported non-illustrative mechanism and -- unless it is
    `first` (the plan's original first step, which follows from the question) -- at least
    one depends_on id naming a SUPPORTED claim of an earlier step that itself holds. Since
    depends_on only points backwards, one forward pass decides every step. The same
    function drives the repair triggers and the final prune, so a step whose dependency
    sits in a step that does not hold is repaired (it can re-point) before it is dropped.
    """
    held_ids: set[str] = set()
    out = []
    for step in steps:
        missing = [] if _has_mechanism(step) else [NO_MECHANISM]
        live = [d for d in explanation.depends_on_ids(step) if d in held_ids]
        if step is not first and not live:
            missing.append(NO_DEPENDS_ON)
        if not missing:
            held_ids |= {c["id"] for c in step["claims"] if c.get("verdict") == "supported"}
        out.append((missing, live))
    return out


def _failing_steps(plan: dict, first) -> list[tuple[dict, list[str]]]:
    """Steps that need a repair round, with what each lacks: any claim that is not
    supported, no verified mechanism, or (not first) no valid depends_on."""
    out = []
    for step, (missing, _live) in zip(plan["steps"], _chain_status(plan["steps"], first)):
        failed = [c for c in step["claims"] if c.get("verdict") != "supported"]
        if failed or missing:
            out.append((step, missing))
    return out


def _earlier_block(plan: dict, idx: int, first) -> tuple[str, set[str]]:
    """Listing of the earlier steps' supported claims with ids, and the ids that are valid
    depends_on targets right now (supported claims of earlier steps that hold)."""
    status = _chain_status(plan["steps"], first)
    lines, valid = [], set()
    for step, (missing, _live) in zip(plan["steps"][:idx], status[:idx]):
        holds = not missing
        lines.append(f"Earlier step {step['step_id']} ("
                     + ("holds" if holds else "does NOT hold yet: " + "; ".join(missing))
                     + f"): {step.get('claim_text', '')}")
        for c in step["claims"]:
            if c.get("verdict") == "supported":
                lines.append(f"  - [{c['id']}] ({_role(c)}) {c['text']}")
                if holds:
                    valid.add(c["id"])
    return "\n".join(lines), valid


def _role(claim: dict) -> str:
    """Stored role; anything but "mechanism" (none, or a legacy "link") is a fact."""
    return "mechanism" if claim.get("role") == "mechanism" else "fact"


def _repair_step(plan: dict, step: dict, missing: list[str], round_no: int,
                 local_only: bool, canonical: str, first) -> None:
    failed = [c for c in step["claims"] if c["verdict"] != "supported"]
    kept = [c for c in step["claims"] if c["verdict"] == "supported"]
    idx = next(i for i, s in enumerate(plan["steps"]) if s is step)
    earlier, valid = _earlier_block(plan, idx, first)
    problems = []
    if NO_MECHANISM in missing:
        problems.append("MISSING MECHANISM: no verified claim with role 'mechanism' explains "
                        "why/how this step happens. Add one (kind blog_stated, external_fact "
                        "or reasoning; never illustrative).")
    if NO_DEPENDS_ON in missing:
        problems.append(
            "NO VALID DEPENDS_ON: none of this step's depends_on ids names a verified claim "
            "of an earlier step that holds. Return `depends_on` with the ids (from the "
            "earlier steps listed above that hold) of the claims this step builds on, and a "
            f"matching `transition`: words only, at most {config.TRANSITION_MAX_WORDS} words, "
            "no digits, no new fact, cause or condition.")
    if failed:
        problems.append("FAILED CLAIMS: the claims listed below failed verification; "
                        "replace them with claims a source or the article supports, or "
                        "leave them out.")
    chain = (f"{earlier}\n" if earlier
             else "This is the first step: it follows from the question and has no "
                  "depends_on.\n")
    prompt = (
        f"Article:\n{canonical}\n\n"
        f"Question: {plan['question']}\n{chain}Step goal: {step['claim_text']}\n"
        f"Current depends_on: {explanation.depends_on_ids(step)}\n"
        f"Current transition: {step.get('transition') or ''}\n"
        "What is wrong with this step:\n" + "\n".join(f"- {p}" for p in problems)
        + "\nSupported claims (keep as they are):\n"
        + "\n".join(f"- [{_role(c)}] {c['text']}" for c in kept)
        + "\nFailed claims:\n"
        + "\n".join(f"- [{_role(c)}] {c['text']} ({c['kind']}) -> {c['verdict']}: "
                    f"{c.get('reason', '')}" for c in failed)
        + "\nReturn only the new claims, each with `role`, `kind` and `quote` set (an empty "
          "list is allowed when only depends_on needs fixing, or if the step truly cannot "
          "be explained)" + ("; plus `depends_on` and `transition` when depends_on is the "
                             "problem." if NO_DEPENDS_ON in missing and step is not first
                             else "."))
    res = text_llm.generate_schema_json(prompt, _REPAIR_SYSTEM, REPAIR_SCHEMA,
                                        local_only=local_only, role="planner")
    plan["dropped_claims"].extend(failed)
    new = []
    for n, raw in enumerate(res["claims"], start=1):
        raw = dict(raw, id=f"{step['step_id']}_r{round_no}_{n}")
        new.append(explanation.finalize_claim(raw))
    step["claims"] = kept + new
    # A repaired depends_on is applied only when it is valid NOW: non-empty, and every id
    # a supported claim of an earlier step that holds. The first step never takes one.
    deps = res.get("depends_on")
    if (step is not first and isinstance(deps, list) and deps
            and all(isinstance(d, str) and d in valid for d in deps)):
        step["depends_on"] = list(dict.fromkeys(deps))
    transition = res.get("transition")
    if isinstance(transition, str) and not explanation.transition_errors(transition):
        step["transition"] = transition


def _prune(plan: dict, first) -> None:
    """Drop unsupported claims, then every step that does not hold (see _chain_status):
    no verified mechanism, or (unless `first`) no depends_on id naming a supported claim
    of an earlier SURVIVING step. Each surviving step keeps only its live depends_on ids.

    Reasons: "no verified mechanism"; "depends on dropped claims" when its depends_on named
    claims that are gone (dropped, or in a dropped step); "no valid depends_on" otherwise.

    Termination: depends_on only points backwards, so the forward pass in _chain_status
    is already a fixed point; the loop re-checks as a guard, and every pass that changes
    anything removes at least one step, so it ends within len(steps) + 1 passes.
    """
    for step in plan["steps"]:
        plan["dropped_claims"].extend(c for c in step["claims"] if c["verdict"] != "supported")
        step["claims"] = [c for c in step["claims"] if c["verdict"] == "supported"]
    known = {c.get("id") for c in plan["dropped_claims"]} | {
        c["id"] for s in plan["steps"] for c in s["claims"]}
    while True:
        status = _chain_status(plan["steps"], first)
        surviving = []
        for step, (missing, live) in zip(plan["steps"], status):
            if missing:
                reasons = [DEPENDS_ON_DROPPED if m == NO_DEPENDS_ON and any(
                    d in known for d in explanation.depends_on_ids(step)) else m
                    for m in missing]
                plan["dropped_steps"].append(dict(step, reason="; ".join(reasons)))
            else:
                if step is not first:
                    step["depends_on"] = live
                surviving.append(step)
        if len(surviving) == len(plan["steps"]):
            return
        plan["steps"] = surviving


def max_repair_calls(n_steps: int) -> int:
    """Upper bound on planner REPAIR calls for a plan of `n_steps` steps.

    Only the repair rounds call the planner, and each round repairs each step at most once
    (steps are never removed during the rounds): PLAN_MAX_REPAIR_ROUNDS * n_steps. The
    final prune makes no LLM calls."""
    return config.PLAN_MAX_REPAIR_ROUNDS * n_steps


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
    # the plan's original first step follows from the question and needs no depends_on
    first = plan["steps"][0] if plan["steps"] else None

    verify_plan(plan, canonical=canonical, retriever=retriever, local_only=local_only)
    for round_no in range(1, config.PLAN_MAX_REPAIR_ROUNDS + 1):
        failing = _failing_steps(plan, first)
        if not failing:
            break
        for step, missing in failing:
            _repair_step(plan, step, missing, round_no, local_only, canonical, first)
        verify_plan(plan, canonical=canonical, retriever=retriever, local_only=local_only)
    _prune(plan, first)

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
    if config.CORROBORATE_CLAIMS:
        try:  # best effort: a trust signal, never a gate; must not hold or fail the run
            counts = corroboration.corroborate_plan(
                plan, retriever, limit=config.CORROBORATION_LIMIT, local_only=local_only)
            logger.info(f"verify_claims: corroboration {counts}")
        except Exception as exc:
            logger.warning(f"verify_claims: corroboration skipped: {exc}")
    path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"explanation_plan": "explanation_plan.json"}
