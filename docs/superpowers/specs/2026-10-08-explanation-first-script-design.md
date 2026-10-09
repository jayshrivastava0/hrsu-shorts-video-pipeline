# Explanation-First Script Stage — Design

**Date:** 2026-10-08
**Status:** Draft for review
**Sub-project:** 1 of 3 (see Decomposition)

## 1. Problem

The pipeline turns an HRSU blog post into a video, and the videos are not understandable. A
real run (`output/shorts/run-e721c494`, post "Calcium Nitrate Solubility in Precision Foliar
Blends") shows why. All of it traces to the script stage:

1. **The writer never sees the article.** `script.py` states the writer receives "only
   already-verbatim facts". For this post the facts stage extracted three items (`425 EC`,
   `20 %`, `80 %`, all about sandy soil). The writer had the title plus three numbers, so it
   invented the connective logic and jumped from foliar blending to desert soil.
2. **Every gate checks form, not meaning.** The gates cover number tracing, banned phrases,
   per-purpose word budgets, card_text length and the differentiator count. Nothing checks that
   a beat follows from the previous one, that a term is defined, or that a number has a unit
   (`425` passed with no unit).
3. **The format forces a teaser.** A 30 s floor at 1.7 words/s is about 51 words, five
   one-line beats. `apply_word_topup` pads short scripts with canned filler clauses.
4. **The critic is one-shot.** It scores the first draft once, triggers one rewrite, and the
   rewrite is never re-scored. The saved critique can describe a draft that was replaced. The
   rewriter receives no new source material, so it cannot fix a grounding problem.
5. **A "diagram" is three unrelated labels** (`diagram_labels`), so even the visuals that exist
   explain nothing. This is addressed in sub-project 2.

## 2. Goals and non-goals

**Goals**
- A script that teaches one thing: a question, a causal chain built up step by step, and a
  payoff tied to an HRSU differentiator.
- Free explanation (general knowledge allowed), but **no wrong facts**: every factual claim is
  verified against the blog or a retrieved source, or it is dropped.
- Length follows the content. 30 s is a floor and there is **no ceiling**.
- A harness that is reusable: stages are independent packages, the brand lives in
  `brand_facts.yaml`, and the input is a source document, not specifically an HRSU blog.
- Every claim leaves an audit trail (kind, source, verdict).

**Non-goals (this spec)**
- The diagram engine and narration-synced animation (sub-project 2).
- The horizontal long-form format (sub-project 3).
- Choosing the scene-author model (sub-project 2).
- Human spot-check of published videos (possible follow-up).
- Decision/classifier models (laya, tev1, nimble, clef-flash): evaluated and dropped. They
  need Ollama's new `/v1/systemone` endpoint (the machine runs 0.32.15, they need 0.35+), the
  published fact-checking accuracy (88.3% for laya, 512-token limit) is too low to gate facts,
  and the small decisions they would make are deterministic code or part of the planner's own
  structured output. The per-role model map (Section 8) allows adding one later.

## 3. Decomposition

1. **Explanation-first script stage** (this spec).
2. **Diagram engine:** typed diagram spec, template library, narration-synced reveals, scene
   author model, geometry/lint checks. Consumes `visual_intent` from this spec.
3. **Horizontal long-form format:** layout profile and chapter structure on top of 1 and 2.

## 4. Pipeline

```
INGEST -> FACTS -> EXPLAIN -> VERIFY -> SCRIPT -> SHOTLIST -> AUDIO -> VISUALS -> ...
                    plan      claims    narration  uses plan
                    + steps   re-checked per step   visual_intent
```

- **EXPLAIN** (new) reads the full `canonical.txt` and the factsheet and writes
  `explanation_plan.json`.
- **VERIFY** (new) classifies and verifies every claim, repairs or drops failures, and
  annotates `explanation_plan.json` with sources and verdicts.
- **SCRIPT** (changed) turns the verified steps into narration. It can no longer invent the
  logic, because the logic is the plan.
