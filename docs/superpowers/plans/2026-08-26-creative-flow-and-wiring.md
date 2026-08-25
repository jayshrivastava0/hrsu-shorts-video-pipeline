# Creative Flow & Real-Pipeline Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every generated video gets a genuinely different narrative structure and visual
archetype per beat (not the same headline→diagram→stat→quote→logo skeleton every time), and the
already-built HyperFrames creative rendering actually reaches a real video for the first time —
via `python -m shorts_engine <blog_url>`, unattended.

**Architecture:** `script.py`'s fixed 5-beat schema becomes free-form (purpose-tagged, no count
cap, 30s floor). `shotlist.py`'s deterministic beat-name→type ladder is replaced by a lighter
planner that only resolves facts verbatim and *suggests* a type. The visuals-stage authoring
subagent's persona is updated so that suggestion is a hint, not a directive, and gains a
`request_broll` tool for real photos. A new Node script drives the existing top-level harness
agent through exactly one real, headless turn per phase (visuals, then assembly) — the same
`agent.followup()` / `agent.whenIdle()` primitive `tests/roundtrip.e2e.ts` already proves works —
and `cli.py`'s `"visuals"`/`"assemble"` stage functions shell out to it.

**Tech Stack:** Python 3.12 (`shorts_engine`), Node 22 / TypeScript (`harness`, Cordis harness),
Vitest, pytest.

**Spec:** `docs/superpowers/specs/2026-08-26-creative-flow-and-wiring-design.md`

## Global Constraints

- Minimum total video duration: 30 seconds. No maximum.
- The final beat's `purpose` must be `"cta"` — every video ends with a call to action.
- Every number/statistic a shot displays must trace verbatim to `factsheet.json` via the beat's
  `fact_ids` — never invented, computed, or rounded differently by the LLM.
- No changes to `facts.py`, `audio.py`, `ingest.py`, `verify.py`, `assemble.py`'s `reflow()`/
  `_reassemble()` (already fixed this session), `package.py`, `publish.py`, or the
  `RunManifest`/`STATUS_ORDER` model.
