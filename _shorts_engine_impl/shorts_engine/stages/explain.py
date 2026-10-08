# shorts_engine/stages/explain.py
"""EXPLAIN stage -- canonical.txt (+ post.json, factsheet.json) to explanation_plan.json.

The planner reads the FULL article (the old script writer never did) and designs
one explanation: the question the viewer will be able to answer, a causal chain of
steps, defined terms with units, and a visual intent per step. Every claim is
tagged with a kind; the deterministic checks in `explanation.validate_plan` run
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
    "Plan ONE explanation: choose the single question the viewer will be able to answer by "
    "the end, then lay out the shortest correct causal chain of steps that answers it. Use as "
    "many steps as the explanation truly needs (at least 3). You may use general chemistry and "
    "engineering knowledge, but tag EVERY factual claim with exactly one kind: blog_stated "
    "(copy the supporting sentence from the article verbatim into `quote`), external_fact (a "
    "general fact a reference source would confirm; leave `quote` empty), reasoning (a "
    "conclusion that follows from earlier claims in the chain; leave `quote` empty), or "
    "illustrative (an analogy, never with numbers; leave `quote` empty). Never write a number "
    "unless it comes from a blog_stated quote or you are certain a reference source states it, "
    "and write the unit right next to every number (e.g. '425 dS/m'); a unit you cannot "
    "support means you must drop the number. Define every technical term you introduce in "
    "`terms` (with its unit if it has one, else an empty string). `visual_intent` says what a "
    "diagram for the step should show: the entities, the relationship between them, and the "
    "quantity that changes. The `question` and `payoff.takeaway` contain no numbers. `payoff` "
    "names exactly one brand differentiator id."
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
        f"{diff_block}\n\nWrite the explanation plan now as JSON."
    )
    if errors:
        prompt += ("\n\nYour previous draft FAILED these checks -- fix every one:\n"
                   + "\n".join(f"- {e}" for e in errors))
    return prompt


def run(ctx) -> dict[str, str]:
    """Run the EXPLAIN stage and write explanation_plan.json (status "planned").

    Raises:
        HoldForReview: if no draft passes `validate_plan` within config.LLM_MAX_RETRIES.
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
        raise HoldForReview(errors)

    doc = explanation.to_plan_document(raw, attempts)
    (ws / "explanation_plan.json").write_text(
        json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    n_claims = sum(len(s["claims"]) for s in doc["steps"])
    logger.info(f"explain: {len(doc['steps'])} steps, {n_claims} claims, attempts={attempts}")
    return {"explanation_plan": "explanation_plan.json"}
