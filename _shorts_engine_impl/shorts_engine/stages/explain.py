# shorts_engine/stages/explain.py
"""EXPLAIN stage -- canonical.txt (+ post.json, factsheet.json) to explanation_plan.json.

The planner reads the FULL article (the old script writer never did) and designs
one explanation: the question the viewer will be able to answer, a causal chain of
steps, defined terms with units, and a visual intent per step. Every claim is
tagged with a kind and a role (mechanism/fact); each later step names the earlier claims
it builds on (`depends_on`) plus a words-only `transition`; the deterministic checks in `explanation.validate_plan` run
here and their errors are echoed back for a retry. Claim *verification* against
sources is the next stage (verify_claims).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from shorts_engine import config, explanation
from shorts_engine.brand import BrandFacts, load_brand_facts
from shorts_engine.errors import HoldForReview
from shorts_engine.llm import text_llm

logger = logging.getLogger(__name__)

_PLANNER_SYSTEM = (
    "You design explainer videos for procurement managers sourcing industrial chemicals. "
    "Plan ONE explanation that teaches from first principles: choose the single question the "
    "viewer will be able to answer by the end, then lay out the shortest correct causal chain "
    "of steps that answers it. Use as many steps as the explanation truly needs (at least 3). "
    "The first step must follow directly from the question and the last step must answer it. "
    "Never assume the viewer already knows a supporting fact or mechanism: if a step depends "
    "on it, explain it.\n\n"
    "Every claim has a `role`:\n"
    "- mechanism: the WHY/HOW of the step -- what physically or chemically happens (e.g. "
    "'chloride ions break down the protective oxide film on steel, which starts pitting "
    "corrosion'). EVERY step needs at least one mechanism claim. An analogy is never a "
    "mechanism.\n"
    "- fact: a supporting detail (a figure, an example, a consequence).\n\n"
    "Tag EVERY claim with exactly one kind: blog_stated (copy the supporting sentence from the "
    "article verbatim into `quote`), external_fact (a general fact a reference source would "
    "confirm; leave `quote` empty), reasoning (a conclusion that follows from earlier claims in "
    "the chain; leave `quote` empty), or illustrative (an analogy, never with numbers; leave "
    "`quote` empty). When the article explains the mechanism, use a blog_stated claim "
    "quoting it; otherwise write the mechanism as an external_fact phrased plainly, the way a "
    "textbook or reference source would state it, so a source can confirm it.\n\n"
    "How the steps connect is NOT a claim. Every step has:\n"
    "- `depends_on`: the ids of claims in EARLIER steps that this step builds on (never a "
    "claim of the same or a later step). The first step has depends_on: [] (it follows from "
    "the question); every later step lists at least one earlier claim id, normally the "
    "mechanism of the step before it.\n"
    "- `transition`: one short sentence (at most 25 words) saying in plain words how those "
    "earlier claims lead to this step. It is narration guidance only, not a claim: no facts, "
    "no numbers, no new cause or condition -- anything new belongs in a mechanism or fact "
    "claim, where it is verified. The first step's transition may be empty or tie the step "
    "to the question. Good transition: 'With the surface staying damp, the next question is "
    "what that does to the dust.' Bad transition: 'Because it stays damp, it cuts water "
    "truck trips.' (a new effect no claim states).\n\n"
    "Define every technical term in the `terms` of the step that first uses it (with its unit "
    "if it has one, else an empty string), BEFORE any later step uses it; never rely on a term "
    "you have not defined. Never write a number unless it comes from a blog_stated quote or "
    "you are certain a reference source states it, and write the unit right next to every "
    "number (e.g. '425 dS/m'); a unit you cannot support means you must drop the number.\n\n"
    "Three examples of good steps (generic illustrations, not this article):\n"
    "1. Step s1 'salt water reaches the steel' -- mechanism m1 (external_fact): 'Dissolved "
    "chloride ions break down the thin protective oxide film on steel.'; depends_on: []; "
    "transition: 'Start with what road salt does once rain carries it onto steel.'\n"
    "2. Step s2 'pits grow into the metal' -- mechanism m2 (external_fact): 'Where the oxide "
    "film is broken, the exposed iron oxidises and the pit deepens.'; depends_on: ['m1']; "
    "transition: 'Once that film is gone, look at what happens to the bare metal.'\n"
    "3. Step s3 'a more soluble salt spreads further' -- mechanism m3 (external_fact): 'Water "
    "molecules surround and pull apart the ions of a soluble salt, so it dissolves.'; "
    "depends_on: ['m2']; transition: 'That raises the question of how far the salt "
    "travels.'\n\n"
    "`visual_intent` says what a diagram for the step should show: the entities, the "
    "relationship between them, and the quantity that changes. The `question` and "
    "`payoff.takeaway` contain no numbers. `payoff` names exactly one brand differentiator id."
)


def _planner_prompt(post_meta: dict, canonical: str, factsheet: dict, brand: BrandFacts, *,
                    errors: list[str] | None = None) -> str:
    facts_block = "\n".join(
        f"[{f['id']}] \"{f['verbatim_quote']}\" (value={f['value']} {f['unit']})"
        for f in factsheet.get("facts", [])
    ) or "(none extracted)"
    diff_block = "\n".join(f"[{d['id']}] {d['text']}" for d in brand.differentiators)
    prompt = (
        f"Blog: {post_meta.get('title')} | region={post_meta.get('region')} | "
        f"category={post_meta.get('category')}\n\n"
        f"FULL ARTICLE (the source for every blog_stated quote):\n{canonical}\n\n"
        f"NUMERIC FACTS already extracted from the article:\n{facts_block}\n\n"
        f"BRAND DIFFERENTIATORS (payoff.differentiator_id must be one of these ids):\n"
        f"{diff_block}\n\nRemember: every step explains its why/how in a `mechanism` claim, "
        f"every step after the first lists in `depends_on` the ids of the earlier claims it "
        f"builds on (the first step has none), every `transition` is words only (no facts, "
        f"no numbers), and every term is defined before it is used.\n"
        f"Write the explanation plan now as JSON."
    )
    if errors:
        prompt += ("\n\nYour previous draft FAILED these checks -- fix every one:\n"
                   + "\n".join(f"- {e}" for e in errors))
    return prompt


def run(ctx) -> dict[str, str]:
    """Run the EXPLAIN stage and write explanation_plan.json (status "planned").

    Raises:
        HoldForReview: if no draft passes `validate_plan` within config.LLM_MAX_RETRIES
            (a minimal explanation_plan.json with status "held" is written first).
    """
    ws = Path(ctx.workspace)
    canonical = (ws / "canonical.txt").read_text(encoding="utf-8")
    post_meta = json.loads((ws / "post.json").read_text(encoding="utf-8"))
    factsheet = json.loads((ws / "factsheet.json").read_text(encoding="utf-8"))
    brand = load_brand_facts()
    local_only = bool(ctx.flags.get("local_only", False))

    errors: list[str] = []
    raw: dict = {}
    attempts = 0
    for attempt in range(1, config.LLM_MAX_RETRIES + 1):
        attempts = attempt
        raw = text_llm.generate_schema_json(
            _planner_prompt(post_meta, canonical, factsheet, brand,
                            errors=errors or None),
            _PLANNER_SYSTEM, explanation.PLAN_SCHEMA, local_only=local_only, role="planner",
        )
        errors = explanation.validate_plan(raw, canonical, brand)
        if not errors:
            break
        logger.warning(f"plan failed checks (attempt {attempt}/{config.LLM_MAX_RETRIES}): "
                       f"{errors}")
    if errors:
        # Audit trail: a minimal held plan carrying the reasons (plan_report renders it).
        held = {"status": "held", "question": "", "steps": [], "hold_reasons": errors,
                "dropped_claims": [], "dropped_steps": []}
        (ws / "explanation_plan.json").write_text(
            json.dumps(held, indent=2, ensure_ascii=False), encoding="utf-8")
        raise HoldForReview(errors)

    doc = explanation.to_plan_document(raw, attempts)
    (ws / "explanation_plan.json").write_text(
        json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    n_claims = sum(len(s["claims"]) for s in doc["steps"])
    logger.info(f"explain: {len(doc['steps'])} steps, {n_claims} claims, attempts={attempts}")
    return {"explanation_plan": "explanation_plan.json"}
