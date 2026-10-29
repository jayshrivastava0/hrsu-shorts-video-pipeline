# Brief-driven videos with selectable video types — design

Date: 2026-10-29. Status: draft for approval.

## Intent and why

HRSU's video pipeline today does one thing: it takes a published blog post URL and turns it into a
faceless explainer video of that post. The blog pipeline is different: the owner can instruct it with
any topic (`--topic`) and it writes and publishes a post about anything.

The owner wants the same freedom for video. The channel is a **brand** channel for procurement
managers and executives at Western industrial buyers. A buyer should leave a video thinking "this
company's technical knowledge and product quality are on par with the large established chemical
companies (Mitsubishi- or Nordic-chemical-company class), so I have no quality risk." An explainer
shows technical knowledge. It does not show excellence, ESG practice, market insight, or personality.
So the owner wants to give the pipeline any instruction and get back a finished, on-brand video of the
right kind.

Examples the owner gave:
- **Marketing:** "Christmas is coming. A child doesn't need to worry about REACH or customs. Ask for a
  door quotation and HRSU delivers calcium nitrate to your door."
- **Proof of excellence:** a video showing excellence in the product or process.
- **ESG:** a video about ESG practice.
- **Market commentary:** how the ESG model is shifting and supply chains are looking to India instead
  of China; how the war is stopping Russian producers such as Uralchem from supplying global chains,
  creating an opening for large established suppliers, who carry higher prices.

Claude (Opus 5.5, via Claude Code) is the visual director for every type. This is already decided
and is not reopened here.

## Goals

1. A video can start from a **brief** (free text instruction) instead of only a blog URL. A blog URL
   stays available as an optional input to a brief.
2. Each brief has a **video type**. Types: `explainer`, `proof`, `esg`, `market`, `marketing`. The
   owner can name the type, or leave it as `auto` and the pipeline picks one and says which.
3. A type is a recipe: how facts are sourced, what the script stage does, the director prompt, the
   pacing and default orientation, and the type-specific gate checks. Adding a sixth type later means
   adding a recipe, not changing the pipeline.
4. The existing explainer path keeps producing identical output for a blog URL, except that it now
   stops for review instead of publishing. Existing tests for it still pass, updated only for the
   review stop.
5. **Review first, for every video.** A finished video of any type, including the daily blog-driven
   explainer, stops at `awaiting_review` and is not published. The owner approves or rejects it.
   Approving publishes to YouTube through the existing publish step. A per-type switch allows
   unattended publishing later, off by default for every type (decided by the owner on 2026-10-29:
   "pause every video for now, even the blog ones").
6. One shared brand look across all types: dark navy `#0a192f`, gold `#d4af37`, text `#ccd6f6`,
   muted `#8892b0`, Arial, small letter-spaced "HRSU INDORE" wordmark, one CTA end card to
   hrsuindore.com. A type may change composition and pacing, not the brand tokens.

## Non-goals

- No open-source / Gemma / Kaggle / Harness-squared / videobench work. Visuals stay Claude-only.
- No change to the blog pipeline.
- No AI-generated footage and no stock footage of plants, labs or people. Motion graphics, typography,
  diagrams, and any photos or documents the owner supplies.
- Publishing destination stays YouTube. Posting videos to LinkedIn or Facebook is out of scope.
- No new text-stage models. Script and verification stay on the current local/Ollama-cloud setup.

## Anti-goals (a failure even if it technically works)

- A video that states something false or unsourced about a real company, country, war, sanction or
  person. Market commentary is the highest-risk type.
- Invented certifications, badges, numbers, "QC passed", ISO or REACH claims that are not in the
  supplied documents or `brand_facts.yaml`.
- A marketing video that makes a product or service promise HRSU does not offer.
- Output that looks like a generic stock ad, a slideshow, or a screensaver. The existing hook and
  motion rules in `scheduler/director_prompt.md` apply to every type.
- An opinion presented as fact.

## Type rules (decided with the owner)