- **SHOTLIST** passes each step's `visual_intent` through as the diagram brief, replacing
  today's `diagram_labels` (consumed by sub-project 2).
- Existing FACTS stays: it supplies verbatim quotes and the number/unit records.

Adding stages requires updating the manifest state sequence, the stage tools in
`harness/packages/tool-shorts-stage`, and the stage list in `harness/system_prompt.md`
(which currently states a fixed stage order). The implementation plan confirms exact edits.

## 5. The explanation plan (`explanation_plan.json`)

```json
{
  "question": "Why does calcium nitrate stay dissolved in a foliar blend?",
  "steps": [
    {
      "step_id": "s1",
      "claim_text": "plain-language sentence the narration will convey",
      "claims": [
        {"id": "c1", "text": "...", "kind": "blog_stated|external_fact|reasoning|illustrative",
         "support": {"type": "blog_quote|source", "quote": "...", "url": null},
         "verdict": "supported|contradicted|unsupported", "needs_number": false}
      ],
      "terms": [{"term": "EC", "definition": "...", "unit": "dS/m"}],
      "visual_intent": {"entities": ["..."], "relationship": "...", "quantity": "..."}
    }
  ],
  "payoff": {"takeaway": "...", "differentiator_id": "b_..."}
}
```

- `question`: the one thing the viewer can answer at the end, phrased for a procurement reader.
- `steps`: an ordered causal chain, **minimum 3 verified steps, no maximum**.
- `visual_intent`: what should be drawn (entities, relationship, changing quantity). It is
  opaque to this stage and is the seed of the diagram spec in sub-project 2.
- `payoff`: ties to exactly one brand differentiator, as the existing differentiator gate
  requires for the CTA.

### Claim kinds and verification

| Kind | Example | Verification |
|---|---|---|
| `blog_stated` | "EC peaked at 425 in sandy soil" | Verbatim quote check against `canonical.txt` using `normalize_for_match` |
| `external_fact` | "Calcium nitrate dissolves readily in water" | Retrieval-backed (Section 6): the verifier judges the claim against retrieved passages |
| `reasoning` | "So the nozzle is less likely to clog" | Must follow from claims already `supported` in the same chain; checked by a logic pass |
| `illustrative` | "Like sugar dissolving in tea" | Allowed; must contain no numbers or specifics |

**Numbers.** Any number or unit must trace to a blog quote or a retrieved passage, as today's
`gate_numbers` does. A number the model "just knows" is never allowed. Every number must also
carry a unit, defined by the fact record or by the step's `terms`.

**Verifier independence.** The verifier and the coherence critic are a different model family
from the writer (Section 8). The verifier sees only the claim plus the retrieved passages,
never the writer's reasoning, and returns a verdict plus the passage it relied on.

## 6. Retrieval tool

The repo currently has no text web search; the only DuckDuckGo code is image search for b-roll
(`video_agent/sources/duckduckgo.py`). This spec adds a small `retrieval` module:

- `search(query)`: text search through the `duckduckgo-search` package already in `requirements.txt` (imported as `ddgs`
with a legacy fallback, as `video_agent/sources/duckduckgo.py` does).
- `fetch(url)`: fetch the page and extract passages relevant to the claim.
- Source ranking prefers government/standards bodies, papers and manufacturer data sheets over
  general web pages, and tries the blog's own citations (`post.json` `citations`) first.
- Results are cached per run so repair rounds do not repeat searches.
- The search backend sits behind one interface so a paid API can replace DuckDuckGo by config.

**Empty or failed retrieval never stops the pipeline.** If a search returns nothing or errors
(after bounded retries), the claim is simply treated as unsupported, handled as in Section 7.
The run continues.

## 7. Failure handling and repair

1. A `contradicted` or `unsupported` claim is removed from its step. The writer rewrites that
   step (up to 2 repair rounds) using only the claims that remain.
2. After repair, all claims are re-verified, and the coherence critic scores the **final**
   plan and script. The score bar stays at the existing threshold (7).