- The old PIL card renderers (`shorts_engine/cards/*.py`) stay in the repo, unused by default, as
  a rollback reference (matches the visuals-stage design's own precedent) — not deleted in this
  plan.

---

### Task 1: Purpose-based duration template + 30s floor, no ceiling

**Files:**
- Modify: `_shorts_engine_impl/shorts_engine/config.py:57-68` (replace `BEAT_TEMPLATE`),
  `config.py:127-128` (`TOTAL_MIN_S`/`TOTAL_MAX_S`)
- Test: `_shorts_engine_impl/tests/shorts_engine/test_config.py` (new file if none exists — check
  first with `ls _shorts_engine_impl/tests/shorts_engine/test_config.py`)

**Interfaces:**
- Produces: `config.PURPOSE_TEMPLATE: dict[str, dict]` (keys: `hook`, `stakes`, `mechanism`,
  `proof`, `cta`, `other`; each value `{"min_s": float, "max_s": float}`), `config.MIN_BEATS = 3`,
  `config.TOTAL_MIN_S = 30.0`. `config.TOTAL_MAX_S` and `config.BEAT_TEMPLATE` are deleted — Task 2
  is their only consumer and removes every reference in the same commit, so no dangling import
  survives between tasks.

- [ ] **Step 1: Write the failing test**

```python
# _shorts_engine_impl/tests/shorts_engine/test_config.py
from shorts_engine import config


def test_purpose_template_covers_all_purposes():
    for purpose in ("hook", "stakes", "mechanism", "proof", "cta", "other"):
        assert purpose in config.PURPOSE_TEMPLATE
        spec = config.PURPOSE_TEMPLATE[purpose]
        assert spec["min_s"] > 0
        assert spec["max_s"] >= spec["min_s"]


def test_total_min_s_is_30_and_no_ceiling_constant_remains():
    assert config.TOTAL_MIN_S == 30.0
    assert not hasattr(config, "TOTAL_MAX_S")
    assert not hasattr(config, "BEAT_TEMPLATE")


def test_min_beats_is_three():
    assert config.MIN_BEATS == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `_shorts_engine_impl/`): `pytest tests/shorts_engine/test_config.py -v`
Expected: FAIL — `AttributeError: module 'shorts_engine.config' has no attribute 'PURPOSE_TEMPLATE'`

- [ ] **Step 3: Replace `BEAT_TEMPLATE` with `PURPOSE_TEMPLATE`, update duration constants**

In `config.py`, replace lines 57-68:

```python
# ── Beat/Scene structure ────────────────────────────────────────────────────
# Per-PURPOSE pacing guidance (not per fixed beat name/position) -- beats are
# now free-form in count/order/naming (2026-08-26 creative-flow redesign); a
# beat's `purpose` tag is what carries pacing intent forward, consumed by
# shorts_engine.stages.script (gate_word_budget, gate_total_duration,
# apply_word_topup, the writer prompt's beat rules) and shotlist.py (type
# suggestion, CTA-length cap). "other" is the fallback for any beat whose
# purpose isn't one of the five named ones.
PURPOSE_TEMPLATE: dict[str, dict] = {
    "hook":      {"min_s": 2.0, "max_s": 4.0},
    "stakes":    {"min_s": 4.0, "max_s": 6.0},
    "mechanism": {"min_s": 8.0, "max_s": 12.0},
    "proof":     {"min_s": 6.0, "max_s": 10.0},
    "cta":       {"min_s": 6.0, "max_s": 8.0},
    "other":     {"min_s": 3.0, "max_s": 8.0},
}
MIN_BEATS = 3
```

And replace lines 127-128 (`LOGO_CTA_MAX_S` stays; only the two duration constants below it
change):

```python
TOTAL_MIN_S = 30.0
```

(delete the old `TOTAL_MAX_S = 50.0` line entirely — no replacement constant, per the "no cap"
decision).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/shorts_engine/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add _shorts_engine_impl/shorts_engine/config.py _shorts_engine_impl/tests/shorts_engine/test_config.py
git commit -m "config: replace fixed BEAT_TEMPLATE with purpose-based pacing, 30s floor, no ceiling"
```

---

### Task 2: `script.py` — flexible beat structure

**Files:**
- Modify: `_shorts_engine_impl/shorts_engine/stages/script.py` (all of `gate_word_budget`,
  `gate_total_duration`, `apply_word_topup`, `_TOPUP_PHRASES`, `run_gates`, `SCRIPT_SCHEMA`,
  `_WRITER_SYSTEM`, `_beat_rules`, `_writer_prompt`)
- Modify: `_shorts_engine_impl/tests/shorts_engine/test_script.py` (existing gate tests keyed to
  the old fixed 5-beat structure need rewriting — read the current file first with
  `pytest tests/shorts_engine/test_script.py -v --collect-only` to see what's there before editing)

**Interfaces:**
- Consumes: `config.PURPOSE_TEMPLATE`, `config.MIN_BEATS`, `config.TOTAL_MIN_S` (Task 1)
- Produces: every beat dict now carries both `"beat"` (a free-form string label, no longer
  constrained to five fixed names) and `"purpose"` (one of `hook|stakes|mechanism|proof|cta|
  other`) — this is what Task 3's `shotlist.py` and every later task consume. `gate_differentiator`
  and `gate_numbers`/`gate_banned`/`gate_card_text` are unchanged (already generic over beat
  identity) and are NOT touched by this task.

- [ ] **Step 1: Write the failing tests**

```python
# Add to _shorts_engine_impl/tests/shorts_engine/test_script.py
from shorts_engine.stages.script import (
    run_gates, gate_word_budget, gate_total_duration, apply_word_topup, SCRIPT_SCHEMA,
)


def _beat(beat, purpose, narration, fact_ids=None, card_text="c", broll_wish=""):
    return {"beat": beat, "purpose": purpose, "narration": narration,
            "fact_ids": fact_ids or [], "card_text": card_text, "broll_wish": broll_wish}


class TestFlexibleBeatStructure:
    def test_run_gates_rejects_fewer_than_min_beats(self):
        beats = [_beat("hook", "hook", "word " * 5), _beat("cta", "cta", "word " * 10)]
        errors = run_gates(beats, {"facts": []}, _fake_brand())
        assert any("at least" in e for e in errors)

    def test_run_gates_rejects_final_beat_not_cta_purpose(self):
        beats = [_beat("a", "hook", "w " * 5), _beat("b", "stakes", "w " * 8),
                 _beat("c", "proof", "w " * 8)]
        errors = run_gates(beats, {"facts": []}, _fake_brand())
        assert any('purpose "cta"' in e for e in errors)

    def test_run_gates_accepts_nonstandard_beat_count_and_names(self):
        # 7 beats, none named hook/stakes/mechanism/proof -- only purposes matter.
        beats = [
            _beat("cold-open", "hook", "word " * 3),
            _beat("problem", "stakes", "word " * 4),
            _beat("twist", "other", "word " * 5),
            _beat("how-it-works", "mechanism", "word " * 8),
            _beat("evidence-1", "proof", "word " * 6),
            _beat("evidence-2", "proof", "word " * 6),
            _beat("close", "cta", "word " * 6,
                  fact_ids=[_fake_brand().differentiators[0]["id"]]),
        ]
        errors = run_gates(beats, {"facts": []}, _fake_brand())
        assert not any(e.startswith("structure:") for e in errors)

    def test_gate_word_budget_keys_off_purpose_not_position(self):
        # A beat named "twist" with purpose "mechanism" must be judged against
        # mechanism's word range, not against whatever beat sits at that index.
        beats = [_beat("twist", "mechanism", "word " * 2)]  # far under mechanism's floor
        errors = gate_word_budget(beats)
        assert any("budget[twist]" in e for e in errors)

    def test_gate_total_duration_only_checks_floor_no_ceiling(self):
        # 200 words is far over the old 50s/85-word ceiling -- must NOT error.
        beats = [_beat("a", "cta", "word " * 200)]
        assert gate_total_duration(beats) == []

    def test_apply_word_topup_uses_purpose_keyed_headroom(self):
        beats = [_beat("twist", "mechanism", "short narration here")]
        padded = apply_word_topup(beats)
        assert len(padded[0]["narration"].split()) > len(beats[0]["narration"].split())

    def test_schema_has_no_max_items_and_requires_purpose(self):
        beats_schema = SCRIPT_SCHEMA["properties"]["beats"]
        assert "maxItems" not in beats_schema
        assert beats_schema["minItems"] == 3
        assert "purpose" in beats_schema["items"]["required"]


def _fake_brand():
    from shorts_engine.brand import BrandFacts
    return BrandFacts(
        differentiators=[{"id": "b_purity", "text": "High-purity calcium nitrate"}],
        cta_lines=["Visit hrsuindore.com"], domain="hrsuindore.com", banned_claims=[],
    )
```

(If `_fake_brand`/`BrandFacts` construction differs from the real signature, check
`shorts_engine/brand.py` first and adjust the helper — don't guess blindly.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/shorts_engine/test_script.py -v -k TestFlexibleBeatStructure`
Expected: FAIL (structural checks still enforce exactly 5 named beats; `purpose` key doesn't exist
yet; `gate_total_duration` still has a ceiling)

- [ ] **Step 3: Rewrite the gates and schema**

Replace `gate_word_budget` (script.py:170-201):

```python
def gate_word_budget(beats: list[dict]) -> list[str]:
    """
    Check each beat's narration word count against its own `purpose`'s
    config.PURPOSE_TEMPLATE seconds range (not positional alignment with a
    fixed template -- beats are free-form in count/order since the
    2026-08-26 creative-flow redesign), converted to words via
    WORDS_PER_SECOND and tolerated by WORD_BUDGET_TOLERANCE.
    """
    errs: list[str] = []
    tol = config.WORD_BUDGET_TOLERANCE
    for b in beats:
        spec = config.PURPOSE_TEMPLATE.get(b.get("purpose"), config.PURPOSE_TEMPLATE["other"])
        words = len(b.get("narration", "").split())
        lo = spec["min_s"] * config.WORDS_PER_SECOND * (1 - tol)
        hi = spec["max_s"] * config.WORDS_PER_SECOND * (1 + tol)
        if not (lo <= words <= hi):
            errs.append(
                f"budget[{b.get('beat')}]: {words} words outside "
                f"[{math.ceil(lo)}, {math.floor(hi)}]"
            )
    return errs
```

Replace `gate_total_duration` (script.py:205-269) — floor only, no ceiling branch:

```python
def gate_total_duration(beats: list[dict]) -> list[str]:
    """
    Check the script's AGGREGATE estimated duration against the 30s floor
    (config.TOTAL_MIN_S). No ceiling -- per the 2026-08-26 creative-flow
    decision, longer videos are fine; only "too short" is a defect.
    """
    total_words = sum(len(b.get("narration", "").split()) for b in beats)
    total_s = total_words / config.WORDS_PER_SECOND
    if total_s >= config.TOTAL_MIN_S:
        return []

    min_words = round(config.TOTAL_MIN_S * config.WORDS_PER_SECOND)
    tol = config.WORD_BUDGET_TOLERANCE
    deficit = min_words - total_words + 3  # small buffer vs. undershoot (see apply_word_topup)

    headroom = []
    for b in beats:
        spec = config.PURPOSE_TEMPLATE.get(b.get("purpose"), config.PURPOSE_TEMPLATE["other"])
        words = len(b.get("narration", "").split())
        hi = int(spec["max_s"] * config.WORDS_PER_SECOND * (1 + tol))
        if hi > words:
            headroom.append((hi - words, b.get("beat"), words, hi))
    headroom.sort(reverse=True)
    suggestion = "; ".join(
        f"{beat} (currently {words} words, can go up to {hi})"
        for _room, beat, words, hi in headroom[:3]
    )
    return [
        f"total_duration: {total_s:.1f}s ({total_words} words) is short of "
        f"the {config.TOTAL_MIN_S:.0f}s floor ({min_words} words). Add "
        f"AT LEAST {deficit} more words total, distributed across the "
        f"beats with the most room: {suggestion}."
    ]
```

Replace `_TOPUP_PHRASES` (script.py:280-290) — same phrase pools, now keyed by purpose plus an
`"other"` bucket:

```python
_TOPUP_PHRASES: dict[str, list[str]] = {
    "hook": ["for", "procurement", "teams", "evaluating", "suppliers", "today"],
    "stakes": ["this", "affects", "cost", "control", "and", "supply",
               "reliability", "directly"],
    "mechanism": ["understanding", "this", "mechanism", "helps", "teams",
                  "set", "realistic", "expectations", "before", "sourcing"],
    "proof": ["this", "result", "held", "up", "under", "real", "operating",
              "conditions", "in", "the", "field"],
    "cta": ["reach", "out", "to", "discuss", "your", "specific", "sourcing",
            "requirements", "today"],
    "other": ["this", "point", "matters", "for", "teams", "planning",
               "their", "next", "sourcing", "decision"],
}
```

Replace `apply_word_topup` (script.py:293-342) — key headroom/pool lookup by `purpose`:

```python
def apply_word_topup(beats: list[dict]) -> list[dict]:
    """
    Deterministically pad narration word counts to clear the 30s floor,
    without touching any beat's own gate_word_budget ceiling. Keyed by each
    beat's `purpose` (config.PURPOSE_TEMPLATE), not position -- beats are
    free-form since the 2026-08-26 creative-flow redesign.
    """
    tol = config.WORD_BUDGET_TOLERANCE
    beats = [dict(b) for b in beats]
    total_words = sum(len(b.get("narration", "").split()) for b in beats)
    min_words = round(config.TOTAL_MIN_S * config.WORDS_PER_SECOND)
    deficit = min_words - total_words
    if deficit <= 0:
        return beats

    def _spec(b):
        return config.PURPOSE_TEMPLATE.get(b.get("purpose"), config.PURPOSE_TEMPLATE["other"])

    def _headroom(b):
        spec = _spec(b)
        hi = spec["max_s"] * config.WORDS_PER_SECOND * (1 + tol)
        return hi - len(b.get("narration", "").split())

    order = sorted(beats, key=_headroom, reverse=True)
    for b in order:
        if deficit <= 0:
            break
        spec = _spec(b)
        hi = int(spec["max_s"] * config.WORDS_PER_SECOND * (1 + tol))
        words = b.get("narration", "").split()
        room = hi - len(words)
        if room <= 0:
            continue
        pool = _TOPUP_PHRASES.get(b.get("purpose"), _TOPUP_PHRASES["other"])
        take = min(room, deficit, len(pool))
        if take <= 0:
            continue
        narration = b.get("narration", "").rstrip()
        if narration and narration[-1] not in ".!?":
            narration += "."
        b["narration"] = (narration + " " + " ".join(pool[:take])).strip() + "."
        deficit -= take
    return beats
```

Replace `run_gates`'s structural check (script.py:439-443, inside the existing function — keep
the rest of the function body, the five-gate aggregation call, unchanged):

```python
    if len(beats) < config.MIN_BEATS:
        return [f"structure: expected at least {config.MIN_BEATS} beats, got {len(beats)}"]
    if not beats or beats[-1].get("purpose") != "cta":
        return ['structure: the final beat must have purpose "cta"']
```

(delete the old `expected_beats`/exact-order check lines above it — there is no fixed order left
to check against).

Replace `SCRIPT_SCHEMA` (script.py:456-482):

```python
SCRIPT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "beats": {
            "type": "array",
            "minItems": config.MIN_BEATS,
            "items": {
                "type": "object",
                "properties": {
                    "beat": {"type": "string"},
                    "purpose": {"enum": ["hook", "stakes", "mechanism", "proof", "cta", "other"]},
                    "narration": {"type": "string"},
                    "fact_ids": {"type": "array", "items": {"type": "string"}},
                    "card_text": {"type": "string"},
                    "broll_wish": {"type": "string"},
                    "diagram_labels": {"type": "array", "items": {"type": "string"},
                                       "maxItems": 4},
                },
                "required": ["beat", "purpose", "narration", "fact_ids", "card_text",
                             "broll_wish"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["beats"],
    "additionalProperties": False,
}
```

Replace `_WRITER_SYSTEM` (script.py:497-504):

```python
_WRITER_SYSTEM = (
    "You write video scripts (at least 30 seconds) for procurement managers sourcing "
    "industrial chemicals. Voice: concrete, technical, zero hype. You choose how many beats "
    "to use and what to call them -- there is no fixed count or fixed set of names. Every "
    "beat needs a `purpose` tag: hook, stakes, mechanism, proof, cta, or other. The FINAL "
    "beat's purpose MUST be cta. HARD RULES: every number you use MUST come from a provided "
    "fact's verbatim quote, and that fact's id MUST be listed in the beat's fact_ids. Never "
    "invent statistics. card_text is at most 7 words and must not repeat the narration. The "
    "cta beat cites exactly one brand differentiator id."
)
```

Replace `_beat_rules` (script.py:517-532):

```python
def _beat_rules() -> str:
    """Render config.PURPOSE_TEMPLATE as a human-readable seconds/words block for the
    writer prompt, one entry per purpose (not per fixed beat -- beats are free-form)."""
    tol = config.WORD_BUDGET_TOLERANCE
    return "\n".join(
        f"- {purpose}: {spec['min_s']:.0f}-{spec['max_s']:.0f}s "
        f"({math.ceil(spec['min_s'] * config.WORDS_PER_SECOND * (1 - tol))}-"
        f"{math.floor(spec['max_s'] * config.WORDS_PER_SECOND * (1 + tol))} "
        f"words allowed)"
        for purpose, spec in config.PURPOSE_TEMPLATE.items()
    )
```

In `_writer_prompt` (script.py:553-571), replace the two lines referencing "five beats"/fixed
count:

```python
        f"Beat purpose guide (pick however many beats you need, name them freely, tag each "
        f"with one of these purposes):\n{_beat_rules()}\n\n"
        f"The FINAL beat's purpose MUST be \"cta\". All beats' narration word counts must SUM "
        f"to AT LEAST {config.TOTAL_MIN_S * config.WORDS_PER_SECOND:.0f} words combined (no "
        f"maximum), while each beat stays inside its own purpose's word range above.\n\n"
```

(replaces the old `f"The five beats' narration word counts must SUM to between..."` block).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/shorts_engine/test_script.py -v`
Expected: PASS for the new `TestFlexibleBeatStructure` class. Some OLD tests in this file (any
asserting the exact 5-name structure, e.g. a test hardcoding `beats[0]["beat"] == "hook"` as a
structural requirement) will now fail — update those to use `purpose` instead of `beat` for any
assertion that was really checking narrative role, and delete any assertion that specifically
checked "exactly 5 beats" or "exact beat-name order" as a hard requirement, since that's no longer
true by design. Re-run until the whole file passes.

- [ ] **Step 5: Commit**

```bash
git add _shorts_engine_impl/shorts_engine/stages/script.py _shorts_engine_impl/tests/shorts_engine/test_script.py
git commit -m "script: replace fixed 5-beat schema with free-form, purpose-tagged beats"
```

---

### Task 3: `shotlist.py` — freeform shot planning, type as suggestion

**Files:**
- Modify: `_shorts_engine_impl/shorts_engine/stages/shotlist.py` (replace `plan_beat_shots`,
  `lint_shotlist`; `run()` is unchanged — it already just loops `script_doc["beats"]` generically)
- Modify: `_shorts_engine_impl/shorts_engine/stages/assemble.py:29-30` (`_cap()` — currently keys
  off `shot["type"] == "LOGO_CTA"`, must key off `shot["beat_purpose"] == "cta"` since `type` no
  longer reliably appears)
- Test: `_shorts_engine_impl/tests/shorts_engine/test_shotlist.py`

**Interfaces:**
- Consumes: beat dicts with `"purpose"` (Task 2)
- Produces: each shot dict now has `suggested_type` (was `type` — advisory, not read by any gate),
  `beat_purpose` (copied from the source beat's `purpose`), and — when a fact was resolved — the
  fact's verbatim text/value/unit/citation directly in `payload` under `fact_text`/`fact_value`/
  `fact_unit`/`fact_citation`, so Task 4's authoring subagent never has to look anything up. Task 4
  and Task 5 both key the CTA-length cap off `beat_purpose == "cta"`, not `type`.

- [ ] **Step 1: Write the failing tests**

```python
# Add to _shorts_engine_impl/tests/shorts_engine/test_shotlist.py
from shorts_engine.stages.shotlist import plan_beat_shots, lint_shotlist


def _beat(beat, purpose, narration, fact_ids=None, card_text="c", broll_wish=""):
    return {"beat": beat, "purpose": purpose, "narration": narration,
            "fact_ids": fact_ids or [], "card_text": card_text, "broll_wish": broll_wish}


class TestFreeformShotPlanning:
    def test_shots_carry_suggested_type_not_type(self):
        shots = plan_beat_shots(_beat("cold-open", "hook", "Cold weather pours."),
                                 {}, {}, _fake_brand())
        assert "suggested_type" in shots[0]
        assert "type" not in shots[0]

    def test_shots_carry_beat_purpose(self):
        shots = plan_beat_shots(_beat("twist", "mechanism", "How it works, step one."),
                                 {}, {}, _fake_brand())
        assert all(s["beat_purpose"] == "mechanism" for s in shots)

    def test_fact_resolved_verbatim_into_payload(self):
        facts = {"f1": {"id": "f1", "verbatim_quote": "425 mS/cm peak conductivity",
                        "value": 425, "unit": "mS/cm", "citation_marker": 1}}
        cites = {1: {"url": "https://epa.gov/x", "kind": "standard"}}
        shots = plan_beat_shots(
            _beat("evidence", "proof", "Peak conductivity reached 425.", fact_ids=["f1"]),
            facts, cites, _fake_brand())
        assert shots[0]["payload"]["fact_text"] == "425 mS/cm peak conductivity"
        assert shots[0]["payload"]["fact_value"] == 425

    def test_cta_purpose_gets_cta_length_cap(self):
        shots = plan_beat_shots(
            _beat("close", "cta", "Visit us today for pricing and technical support now.",
                  fact_ids=["b_purity"]),
            {}, {}, _fake_brand())
        assert all(s["duration_s"] <= 10.0 for s in shots)  # config.LOGO_CTA_MAX_S

    def test_lint_shotlist_has_no_ceiling(self):
        shots = [{"id": "s00", "duration_s": 4.0, "beat_purpose": "hook"}] * 20  # far over old 50s
        assert lint_shotlist(shots, {"facts": []}) == []

    def test_lint_shotlist_still_enforces_floor(self):
        shots = [{"id": "s00", "duration_s": 2.0, "beat_purpose": "hook"}]  # well under 30s
        errors = lint_shotlist(shots, {"facts": []})
        assert any("floor" in e or "under" in e for e in errors)


def _fake_brand():
    from shorts_engine.brand import BrandFacts
    return BrandFacts(
        differentiators=[{"id": "b_purity", "text": "High-purity calcium nitrate"}],
        cta_lines=["Visit hrsuindore.com"], domain="hrsuindore.com", banned_claims=[],
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/shorts_engine/test_shotlist.py -v -k TestFreeformShotPlanning`
Expected: FAIL (`plan_beat_shots` still branches on fixed beat names and returns `type`, not
`suggested_type`/`beat_purpose`)

- [ ] **Step 3: Replace `plan_beat_shots` and `lint_shotlist`**

Replace `plan_beat_shots` (shotlist.py:100-194) entirely:

```python
def _suggest_type(purpose: str, fact: dict | None) -> str:
    """Advisory only -- Task 4's authoring subagent may override this. Never
    read by lint_shotlist or any gate."""
    if purpose == "cta":
        return "LOGO_CTA"
    if purpose == "mechanism":
        return "DIAGRAM"
    if fact is not None:
        return "STAT_CARD"
    return "HEADLINE_CARD"


def plan_beat_shots(beat: dict, facts: dict, cites: dict, brand) -> list[dict]:
    narration = beat["narration"]
    purpose = beat.get("purpose", "other")
    spans = pack_phrases(split_phrases(narration)) or [narration]
    est_total = max(estimate_s(narration), config.SHOT_MIN_S)

    fact = _first_numeric_fact(beat, facts) or next(
        (facts[f] for f in beat.get("fact_ids", []) if f in facts), None)

    shots: list[dict] = []
    for span in spans:
        payload = {"text": beat.get("card_text", "")}
        if fact is not None:
            payload["fact_text"] = fact["verbatim_quote"]
            payload["fact_value"] = fact.get("value")
            payload["fact_unit"] = fact.get("unit")
            payload["fact_citation"] = _chip(fact.get("citation_marker"), cites)
        if purpose == "mechanism":
            payload["diagram_labels"] = beat.get("diagram_labels") or _fallback_labels(narration)
        if purpose == "cta":
            diff_text = ""
            for fid in beat.get("fact_ids", []):
                for dd in brand.differentiators:
                    if dd["id"] == fid:
                        diff_text = dd["text"]
            payload["differentiator"] = diff_text
            payload["cta_line"] = brand.cta_lines[0] if brand.cta_lines else ""
            payload["domain"] = brand.domain
        shots.append({
            "id": "", "beat": beat.get("beat", purpose), "beat_purpose": purpose,
            "suggested_type": _suggest_type(purpose, fact), "duration_s": 0.0,
            "narration_span": span, "payload": payload,
            "broll_wish": beat.get("broll_wish", ""),
        })

    per = est_total / len(shots)
    cap = config.LOGO_CTA_MAX_S if purpose == "cta" else config.SHOT_MAX_S
    for s in shots:
        s["duration_s"] = round(min(max(per, config.SHOT_MIN_S), cap), 2)
    return shots
```

Replace `lint_shotlist` (shotlist.py:197-238) — drop the `known` type-enum check and the
`STAT_CARD`/`DIAGRAM`-specific payload checks (no longer meaningful once `type` is advisory only;
grounding is now enforced upstream in `script.py`'s `gate_numbers`, and per-shot visual quality is
verified downstream by `verify.py`'s vision-judge, unchanged), drop the ceiling check:

```python
def lint_shotlist(shots: list[dict], factsheet: dict) -> list[str]:
    errors: list[str] = []
    total = 0.0
    for s in shots:
        total += s["duration_s"]
        cap = config.LOGO_CTA_MAX_S if s.get("beat_purpose") == "cta" else config.SHOT_MAX_S
        if not (config.SHOT_MIN_S <= s["duration_s"] <= cap):
            errors.append(f"{s['id']}: duration {s['duration_s']} outside "
                          f"[{config.SHOT_MIN_S}, {cap}]")
    eps = 0.1
    if total < config.TOTAL_MIN_S - eps:
        errors.append(f"total duration {total:.2f}s under the {config.TOTAL_MIN_S}s floor")
    return errors
```

In `assemble.py`, replace `_cap()` (assemble.py:29-30):

```python
def _cap(shot: dict) -> float:
    return config.LOGO_CTA_MAX_S if shot.get("beat_purpose") == "cta" else config.SHOT_MAX_S
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/shorts_engine/test_shotlist.py tests/shorts_engine/test_assemble_run.py -v`
Expected: PASS. Delete/update any old test in `test_shotlist.py` that asserted a specific `type`
string for a specific beat name (e.g. `"hook" -> HEADLINE_CARD` as a hard requirement) — that
mapping is now advisory, produced by `_suggest_type`, not a contract.

- [ ] **Step 5: Commit**

```bash
git add _shorts_engine_impl/shorts_engine/stages/shotlist.py _shorts_engine_impl/shorts_engine/stages/assemble.py _shorts_engine_impl/tests/shorts_engine/test_shotlist.py _shorts_engine_impl/tests/shorts_engine/test_assemble_run.py
git commit -m "shotlist: replace beat-name type ladder with generic planning, type as suggestion"
```

---

### Task 4: `request_broll` tool + freed authoring persona

**Files:**
- Modify: `_shorts_engine_impl/shorts_engine/stage_cli.py` (new `broll-request` subcommand)
- Test: `_shorts_engine_impl/tests/shorts_engine/test_stage_cli.py`
- Modify: `harness/packages/tool-visual-scene/src/index.ts` (register `request_broll`, add to
  `CHILD_TOOL_NAMES`, add `shortsEngineCwd` config field)
- Modify: `harness/packages/tool-visual-scene/package.json` (add `@hrsu/dsh-tool-shorts-stage`
  dependency, for `runStageCli`)
- Modify: `harness/packages/tool-visual-scene/src/scene-author-persona.md` (type-as-hint,
  `request_broll` usage, verbatim-fact instructions)
- Modify: `harness/cordis.yml` (tool-visual-scene config: add `shortsEngineCwd`)
- Test: `harness/packages/tool-visual-scene/tests/index.spec.ts`

**Interfaces:**
- Consumes: `shorts_engine.sourcing.ladder.acquire(wish: str, narration_span: str, workspace: Path,
  post_images: list[dict], torture: bool = False) -> dict` (existing, unchanged) — returns
  `{"image_path": str | None, "focal_hint": str, "provenance": dict}`.
- Produces: `request_broll` tool, callable by the scene-author subagent (added to its
  `toolFilter.allow` list alongside `write_scene_file`/`render_scene`), taking
  `{workspace, wish, narration_span}` and returning the same shape `acquire()` returns.

- [ ] **Step 1: Write the failing Python test**

```python
# Add to _shorts_engine_impl/tests/shorts_engine/test_stage_cli.py
import json


def test_broll_request_calls_acquisition_ladder(tmp_path, monkeypatch, capsys):
    from shorts_engine import stage_cli
    (tmp_path / "post.json").write_text(json.dumps({"images": []}), encoding="utf-8")
    called = {}

    def fake_acquire(wish, narration_span, workspace, post_images, torture=False):
        called.update(wish=wish, narration_span=narration_span)
        return {"image_path": None, "focal_hint": "center", "provenance": {"reason": "no_wish"}}

    monkeypatch.setattr("shorts_engine.sourcing.ladder.acquire", fake_acquire)
    args = stage_cli.build_parser().parse_args([
        "broll-request", "--workspace", str(tmp_path),
        "--wish", "close-up of white powder", "--narration-span", "The powder dissolves.",
    ])
    exit_code = args.func(args)
    assert exit_code == 0
    out = json.loads(capsys.readouterr().out.strip().split("\n")[-1])
    assert out["provenance"]["reason"] == "no_wish"
    assert called["wish"] == "close-up of white powder"
```

(Check `stage_cli.py`'s actual `build_parser`/dispatch pattern first — `cmd_visuals_prepare` etc.
are wired via argparse subparsers with `set_defaults(func=cmd_x)`; match that exact existing
convention rather than guessing a different one.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/shorts_engine/test_stage_cli.py -v -k broll_request`
Expected: FAIL — `broll-request` is not a recognized subcommand

- [ ] **Step 3: Add the `broll-request` subcommand**

In `stage_cli.py`, add (near the other `cmd_*` functions, following the exact existing pattern —
read `cmd_visuals_prepare`'s first ~10 lines first for the manifest/workspace-loading convention
used elsewhere, then match it):

```python
def cmd_broll_request(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace)
    from shorts_engine.sourcing.ladder import acquire
    post = json.loads((workspace / "post.json").read_text(encoding="utf-8"))
    result = acquire(args.wish, args.narration_span, workspace, post.get("images", []))
    print(json.dumps(result))
    return 0
```

Register it in the subcommand-building section (wherever `visuals-prepare`'s subparser is added —
match the same `add_parser`/`set_defaults`/argument style):

```python
    p_broll = subparsers.add_parser("broll-request")
    p_broll.add_argument("--workspace", required=True)
    p_broll.add_argument("--wish", required=True)
    p_broll.add_argument("--narration-span", required=True)
    p_broll.set_defaults(func=cmd_broll_request)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/shorts_engine/test_stage_cli.py -v -k broll_request`
Expected: PASS

- [ ] **Step 5: Write the failing Node test**

```ts
// Add to harness/packages/tool-visual-scene/tests/index.spec.ts
import { describe, expect, it, vi } from 'vitest'

describe('request_broll tool', () => {
  it('invokes broll-request via runStageCli with the exact wish/narration_span/workspace', async () => {
    const runStageCli = vi.fn().mockResolvedValue({ image_path: null, focal_hint: 'center', provenance: {} })
    // apply() takes runStageCli as an injectable dependency for this test, mirroring how
    // tool-shorts-stage's own tests inject a fake `spawn` into runStageCli itself -- see
    // Step 6 for the actual injection point this test exercises.
    const { apply } = await import('../src/index.ts')
    const ctx = makeFakeCtx() // reuse the existing fake-ctx helper already used by this file's other tests
    apply(ctx, {
      projectRoot: '/tmp/project', shortsEngineCwd: '/tmp/engine',
      agentOptions: { model: 'gemma4:31b-cloud' }, runStageCli,
    })
    const tool = ctx.tools.getRegistered('request_broll')
    await tool.execute({ workspace: '/tmp/ws', wish: 'white powder', narration_span: 'It dissolves.' })
    expect(runStageCli).toHaveBeenCalledWith(
      ['broll-request', '--workspace', '/tmp/ws', '--wish', 'white powder', '--narration-span', 'It dissolves.'],
      { cwd: '/tmp/engine' },
    )
  })
})
```

(`makeFakeCtx`/`ctx.tools.getRegistered` — check the existing tests in this same file for whatever
fake-context helper they already use; reuse it rather than inventing a new one.)

- [ ] **Step 6: Run test to verify it fails**

Run (from `harness/`): `npx vitest run packages/tool-visual-scene/tests/index.spec.ts`
Expected: FAIL — `request_broll` not registered, `shortsEngineCwd`/`runStageCli` not in `Config`

- [ ] **Step 7: Register `request_broll`, wire dependency**

In `harness/packages/tool-visual-scene/package.json`, add to `dependencies`:
`"@hrsu/dsh-tool-shorts-stage": "workspace:*"`.

In `harness/packages/tool-visual-scene/src/index.ts`, add the import:

```ts
import { runStageCli as defaultRunStageCli, type RunStageCliOptions } from '@hrsu/dsh-tool-shorts-stage'
```

Extend `Config` (near the existing fields):

```ts
export interface Config {
  projectRoot: string
  agentOptions: AgentOptions
  subagentProviderName?: string
  /** Absolute path to `_shorts_engine_impl/` — same value tool-shorts-stage's own config uses. */
  shortsEngineCwd: string
  /** Injectable for tests; defaults to the real @hrsu/dsh-tool-shorts-stage runStageCli. */
  runStageCli?: (args: string[], options: RunStageCliOptions) => Promise<Record<string, unknown>>
}
```

Add `CHILD_TOOL_NAMES` entry (line 84): `['write_scene_file', 'render_scene', 'request_broll']`.

Register the tool inside `apply()` (alongside `write_scene_file`/`render_scene`):

```ts
  ctx.tools.register(defineTool({
    name: 'request_broll',
    description:
      'Acquire a real photo/footage frame matching a text description, vision-judged against ' +
      'the narration it accompanies. Returns image_path: null if nothing matched closely enough ' +
      '— treat that as "no real photo available," not an error, and fall back to a synthetic ' +
      'composition for this shot.',
    parameters: {
      workspace: { type: 'string', required: true, description: 'Absolute path to the run workspace (from your shot brief\'s context).' },
      wish: { type: 'string', required: true, description: 'Short description of the visual you want.' },
      narration_span: { type: 'string', required: true, description: 'The narration text this visual accompanies.' },
    },
    output: {
      schema: { type: 'object', additionalProperties: true },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
    },
    async execute(args) {
      const runStageCli = config.runStageCli ?? defaultRunStageCli
      return (await runStageCli(
        ['broll-request', '--workspace', args.workspace as string,
         '--wish', args.wish as string, '--narration-span', args.narration_span as string],
        { cwd: config.shortsEngineCwd },
      )) as Record<string, JsonValue>
    },
  }))
```

In `harness/cordis.yml`'s `tool-visual-scene` entry, add `shortsEngineCwd` alongside the existing
`projectRoot`/`agentOptions` config (same value `tool-shorts-stage`'s own `shortsEngineCwd` config
already uses — copy it, don't invent a new path).

- [ ] **Step 8: Run test to verify it passes**

Run: `npx vitest run packages/tool-visual-scene/tests/index.spec.ts`
Expected: PASS

- [ ] **Step 9: Update the persona document**

In `scene-author-persona.md`, replace the `type` paragraph (lines 124-127):

```markdown
- `type` — a SUGGESTED visual archetype (e.g. `HEADLINE_CARD`, `STAT_CARD`) based on this beat's
  content — a starting point, not an instruction. You may follow it, adapt it, or design something
  else entirely if you judge it serves the narration better. There is no fixed palette; invent
  freely within the brand rules above.
```

Add a new section after "The shot brief you will receive" (after line 135), covering the two new
capabilities:

```markdown
## Real photos: `request_broll`

If a shot calls for a real photograph or footage frame rather than a synthetic composition, call
`request_broll` with a short `wish` description and the shot's `narration_span`. It returns
`image_path` (a local file path to embed as an `<img>`/`<video>` source in your composition, or
`null` if nothing matched closely enough — vision-judged against your wish and the narration, so a
`null` result means no real photo is available, not that you did something wrong) and `focal_hint`
(where the subject sits in frame, for cropping). Treat a `null` `image_path` as routine: fall back
to a synthetic composition for that shot instead of retrying `request_broll` repeatedly.

## Verbatim facts — never invent a number

If the shot brief's `payload` includes `fact_text`, `fact_value`, or `fact_unit`, any number or
statistic you display on screen MUST come from those fields exactly as given — never compute,
round to different precision, restate in different units, or invent a figure, even one that seems
obviously implied by the narration. If the brief has no `fact_text`, do not display a specific
number at all.
```

- [ ] **Step 10: Run the full existing tool-visual-scene test suite**

Run: `npx vitest run packages/tool-visual-scene/`
Expected: all PASS (existing tests unaffected by the additive changes)

- [ ] **Step 11: Commit**

```bash
git add _shorts_engine_impl/shorts_engine/stage_cli.py _shorts_engine_impl/tests/shorts_engine/test_stage_cli.py harness/packages/tool-visual-scene harness/cordis.yml
git commit -m "visuals: add request_broll tool, free the authoring persona from fixed type/facts"
```

---

### Task 5: Headless-agent bridge for visuals + assembly

**Files:**
- Create: `harness/scripts/run-visual-authoring.mts`
- Modify: `_shorts_engine_impl/shorts_engine/cli.py` (replace `"visuals"`/`"assemble"` entries in
  `build_stages()`)
- Test: `harness/scripts/tests/run-visual-authoring.spec.ts` (unit test of the exported
  prompt-building logic — the full boot+agent-turn path is proven by Step 6's manual live run, not
  a fast unit test)

**Interfaces:**
- Consumes: `agents.list()[0]` / `agent.followup()` / `agent.whenIdle()` — the exact same primitive
  `tests/roundtrip.e2e.ts` already exercises successfully against this project's real `cordis.yml`.
- Produces: a script invoked as `node --import tsx/esm run-visual-authoring.mts <visuals|assemble>
  <absolute-workspace-path>`, exit code 0 on success (workspace's `run_manifest.json` reached the
  expected `last_ok_status`) or 1 with a stderr message otherwise.

- [ ] **Step 1: Write the failing Node test (prompt-building logic only)**

```ts
// harness/scripts/tests/run-visual-authoring.spec.ts
import { describe, expect, it } from 'vitest'
import { buildPrompt } from '../run-visual-authoring.mts'

describe('buildPrompt', () => {
  it('visuals phase names stage_visuals_prepare/author_visual_scene/stage_visuals_finalize and forbids earlier stages', () => {
    const prompt = buildPrompt('visuals', '/abs/workspace')
    expect(prompt).toContain('/abs/workspace')
    expect(prompt).toContain('stage_visuals_prepare')
    expect(prompt).toContain('author_visual_scene')
    expect(prompt).toContain('stage_visuals_finalize')
    expect(prompt).toContain('Do not call stage_init')
  })

  it('assemble phase names stage_assemble_prepare/author_assembly_composition/stage_assemble_finalize', () => {
    const prompt = buildPrompt('assemble', '/abs/workspace')
    expect(prompt).toContain('stage_assemble_prepare')
    expect(prompt).toContain('author_assembly_composition')
    expect(prompt).toContain('stage_assemble_finalize')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `harness/`): `npx vitest run scripts/tests/run-visual-authoring.spec.ts`
Expected: FAIL — module doesn't exist yet

- [ ] **Step 3: Write the bridge script**

```ts
// harness/scripts/run-visual-authoring.mts
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { Context } from '@deepseek-ai/cordis'
import Loader from '@deepseek-ai/cordis-plugin-loader'
import Include from '@deepseek-ai/cordis-plugin-include'
import { createUserMessage } from '@deepseek-ai/dsh-llm'

export type Phase = 'visuals' | 'assemble'

/**
 * One real, headless turn of the top-level harness agent, scoped to exactly the sub-sequence
 * `system_prompt.md` already documents for this phase — the agent is told explicitly not to
 * touch any earlier stage, since a workspace this script is invoked against has already had
 * ingest/facts/script/shotlist/audio (or, for the assemble phase, visuals too) run for real by
 * `python -m shorts_engine`'s own STAGE_FUNCS before it shells out to this script.
 */
export function buildPrompt(phase: Phase, workspace: string): string {
  if (phase === 'visuals') {
    return `Continue the shorts-video pipeline in the existing workspace at ${workspace}. ` +
      'It is currently at last_ok_status="audio". Call stage_visuals_prepare with this ' +
      'workspace, then call author_visual_scene once for every entry in the shot_briefs.json ' +
      'it returns, then call stage_visuals_finalize. Do not call stage_init or any stage ' +
      'before stage_visuals_prepare. Report DONE when stage_visuals_finalize succeeds, or ' +
      'report the exact error if any step fails.'
  }
  return `Continue the shorts-video pipeline in the existing workspace at ${workspace}. ` +
    'It is currently at last_ok_status="visuals". Call stage_assemble_prepare with this ' +
    'workspace, then call author_assembly_composition with the assembly_brief.json it ' +
    'returns, then call stage_assemble_finalize. Do not call stage_init or any stage before ' +
    'stage_assemble_prepare. Report DONE when stage_assemble_finalize succeeds, or report ' +
    'the exact error if any step fails.'
}

const EXPECTED_STATUS: Record<Phase, string> = { visuals: 'visuals', assemble: 'assembled' }

async function main(): Promise<void> {
  const [, , phaseArg, workspace] = process.argv
  if (phaseArg !== 'visuals' && phaseArg !== 'assemble' || !workspace) {
    console.error('usage: run-visual-authoring.mts <visuals|assemble> <absolute-workspace-path>')
    process.exit(1)
  }
  const phase = phaseArg as Phase

  const root = new Context()
  root.baseUrl = import.meta.url
  await root.plugin(Loader, { baseUrl: import.meta.url })
  await root.plugin(Include, { path: '../cordis.yml', initial: [] })
  await root.get('loader')?.await()

  const agents = root.get('agents')
  const agent = agents!.list()[0]
  await agent!.whenIdle()

  agent!.followup(createUserMessage({
    content: [{ type: 'text', text: buildPrompt(phase, workspace) }],
    source: { kind: 'user' },
  }))
  await agent!.whenIdle()

  await root.fiber.dispose()

  const manifest = JSON.parse(readFileSync(join(workspace, 'run_manifest.json'), 'utf8')) as
    { last_ok_status: string; error?: string }
  if (manifest.last_ok_status !== EXPECTED_STATUS[phase]) {
    console.error(
      `run-visual-authoring: expected last_ok_status="${EXPECTED_STATUS[phase]}", got ` +
      `"${manifest.last_ok_status}" (error: ${manifest.error ?? 'none recorded'})`,
    )
    process.exit(1)
  }
  process.exit(0)
}

main().catch((err) => {
  console.error(err)
  process.exit(1)
})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run scripts/tests/run-visual-authoring.spec.ts`
Expected: PASS

- [ ] **Step 5: Wire `cli.py` to the bridge**

In `_shorts_engine_impl/shorts_engine/cli.py`, add near the top (with the other imports):

```python
import subprocess
from shorts_engine.errors import EngineError
```

Add, before `build_stages()`:

```python
def _run_creative_stage(ctx, phase: str) -> dict[str, str]:
    workspace = Path(ctx.workspace)
    harness_dir = Path(config.PROJECT_ROOT) / "harness"
    script = harness_dir / "scripts" / "run-visual-authoring.mts"
    result = subprocess.run(
        ["node", "--import", "tsx/esm", str(script), phase, str(workspace.resolve())],
        cwd=harness_dir, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise EngineError(f"{phase}: harness bridge failed: {result.stderr[-2000:]}")
    if phase == "visuals":
        return {"shots_dir": "shots", "visuals_report": "visuals_report.json"}
    return {"video": "video_short.mp4", "captions": "captions.ass",
            "assemble_report": "assemble_report.json"}


def _visuals_stage(ctx) -> dict[str, str]:
    return _run_creative_stage(ctx, "visuals")


def _assemble_stage(ctx) -> dict[str, str]:
    return _run_creative_stage(ctx, "assemble")
```

In `build_stages()`, replace the two entries:

```python
        ("visuals", "visuals", _visuals_stage),
        ("assemble", "assembled", _assemble_stage),
```

(replacing `("visuals", "visuals", visuals.run)` and `("assemble", "assembled", assemble.run)`).
Remove `visuals` and `assemble` from the `from shorts_engine.stages import (...)` line at the top
of `cli.py` if nothing else in the file still references them directly (check with
`grep -n "visuals\.\|assemble\." shorts_engine/cli.py` first).

- [ ] **Step 6: Manual live smoke test (not automated — do this yourself, once)**

This is the actual point of this whole plan — confirm real creative output before calling it done:

```bash
cd _shorts_engine_impl
python -m shorts_engine <a real, already-used blog URL> --workspace-root ../output/shorts
```

Watch for the `visuals`/`assemble` stages taking noticeably longer (real Ollama-cloud model calls
per shot, not instant PIL rendering). On completion, open the run's `contact_sheet.html` and the
individual `harness/hyperframes_scenes_project/compositions/<run_id>/*.html` files it authored —
confirm they're genuinely varied per shot (not all the same layout), and that
`visuals_report.json`'s `suggested_type` per shot doesn't always match what got rendered (proof
the subagent is actually overriding the suggestion, not just rubber-stamping it). If it fails,
read the harness bridge's stderr (surfaced via the `EngineError` from Step 5) — it will name which
tool call failed and why; that's real debugging signal, not a step to skip past.

- [ ] **Step 7: Commit**

```bash
git add harness/scripts _shorts_engine_impl/shorts_engine/cli.py
git commit -m "cli: wire visuals/assemble stages to the real headless-agent creative pipeline"
```

---

## Self-Review Notes

- **Spec coverage:** Decision #1 (full freedom, CTA last) → Task 2. Decision #2 (30s floor, no
  ceiling) → Task 1 + Task 2 + Task 3. Decision #3 (reuse existing subsystems) → Task 5 (no new
  rendering system written). Decision #4 (type as hint) → Task 3 (`suggested_type`) + Task 4
  (persona wording). Decision #5 (verbatim facts) → Task 3 (fact fields in payload) + Task 4
  (persona instruction). Decision #6 (`request_broll`) → Task 4. Architecture section D (the
  bridge) → Task 5, corrected during planning from a bare-tool-call script (which the actual
  `author_visual_scene` implementation rejects — it requires a live parent agent) to a real headless
  agent turn, using the exact primitive `tests/roundtrip.e2e.ts` already proves works in this repo.
- **CTA duration cap:** the spec's addendum (keying `LOGO_CTA_MAX_S` off `beat_purpose` instead of
  `type`) is covered in Task 3, both in `shotlist.py`'s own duration clamp and `lint_shotlist`, and
  in `assemble.py`'s `_cap()`.
- **Type consistency check:** `beat_purpose` is spelled identically in Task 3 (shot dicts),
  Task 4/5's prompts (referenced conceptually, not by field name), and `assemble.py`'s `_cap()`.
  `suggested_type` (not `type`) is used consistently from Task 3 onward — the persona document in
  Task 4 is the only place that still says `type` in an example, corrected in Step 9 of that task.