| Type | Facts allowed on screen | Creative latitude | Extra gate checks |
|---|---|---|---|
| explainer | Only what the verified narration and plan state (today's rule, unchanged) | Schematic visuals, marked "illustration" | Existing gate |
| proof | Only supplied documents (CoA, TDS, `brand_facts.yaml`) and supplied photos | Motion and layout only | Every number on screen traces to a supplied document |
| esg | `brand_facts.yaml` plus sourced external facts. Only practices HRSU actually has (garden, steam reuse, solar, per the blog project guide) | Motion and layout | No claim without a source; no "greenwashing" phrasing |
| market | Every factual claim about named companies, countries or events passes research and verification with sources. Analysis is labelled as HRSU's view | Layout and metaphor | Source chips shown for each verified claim; dated; entity claims listed in a manifest for the owner to read |
| marketing | Product and service claims only from `brand_facts.yaml`; the CTA is exact | A creative story, characters and metaphor are allowed (e.g. the Christmas child) | No product claim outside `brand_facts.yaml`; story elements contain no false factual statements |

## Pipeline (target)

```
brief (+ optional blog URL, + optional supplied assets, + optional type)
  -> intake: resolve type (auto-classify and report), normalise to a source document
  -> text stage per type: research (DuckDuckGo) where the type needs it, script, claim verification,
     voiceover with word timings
  -> director: Claude on Opus 5.5 with the type's prompt, same HyperFrames workflow tooling
  -> gate: shared checks (duration, lint, voice match) + the type's checks
  -> awaiting_review   (owner watches, approves or rejects with a note)
  -> publish (existing YouTube publish), or reject and optionally re-direct with the note
```

Orientation keeps today's rule: narration of 90 s or more is landscape 1920x1080, otherwise portrait.
A brief may override it.

Entry point: a new command alongside `scheduler.run_daily`, for example
`python -m scheduler.make_video --brief "..." [--type auto|explainer|proof|esg|market|marketing] [--url URL]
[--assets DIR]`, plus a review command (`list`, `approve <id>`, `reject <id> --note ...`). The daily
blog-driven run keeps working as it does now and is the `explainer` type.

## Constraints

- The explainer path runs on Windows Task Scheduler daily. It keeps running daily and producing videos;
  they now wait in `awaiting_review` until the owner approves. Review must not lose or expire them.
- The existing state machine (`scheduler/state.py`: queued, text_done, directed, failed, held,
  failed_gate) is the base. Review adds an `awaiting_review` state; do not fork a second state store.
- The text stage (`_shorts_engine_impl`) currently requires a blog URL. A brief needs a way in.
- Claude allowance is limited. The director stage is the expensive one. Type resolution, research
  and verification run before it so a bad brief fails cheaply.
- The explainer-path branch is `explanation-first-script`, worktree
  `.worktrees/explanation-first-script`. The owner's uncommitted work in the main checkout is never
  touched.

## What the owner decided

- Different video types are wanted; the explainer stays as one of them.
- Free-form briefs for any subject, including marketing, ESG, proof and geopolitical market views.
- Claude as visual director for all types.
- Review each video first; unattended publishing later.
- The five type rules in the table above.

## Calls made by the author (the owner has not decided these)

- Brief enters the engine as a synthetic "source document" (the brief plus research notes), so engine
  stages that expect a blog post stay unchanged where possible.
- Auto-classification of type when `--type` is omitted; it reports its choice and the owner can
  override at review.
- Proof videos use only supplied material; if none is supplied the brief is refused with a clear
  message instead of inventing plant imagery.
- Review is a command-line approve/reject loop with the video path printed; no web UI.
- Rejected videos can be re-directed with the owner's note appended to the director prompt. A reject
  keeps the text stage (script, voice) and re-runs only the director, at most 3 rejects per brief. If
  the note needs different words, the owner edits the brief and starts a new run.
- Type classification: one primary type per brief. If confidence is low, stop before any Claude spend
  and ask the owner. A brief that mixes types gets the primary type and the mix is flagged.
- "Every number on screen traces to a supplied document" is an automatic check: numbers in the rendered
  text are matched against text extracted from the supplied documents. Scanned (image) documents are
  read once at intake with a vision model. Fuzzy checks (greenwashing phrasing, false facts inside a
  story) run as an LLM pass on the script before the director, and the owner's review is the final check.
- Market claims: an unsourceable claim is dropped from the script. If the core thesis cannot be sourced,
  the run fails before the director with a reason. The owner's own view is allowed when labelled as
  HRSU's view on screen.
- State: a brief gets an ID (slug plus short hash) as the key in the same state store, with extra fields
  for type, brief text, assets and review note. New statuses `awaiting_review` and `rejected`. An
  identical repeated brief warns and asks, it does not block.
- Publishing a brief-driven video: privacy follows the existing `HRSU_YOUTUBE_PRIVACY` setting (default
  public, applied only after the owner approves). Title and description are generated from the script;
  the "link in description" CTA line and blog URL are used only when a blog URL was supplied, otherwise
  the type's own CTA is used. Thumbnail follows the existing thumbnail step.
- Assets: PNG, JPG and PDF only, copied into the project's assets folder at intake.

## Facts the owner supplied (2026-10-29) for `brand_facts.yaml`

The builder expands `brand_facts.yaml` with two tiers. Only the first tier may be stated as true in
any video. The second tier must never be stated as done.

**In place (usable):**
- Delivery: HRSU will deliver to a buyer's door when the buyer pays for transport. Any trade term the
  buyer agrees to is acceptable, so a door-delivery quotation is a real offer.
- Anything printed on the current CoA or TDS (assay, specification limits, packaging, minimum order,
  shelf life, hazard class, and so on) may be shown on screen, quoted as printed.
- A garden at the plant.
- Steam reuse, running in one boiler.
- Waste from the reaction (the impurities) is disposed of with government-authority approval, and the
  authority collects it.
- Ash from the wood fire is used in agriculture.
- A scrubber on one reaction vessel.
- Chimneys on three reaction boilers, all above the government's threshold height. Say exactly that
  and no more; never "fully compliant with all regulations".
- Solar power. Its size or share of the plant's power is not stated, so no figure is shown.
- Rainwater harvesting.
- A company-sponsored employee retreat to a religious site, chosen by employee vote, free of cost to
  employees.

**In progress (never stated as done):**
- Steam reuse is being expanded to the other boilers.
- The scrubber is being expanded to the other reaction vessels.
- The chimneys on two of the three boilers are lower than the first one and are being raised to the
  same height as it.
- The new expansion plans to stop burning wood and run on electricity. The electricity department has
  not yet approved it, so this is described, if at all, as planned and pending approval.

**Never claimed:**
- Any CSR programme. The owner says CSR activity is very small; videos do not present HRSU as having
  a CSR programme.

The banned-claims list ("certified", "REACH registered", "ISO 9001", "FDA approved") stays.

## Left to the builder

- File layout, module names, recipe format (data file versus code), and the exact director prompt
  text for each type.
- How the classifier works and how the research stage is parameterised per type.
- How type-specific gate checks are implemented (for example how "every number traces to a source"
  is checked).
- Tests, including a regression test that the explainer path is unchanged.
- Migration of `run_daily` onto the shared flow, as long as behaviour is unchanged.