3. If the verified content cannot form 3 steps, or the final script stays below the bar, or
   fewer than 30 s of verified narration remains, the run **does not crash and does not ship**.
   It stops in the harness's existing non-publishing terminal state (`hold_for_review`) with the
   reasons recorded in `explanation_plan.json` and the manifest. It is never padded to reach
   the 30 s floor. (Whether this is an exception or a status flag is a plan-level detail.)
4. Retrieval outages count as case 1 (claims dropped), not as a failure of the run.

## 8. Models and the harness

Models are set per role in config (not hardcoded), so changing one is a config edit:

| Role | Model |
|---|---|
| Planner / writer | `gemma4:31b-cloud` (user's decision) |
| Fact verifier + coherence critic | `nemotron-3-ultra:cloud` by default, a different family from the writer, with retrieval and source citation (`glm-5.2` was not reachable on the current Ollama plan: HTTP 402) |
| Scene author | Unchanged for now; decided in sub-project 2 |
| Vision judge | `gemma4:31b-cloud` (unchanged) |

The verifier default is confirmed by a bake-off: a fixed set of true, false and unsupported
claims scored against each candidate. The same set is a regression test for model changes.
Before relying on any cloud model, the plan includes a check that it actually responds on this
setup (manifests exist on disk for several that `ollama list` does not show). Result (2026-10-09): `nemotron-3-ultra:cloud` scored 1.0 on supported, contradicted and unsupported claims with a false-support rate of 0.0.

Reuse: the new stages are separate packages under `harness/packages/`, take a source
document as input, and read brand rules, banned claims, differentiators and CTA from
`brand_facts.yaml`. Another brand needs a different brand file only.

## 9. Changes to existing script logic

**Kept:** number tracing (`gate_numbers`), banned phrases (`gate_banned`), the
differentiator-in-CTA gate, the schema-validated retry pattern in `text_llm`.

**Removed:** per-purpose word budgets (`gate_word_budget`, `PURPOSE_TEMPLATE` ranges),
`apply_word_topup` and `_TOPUP_PHRASES`, the fixed 51-word shape.

**Changed:**
- Writer input becomes the verified explanation plan plus the term definitions.
- Beats keep `beat`, `purpose` (hook / mechanism / proof / cta) and `fact_ids`, and gain
  `step_id`, so the existing shotlist keeps working until sub-project 2.
- The 30 s total floor stays (`TOTAL_MIN_S`); there is no ceiling.
- Packaging labels each video short-form or long-form from its duration, so very long vertical
  videos are not assumed to be Shorts.
- The run logs step, claim and shot counts so cost growth is visible. There is no hard cap.

## 10. Testing

- **Unit tests** per gate: quote matching, number-with-unit tracing, claim-kind handling, the
  3-step minimum, and the hold (not crash, not ship) behaviour.
- **Retrieval tests:** empty result, error and timeout each leave the run going with the claim
  dropped.
- **Verifier tests** on the fixed claim set (true, false, unsupported), run against each
  candidate model.
- **Regression test:** replay `run-e721c494` and assert the new pipeline either produces a
  coherent plan or holds with a reason. It must not produce the foliar-to-soil jump or a
  unit-less number.
- **`--dry-run`** prints the plan and verdicts without rendering, per the project convention.

## 11. Risks and open questions

- **Automated verification cannot reach zero error**, especially for niche chemistry where web
  sources are thin. Dropping unsupported claims is the mitigation. A sampled human spot-check
  is a possible later addition.
- **DuckDuckGo search is rate-limited and uneven.** Mitigated by source ranking, per-run
  caching, bounded retries and the backend interface.
- **Gemma is slower than the alternatives.** Accepted by the user; length has no ceiling, so
  runs will take longer.
- **Posts with little mechanism content** may produce short or held runs. That is intended.
- Open for the plan: exact manifest state names for the new stages, whether holds are
  exceptions or statuses, and the retrieval passage-extraction method.
