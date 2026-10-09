# Explanation-First Script Stage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the facts-only script writer with an explain -> verify -> script pipeline that produces coherent, claim-verified explainer scripts of any length (30 s floor, no ceiling).

**Architecture:** Two new stages (`explain`, `verify_claims`) sit between `facts` and `script`. EXPLAIN builds a causal explanation plan from the full article; VERIFY classifies every claim and checks it against the blog quote or retrieved web passages (dropping what it cannot support, never guessing); SCRIPT narrates the verified plan and a different-family critic re-scores the final script. A stage that cannot produce shippable content raises `HoldForReview`, which stops the run in a non-publishing `hold_for_review` state instead of crashing or padding.

**Tech Stack:** Python 3.12, pytest, `jsonschema`, `requests` + `beautifulsoup4` (already used by ingest), `duckduckgo-search`/`ddgs` (already in `requirements.txt`), Ollama (cloud models via the Python SDK), TypeScript/vitest for the harness tool list.

**Spec:** `docs/superpowers/specs/2026-10-08-explanation-first-script-design.md`

## Global Constraints

- Run all Python tests from `_shorts_engine_impl/` (`cd _shorts_engine_impl && python -m pytest tests/shorts_engine/<file> -v`); imports are `from shorts_engine import ...`.
- 30 s total narration floor stays (`config.TOTAL_MIN_S = 30.0`); **no ceiling**; no filler top-up; no per-purpose word budgets.
- Minimum **3 verified steps** (`config.MIN_VERIFIED_STEPS = 3`), no maximum.
- Every number in a claim or narration must trace to a blog quote or a retrieved passage **and** carry a unit that is known or defined in the step's `terms` **and** appears in the source text.
- Empty or failed web search never stops the run: the claim is dropped (verdict `unsupported`, reason `no_retrieval`).
- Writer/planner: `gemma4:31b-cloud` (`config.SMART_TEXT_MODEL`). Verifier and critic must be a **different model family** from the writer, enforced in code.
- Model names are config, never hardcoded in stage code (`config.model_for_role`).
- No silent model fallback: an unreachable model raises `EngineLLMError` (existing `text_llm` behaviour).
- `hold_for_review` is a manifest status string (like `"failed"`); it is not added to `STATUS_ORDER`.
- Beat names/purposes stay compatible with downstream stages: first beat is named `hook` (`package.py` looks it up by name), last beat has purpose `cta` with exactly one differentiator id.
- `shotlist.py` and its test currently carry **uncommitted work in progress** from the user. Task 9 touches them; do not start Task 9 until the user confirms that WIP is committed, and never stage those files together with unrelated changes.
- Commit messages end with: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`

## Review Focus

Inputs the spec implies but the task tests do not obviously exercise; each has a pinning test in the owning task.

1. **Article with no explainable mechanism** (e.g. a pure price-list post): the planner returns <3 steps or VERIFY drops below 3 -> run holds with reasons, no crash. (Task 6, test `test_holds_when_fewer_than_three_steps_survive`.)
2. **Search backend down / rate-limited**: `web_search` and `fetch_text` must swallow errors and return empty results. (Task 4 tests.)
3. **Planner invents a unit** (`425 dS/m` when the blog only says `425 EC`): rejected because the unit is not in the source text. (Task 3, `test_unit_must_appear_in_source`.)
4. **Writer returns step ids in a different order or skips one**: script build rejects with a clear error and retries. (Task 7, `test_build_beats_rejects_step_id_mismatch`.)
5. **Configured verifier equals the writer's family** (env override mistake): refuse to run rather than self-grade. (Task 1, `test_independence_check_rejects_same_family`.)

---

## File Structure

| File | Responsibility |
|---|---|
| `shorts_engine/config.py` (modify) | Model-role map, `model_for_role`, family/independence helpers, new constants |
| `shorts_engine/llm/text_llm.py` (modify) | `role=` parameter selecting the per-role model client |
| `shorts_engine/errors.py` (modify) | `HoldForReview` |
| `shorts_engine/runner.py`, `stage_cli.py` (modify) | Handle `HoldForReview` without crashing or publishing |
| `shorts_engine/explanation.py` (create) | Pure logic: plan schema, structure validation, number+unit gate |
| `shorts_engine/retrieval/__init__.py`, `search.py`, `fetch.py`, `passages.py` (create) | Text web search, page fetch, passage extraction, ranked cached `Retriever` |
| `shorts_engine/stages/explain.py` (create) | EXPLAIN stage |
| `shorts_engine/stages/verify_claims.py` (create) | VERIFY stage: classification, verification, repair, hold |
| `shorts_engine/stages/script.py` (rewrite) | Narrate verified plan; keep number/banned/card/differentiator gates; final-script critic loop |
| `shorts_engine/manifest.py`, `cli.py` (modify) | Two new statuses/stages, `--until` choices |
| `harness/packages/tool-shorts-stage/src/index.ts`, `harness/system_prompt.md`, `harness/cordis.yml` (modify) | New tools, stage chain, hold rule |
| `shorts_engine/review/plan_report.py` (create), `cli.py` (modify) | `--print-plan` dry-run report |
| `shorts_engine/stages/shotlist.py` (modify, gated) | Forward `visual_intent` to the shot payload |
| `shorts_engine/stages/package.py` (modify) | `format` label (short/long) from duration |
| `scripts/eval_verifier.py`, `scripts/smoke_models.py`, `tests/shorts_engine/fixtures/claims_eval.json` (create) | Verifier bake-off and model reachability check |

---

### Task 1: Model roles, independence guard, `role=` in text_llm

**Files:**
- Modify: `_shorts_engine_impl/shorts_engine/config.py` (imports at top; new block after the `LLM behavior` section)
- Modify: `_shorts_engine_impl/shorts_engine/llm/text_llm.py` (`generate_schema_json` signature + client selection)
- Test: `_shorts_engine_impl/tests/shorts_engine/test_model_roles.py` (create)

**Interfaces:**
- Produces: `config.MODEL_ROLES: dict[str,str]`, `config.model_for_role(role: str) -> str`, `config.model_family(model: str) -> str`, `config.check_role_independence(role: str) -> None` (raises `EngineConfigError`), `config.MIN_VERIFIED_STEPS`, `config.PLAN_MAX_REPAIR_ROUNDS`, `config.SCRIPT_MAX_REWRITES`, `config.RETRIEVAL_*`, `config.SHORT_FORM_MAX_S`; `text_llm.generate_schema_json(..., role: str | None = None)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_model_roles.py
from __future__ import annotations

import pytest

from shorts_engine import config
from shorts_engine.errors import EngineConfigError
from shorts_engine.llm import text_llm


def test_every_role_has_a_model():
    for role in ("planner", "writer", "verifier", "critic"):
        assert config.model_for_role(role)


def test_unknown_role_raises():
    with pytest.raises(EngineConfigError):
        config.model_for_role("nope")


def test_env_override(monkeypatch):
    monkeypatch.setenv("HRSU_MODEL_VERIFIER", "nemotron-3-ultra:cloud")
    assert config.model_for_role("verifier") == "nemotron-3-ultra:cloud"


def test_model_family():
    assert config.model_family("gemma4:31b-cloud") == "gemma"
    assert config.model_family("glm-5.2:cloud") == "glm"
    assert config.model_family("nemotron-3-ultra:cloud") == "nemotron"


def test_default_verifier_and_critic_differ_from_writer_family():
    assert config.model_family(config.model_for_role("verifier")) != config.model_family(
        config.model_for_role("writer"))
    config.check_role_independence("verifier")
    config.check_role_independence("critic")


def test_independence_check_rejects_same_family(monkeypatch):
    monkeypatch.setenv("HRSU_MODEL_VERIFIER", "gemma3:4b")
    with pytest.raises(EngineConfigError, match="same model family"):
        config.check_role_independence("verifier")


def test_generate_schema_json_uses_role_client(monkeypatch):
    seen = {}

    class FakeClient:
        def generate_json(self, prompt, system=None, retries=1):
            return {"ok": True}

    def fake_role_client(role):
        seen["role"] = role
        return FakeClient()

    monkeypatch.setattr(text_llm, "_get_role_client", fake_role_client)
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}},
              "required": ["ok"]}
    out = text_llm.generate_schema_json("p", "s", schema, role="verifier")
    assert out == {"ok": True}
    assert seen["role"] == "verifier"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd _shorts_engine_impl && python -m pytest tests/shorts_engine/test_model_roles.py -v`
Expected: FAIL (`AttributeError: module 'shorts_engine.config' has no attribute 'model_for_role'`).

- [ ] **Step 3: Implement config additions**

In `config.py` add `import os` and `import re` next to `import logging`, add `from shorts_engine.errors import EngineConfigError` after the existing imports, then add this block after the `# ── LLM behavior` section:

```python
# ── Model roles (spec 2026-10-08 §8) ───────────────────────────────────────
# Planner/writer = the user's choice (Gemma). Verifier and critic MUST be a
# different model family from the writer (a model checking its own claims
# agrees with itself); check_role_independence() enforces that at call time.
# The verifier default is confirmed by scripts/eval_verifier.py (Task 11).
# Any role can be overridden with env HRSU_MODEL_<ROLE> (e.g. HRSU_MODEL_VERIFIER).
MODEL_ROLES: dict[str, str] = {
    "planner": SMART_TEXT_MODEL,
    "writer": SMART_TEXT_MODEL,
    "verifier": "glm-5.2:cloud",
    "critic": "glm-5.2:cloud",
}
_INDEPENDENT_ROLES = ("verifier", "critic")


def model_for_role(role: str) -> str:
    """Model name for a role; env HRSU_MODEL_<ROLE> overrides the default."""
    override = os.environ.get(f"HRSU_MODEL_{role.upper()}")
    if override:
        return override
    try:
        return MODEL_ROLES[role]
    except KeyError:
        raise EngineConfigError(
            f"unknown model role {role!r}; known: {sorted(MODEL_ROLES)}") from None


def model_family(model: str) -> str:
    """'gemma4:31b-cloud' -> 'gemma'; 'glm-5.2:cloud' -> 'glm'."""
    return re.split(r"[-:0-9._]", model.strip().lower(), maxsplit=1)[0]


def check_role_independence(role: str) -> None:
    """Raise EngineConfigError if a verifier/critic shares the writer's family."""
    if role not in _INDEPENDENT_ROLES:
        return
    if model_family(model_for_role(role)) == model_family(model_for_role("writer")):
        raise EngineConfigError(
            f"role {role!r} uses the same model family as the writer "
            f"({model_for_role(role)!r} vs {model_for_role('writer')!r}); a model must "
            f"not grade its own work")


# ── Explanation plan / verification (spec 2026-10-08 §5-7) ─────────────────
MIN_VERIFIED_STEPS = 3
PLAN_MAX_REPAIR_ROUNDS = 2
SCRIPT_MAX_REWRITES = 2          # critic-triggered rewrites before holding
RETRIEVAL_MAX_RESULTS = 5
RETRIEVAL_MAX_FETCHES = 6
RETRIEVAL_MAX_PASSAGES = 3
RETRIEVAL_FETCH_TIMEOUT_S = 10
RETRIEVAL_SEARCH_RETRIES = 2
# YouTube Shorts accepts up to 3 minutes (verify against current YouTube rules);
# anything longer is labelled long-form at packaging time.
SHORT_FORM_MAX_S = 180.0
```

- [ ] **Step 4: Implement text_llm role support**

In `text_llm.py`, add after `_get_smart_client`:

```python
def _get_role_client(role: str):
    """OllamaClient for a configured role. Cloud models go through the Python SDK
    (they reject /api/generate, see OllamaClient._generate_via_sdk)."""
    from shorts_engine import config
    config.check_role_independence(role)
    from video_agent.ollama_client import OllamaClient
    model = config.model_for_role(role)
    client = OllamaClient(model=model)
    if "cloud" in model:
        client.generate = lambda prompt, system=None, **_kw: client._generate_via_sdk(
            prompt, system)
    return client
```

Change the signature of `generate_schema_json` to add `role: str | None = None` after `local_only`, document it in the docstring (`role: Config role ("planner"|"writer"|"verifier"|"critic") selecting the model; None keeps the smart-model default`), and replace the client selection block with:

```python
    if client_factory is not None:
        client = client_factory()
    elif role is not None:
        client = _get_role_client(role)
    else:
        client = _get_smart_client()
```

- [ ] **Step 5: Run to verify pass**

Run: `cd _shorts_engine_impl && python -m pytest tests/shorts_engine/test_model_roles.py tests/shorts_engine/test_text_llm.py tests/shorts_engine/test_config.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add _shorts_engine_impl/shorts_engine/config.py _shorts_engine_impl/shorts_engine/llm/text_llm.py _shorts_engine_impl/tests/shorts_engine/test_model_roles.py
git commit -m "feat: per-role model config with writer/verifier independence guard

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `HoldForReview` in errors, runner and stage CLI

**Files:**
- Modify: `_shorts_engine_impl/shorts_engine/errors.py`
- Modify: `_shorts_engine_impl/shorts_engine/runner.py` (the `try/except` inside the stage loop)
- Modify: `_shorts_engine_impl/shorts_engine/stage_cli.py` (`cmd_run_stage`)
- Test: `_shorts_engine_impl/tests/shorts_engine/test_hold_for_review.py` (create)

**Interfaces:**
- Produces: `errors.HoldForReview(reasons: list[str])` with `.reasons`; runner returns the manifest with `status == "hold_for_review"` and `error == "<stage>: <reasons joined>"`; `cmd_run_stage` prints one JSON line `{"status": "hold", "message": ..., "reasons": [...]}` and returns `0`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_hold_for_review.py
from __future__ import annotations

import argparse
import json

from shorts_engine import runner, stage_cli
from shorts_engine.errors import EngineError, HoldForReview
from shorts_engine.manifest import RunManifest


def test_hold_for_review_is_an_engine_error_with_reasons():
    exc = HoldForReview(["a", "b"])
    assert isinstance(exc, EngineError)
    assert exc.reasons == ["a", "b"]
    assert str(exc) == "a; b"


def test_runner_holds_without_raising_and_skips_later_stages(tmp_path):
    ran = []

    def first(ctx):
        ran.append("first")
        return {}

    def holds(ctx):
        ran.append("holds")
        raise HoldForReview(["only 2 steps survived"])

    def never(ctx):
        ran.append("never")
        return {}

    manifest = runner.run(
        "https://example.com/blog/x",
        [("first", "ingested", first), ("holds", "facts", holds), ("never", "scripted", never)],
        tmp_path,
    )
    assert ran == ["first", "holds"]
    assert manifest.status == "hold_for_review"
    assert manifest.last_ok_status == "ingested"
    assert "only 2 steps survived" in manifest.error
    assert RunManifest.load(manifest.workspace).status == "hold_for_review"


def test_stage_cli_reports_hold_and_exits_zero(tmp_path, monkeypatch, capsys):
    m = RunManifest.create("https://example.com/blog/x", tmp_path)
    m.checkpoint("ingested")

    def holds(ctx):
        raise HoldForReview(["thin article"])

    monkeypatch.setitem(stage_cli.STAGE_FUNCTIONS, "facts", holds)
    args = argparse.Namespace(stage_name="facts", workspace=m.workspace, local_only=False,
                              html_override=None, torture=False, publish=False)
    assert stage_cli.cmd_run_stage(args) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["status"] == "hold"
    assert out["reasons"] == ["thin article"]
    reloaded = RunManifest.load(m.workspace)
    assert reloaded.status == "hold_for_review"
    assert reloaded.last_ok_status == "ingested"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd _shorts_engine_impl && python -m pytest tests/shorts_engine/test_hold_for_review.py -v`
Expected: FAIL (`ImportError: cannot import name 'HoldForReview'`).

- [ ] **Step 3: Implement**

`errors.py` — add after `GateFailure` and list it in the module docstring hierarchy:

```python
class HoldForReview(EngineError):
    """A stage stopped on purpose: nothing crashed, but the content cannot ship
    (too few verified steps, final script below the quality bar...). The run
    ends in the non-publishing status "hold_for_review" with these reasons.

    Args:
        reasons: Human-readable reasons; `str(exc)` joins them with "; ".
    """

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        super().__init__("; ".join(reasons))
```

`runner.py` — change the import to `from shorts_engine.errors import EngineError, HoldForReview` and add this `except` **before** `except Exception as exc:` in the stage loop:

```python
        except HoldForReview as hold:
            manifest.status = "hold_for_review"
            manifest.error = f"{stage_name}: {hold}"
            manifest.save()
            logger.warning(
                f"[{manifest.run_id}] Stage '{stage_name}' held for review: {hold}"
            )
            return manifest
```

`stage_cli.py` — change `from shorts_engine.errors import EngineError` to `from shorts_engine.errors import EngineError, HoldForReview` and in `cmd_run_stage` add before `except Exception as exc:`:

```python
    except HoldForReview as hold:
        manifest.status = "hold_for_review"
        manifest.error = f"{args.stage_name}: {hold}"
        manifest.save()
        print(json.dumps({"status": "hold", "message": str(hold), "reasons": hold.reasons}))
        return 0
```

- [ ] **Step 4: Run to verify pass**

Run: `cd _shorts_engine_impl && python -m pytest tests/shorts_engine/test_hold_for_review.py tests/shorts_engine/test_runner.py tests/shorts_engine/test_stage_cli.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add _shorts_engine_impl/shorts_engine/errors.py _shorts_engine_impl/shorts_engine/runner.py _shorts_engine_impl/shorts_engine/stage_cli.py _shorts_engine_impl/tests/shorts_engine/test_hold_for_review.py
git commit -m "feat: HoldForReview stops a run in hold_for_review without crashing

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `explanation.py` — plan schema, structure validation, number+unit gate

**Files:**
- Create: `_shorts_engine_impl/shorts_engine/explanation.py`
- Test: `_shorts_engine_impl/tests/shorts_engine/test_explanation.py` (create)

**Interfaces:**
- Consumes: `stages.facts.locate_verbatim(quote, canonical) -> int | None`, `stages.facts.normalize_for_match(s) -> str`, `brand.BrandFacts`.
- Produces: `CLAIM_KINDS`, `VERDICTS`, `PLAN_SCHEMA`, `CLAIM_ITEM_SCHEMA`, `KNOWN_UNITS`, `has_number(text) -> bool`, `gate_claim_numbers(text, source, terms=None) -> list[str]`, `validate_plan(plan, canonical, brand) -> list[str]`, `finalize_claim(raw) -> dict`, `to_plan_document(raw, attempts) -> dict`, `supported_claims(step) -> list[dict]`.

A finalized claim: `{"id","text","kind","support":{"type","quote","url"},"verdict":None,"needs_number":bool}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_explanation.py
from __future__ import annotations

import copy

import jsonschema

from shorts_engine import explanation as ex
from shorts_engine.brand import BrandFacts

CANONICAL = (
    "In sandy soils, electrical conductivity (EC) peaks at 425 during infiltration. "
    "Engineers often use a 20% calcium carbonate and 80% river sand mixture to model "
    "desert soil. Calcium nitrate dissolves readily in water."
)
BRAND = BrandFacts(company="HRSU", domain="hrsuindore.com", tagline="t",
                   differentiators=[{"id": "b_purity", "text": "High-purity powder"}],
                   cta_lines=["Visit hrsuindore.com"], banned_claims=[])


def _claim(cid, text, kind="blog_stated", quote=""):
    return {"id": cid, "text": text, "kind": kind, "quote": quote}


def _step(sid, claims, entities=("sand",)):
    return {"step_id": sid, "claim_text": f"goal {sid}", "claims": claims, "terms": [],
            "visual_intent": {"entities": list(entities), "relationship": "r", "quantity": "q"}}


def _plan():
    return {
        "question": "Why does sand drain nutrients?",
        "steps": [
            _step("s1", [_claim("c1", "Engineers model desert soil with a sand mixture.",
                                quote="a 20% calcium carbonate and 80% river sand mixture")]),
            _step("s2", [_claim("c2", "Calcium nitrate dissolves readily in water.",
                                kind="external_fact")]),
            _step("s3", [_claim("c3", "So nutrients move with the water.", kind="reasoning")]),
        ],
        "payoff": {"takeaway": "Pick a consistent powder.", "differentiator_id": "b_purity"},
    }


class TestSchema:
    def test_valid_plan_matches_schema(self):
        jsonschema.validate(_plan(), ex.PLAN_SCHEMA)

    def test_unknown_kind_is_rejected(self):
        p = _plan()
        p["steps"][0]["claims"][0]["kind"] = "guess"
        try:
            jsonschema.validate(p, ex.PLAN_SCHEMA)
        except jsonschema.ValidationError:
            return
        raise AssertionError("expected ValidationError")


class TestGateClaimNumbers:
    def test_number_with_known_unit_in_source_passes(self):
        assert ex.gate_claim_numbers("Mix is 20% carbonate", "a 20% calcium carbonate mix") == []

    def test_number_without_unit_fails(self):
        errs = ex.gate_claim_numbers("EC peaked at 425", "EC peaking at 425")
        assert any("unit" in e and "425" in e for e in errs)

    def test_unknown_unit_word_fails(self):
        errs = ex.gate_claim_numbers("EC peaked at 425 EC", "EC peaking at 425 EC")
        assert any("unit" in e for e in errs)

    def test_unit_must_appear_in_source(self):
        errs = ex.gate_claim_numbers("EC peaked at 425 dS/m", "EC peaking at 425")
        assert any("dS/m" in e or "ds/m" in e for e in errs)

    def test_untraced_number_fails(self):
        errs = ex.gate_claim_numbers("Dose is 5 kg", "the dose is 3 kg")
        assert any("'5'" in e and "trace" in e for e in errs)

    def test_term_defined_unit_is_allowed_when_in_source(self):
        terms = [{"term": "EC", "definition": "x", "unit": "mS/cm"}]
        assert ex.gate_claim_numbers("EC is 4 mS/cm", "EC is 4 mS/cm here", terms) == []

    def test_range_checks_unit_on_last_number(self):
        assert ex.gate_claim_numbers("Use 1.5 to 3 kg", "dose of 1.5 to 3 kg per m3") == []

    def test_years_ph_and_chemical_names_are_not_flagged(self):
        assert ex.gate_claim_numbers("In 2026 pH 7 water with H2S", "2026 pH 7 H2S") == []

    def test_no_numbers_no_errors(self):
        assert ex.gate_claim_numbers("Water dissolves salt.", "") == []


class TestValidatePlan:
    def test_valid_plan_has_no_errors(self):
        assert ex.validate_plan(_plan(), CANONICAL, BRAND) == []

    def test_fewer_than_min_steps(self):
        p = _plan()
        p["steps"] = p["steps"][:2]
        assert any("structure" in e and "3" in e for e in ex.validate_plan(p, CANONICAL, BRAND))

    def test_blog_stated_quote_must_exist_verbatim(self):
        p = _plan()
        p["steps"][0]["claims"][0]["quote"] = "a 30% mixture that is not in the article"
        assert any("c1" in e and "verbatim" in e for e in ex.validate_plan(p, CANONICAL, BRAND))

    def test_duplicate_step_and_claim_ids(self):
        p = _plan()
        p["steps"][1]["step_id"] = "s1"
        p["steps"][2]["claims"][0]["id"] = "c1"
        errs = ex.validate_plan(p, CANONICAL, BRAND)
        assert any("duplicate step" in e for e in errs)
        assert any("duplicate claim" in e for e in errs)

    def test_illustrative_claim_may_not_contain_numbers(self):
        p = _plan()
        p["steps"][2]["claims"][0] = _claim("c3", "Like 3 sugar cubes in tea", kind="illustrative")
        assert any("c3" in e and "illustrative" in e for e in ex.validate_plan(p, CANONICAL, BRAND))

    def test_blog_stated_number_without_unit_is_rejected_at_plan_time(self):
        p = _plan()
        p["steps"][0]["claims"][0] = _claim(
            "c1", "EC peaks at 425 in sand.",
            quote="electrical conductivity (EC) peaks at 425 during infiltration")
        assert any("c1" in e and "unit" in e for e in ex.validate_plan(p, CANONICAL, BRAND))

    def test_payoff_differentiator_must_exist(self):
        p = _plan()
        p["payoff"]["differentiator_id"] = "b_nope"
        assert any("differentiator" in e for e in ex.validate_plan(p, CANONICAL, BRAND))

    def test_step_needs_visual_entities(self):
        p = _plan()
        p["steps"][0]["visual_intent"]["entities"] = []
        assert any("visual_intent" in e for e in ex.validate_plan(p, CANONICAL, BRAND))

    def test_question_and_takeaway_may_not_contain_numbers(self):
        p = _plan()
        p["question"] = "Why do 3 things happen?"
        assert any("question" in e for e in ex.validate_plan(p, CANONICAL, BRAND))


class TestDocumentHelpers:
    def test_finalize_claim_shape(self):
        c = ex.finalize_claim(_claim("c1", "Mix is 20% carbonate", quote="q"))
        assert c == {"id": "c1", "text": "Mix is 20% carbonate", "kind": "blog_stated",
                     "support": {"type": "blog_quote", "quote": "q", "url": None},
                     "verdict": None, "needs_number": True}

    def test_to_plan_document_finalizes_every_claim_and_does_not_mutate_input(self):
        raw = _plan()
        before = copy.deepcopy(raw)
        doc = ex.to_plan_document(raw, attempts=2)
        assert raw == before
        assert doc["status"] == "planned" and doc["attempts"] == 2
        assert doc["steps"][1]["claims"][0]["support"]["type"] is None
        assert doc["dropped_claims"] == [] and doc["dropped_steps"] == []

    def test_supported_claims_filters_by_verdict(self):
        step = {"claims": [{"id": "a", "verdict": "supported"},
                           {"id": "b", "verdict": "unsupported"},
                           {"id": "c", "verdict": None}]}
        assert [c["id"] for c in ex.supported_claims(step)] == ["a"]
```

- [ ] **Step 2: Run to verify failure**

Run: `cd _shorts_engine_impl && python -m pytest tests/shorts_engine/test_explanation.py -v`
Expected: FAIL (`ModuleNotFoundError: shorts_engine.explanation`).

- [ ] **Step 3: Implement**

```python
# shorts_engine/explanation.py
"""Pure (no LLM, no I/O) logic for the explanation plan.

Provides the planner's JSON schema, structural validation, the claim-kind
rules, and the number+unit gate that extends the never-unverified invariant
from "every number traces to a source" to "every number also carries a unit
that is known (or defined in the step's terms) AND appears in the source".
That last rule is what stops a model inventing "425 dS/m" from a blog that
only says "425 EC".
"""
from __future__ import annotations

import copy
import re

from shorts_engine import config
from shorts_engine.brand import BrandFacts
from shorts_engine.stages.facts import locate_verbatim, normalize_for_match

CLAIM_KINDS = ("blog_stated", "external_fact", "reasoning", "illustrative")
VERDICTS = ("supported", "contradicted", "unsupported")

CLAIM_ITEM_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "text": {"type": "string"},
        "kind": {"enum": list(CLAIM_KINDS)},
        "quote": {"type": "string"},
    },
    "required": ["id", "text", "kind", "quote"],
    "additionalProperties": False,
}

_STEP_ITEM_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "step_id": {"type": "string"},
        "claim_text": {"type": "string"},
        "claims": {"type": "array", "minItems": 1, "items": CLAIM_ITEM_SCHEMA},
        "terms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"term": {"type": "string"},
                               "definition": {"type": "string"},
                               "unit": {"type": "string"}},
                "required": ["term", "definition", "unit"],
                "additionalProperties": False,
            },
        },
        "visual_intent": {
            "type": "object",
            "properties": {"entities": {"type": "array", "items": {"type": "string"}},
                           "relationship": {"type": "string"},
                           "quantity": {"type": "string"}},
            "required": ["entities", "relationship", "quantity"],
            "additionalProperties": False,
        },
    },
    "required": ["step_id", "claim_text", "claims", "terms", "visual_intent"],
    "additionalProperties": False,
}

PLAN_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "question": {"type": "string"},
        "steps": {"type": "array", "minItems": config.MIN_VERIFIED_STEPS,
                  "items": _STEP_ITEM_SCHEMA},
        "payoff": {
            "type": "object",
            "properties": {"takeaway": {"type": "string"},
                           "differentiator_id": {"type": "string"}},
            "required": ["takeaway", "differentiator_id"],
            "additionalProperties": False,
        },
    },
    "required": ["question", "steps", "payoff"],
    "additionalProperties": False,
}

# ── number + unit gate ──────────────────────────────────────────────────────
KNOWN_UNITS = frozenset({
    "%", "percent", "ppm", "ppb", "mg/l", "g/l", "kg/m3", "kg/m³", "g/m3", "g/m³",
    "mg/kg", "g/kg", "kg", "mg", "g", "t", "tonnes", "tons", "l", "ml", "m3", "m³",
    "mm", "cm", "m", "km", "°c", "°f", "ds/m", "ms/cm", "µs/cm", "us/cm", "bar",
    "mpa", "kpa", "psi", "mol/l", "mmol/l", "h", "hr", "hours", "hour", "min",
    "minutes", "minute", "s", "seconds", "days", "day", "weeks", "months", "years",
    "x", "times", "fold", "steps", "stages", "cycles", "batches", "samples",
})

_NUMBER_RE = re.compile(
    r"(?<![\w.])(?P<num>\d[\d,]*(?:\.\d+)?)(?P<gap>\s*)"
    r"(?P<unit>%|°?[A-Za-zµ]+(?:/[A-Za-zµ]+)?[²³23]?)?"
)
_YEAR_RE = re.compile(r"(?:19|20)\d\d")
_RANGE_TAIL_RE = re.compile(r"\s*(?:to|-|–|and)\s*\d")


def has_number(text: str) -> bool:
    return bool(_NUMBER_RE.search(text or ""))


def gate_claim_numbers(text: str, source: str, terms: list[dict] | None = None) -> list[str]:
    """Errors for numbers in `text` that do not trace to `source`, or lack a unit.

    A number passes when (a) it appears as a standalone number in `source`, and
    (b) it is followed by a unit that is known (KNOWN_UNITS) or defined in
    `terms`, and (c) that unit string appears in `source` ("%" also accepts
    "percent"). Years, numbers after "pH"/"step"/"stage"/"phase", and numbers
    that open a range ("1.5 to 3 kg") skip the unit check (the range's last
    number carries it) but still must trace.
    """
    pool = normalize_for_match(source).replace(",", "")
    term_units = {t.get("unit", "").strip().lower() for t in (terms or [])
                  if t.get("unit", "").strip()}
    errs: list[str] = []
    for m in _NUMBER_RE.finditer(text or ""):
        num = m.group("num").replace(",", "")
        if not re.search(rf"(?<![\d.]){re.escape(num)}(?![\d])", pool):
            errs.append(f"number {num!r} does not trace to the source")
            continue
        before = text[:m.start()].lower().rstrip()
        if (_YEAR_RE.fullmatch(num) or before.endswith(("ph", "step", "stage", "phase"))
                or _RANGE_TAIL_RE.match(text[m.end("num"):])):
            continue
        unit = (m.group("unit") or "").lower()
        if not unit:
            errs.append(f"number {num!r} has no unit")
        elif unit not in KNOWN_UNITS and unit not in term_units:
            errs.append(f"number {num!r} has unknown unit {unit!r} (not a known unit and "
                        f"not defined in the step's terms)")
        elif unit in ("%", "percent"):
            if "%" not in pool and "percent" not in pool:
                errs.append(f"unit {unit!r} for {num!r} does not appear in the source")
        elif unit not in pool:
            errs.append(f"unit {unit!r} for {num!r} does not appear in the source")
    return errs


# ── structural validation ───────────────────────────────────────────────────
def validate_plan(plan: dict, canonical: str, brand: BrandFacts) -> list[str]:
    """Deterministic checks on a planner draft. Returns error strings (empty = ok)."""
    errs: list[str] = []
    steps = plan.get("steps", [])
    if len(steps) < config.MIN_VERIFIED_STEPS:
        errs.append(f"structure: need at least {config.MIN_VERIFIED_STEPS} steps, "
                    f"got {len(steps)}")
    if has_number(plan.get("question", "")):
        errs.append("question: must not contain numbers")
    if has_number(plan.get("payoff", {}).get("takeaway", "")):
        errs.append("payoff: takeaway must not contain numbers")
    diff_ids = {d["id"] for d in brand.differentiators}
    if plan.get("payoff", {}).get("differentiator_id") not in diff_ids:
        errs.append(f"payoff: differentiator_id must be one of {sorted(diff_ids)}")

    seen_steps: set[str] = set()
    seen_claims: set[str] = set()
    for s in steps:
        sid = s.get("step_id")
        if sid in seen_steps:
            errs.append(f"structure: duplicate step id {sid!r}")
        seen_steps.add(sid)
        if not s.get("claims"):
            errs.append(f"step[{sid}]: needs at least one claim")
        if not s.get("visual_intent", {}).get("entities"):
            errs.append(f"step[{sid}]: visual_intent.entities must not be empty")
        for c in s.get("claims", []):
            cid = c.get("id")
            if cid in seen_claims:
                errs.append(f"structure: duplicate claim id {cid!r}")
            seen_claims.add(cid)
            kind = c.get("kind")
            if kind not in CLAIM_KINDS:
                errs.append(f"claim[{cid}]: unknown kind {kind!r}")
            elif kind == "blog_stated":
                quote = c.get("quote", "")
                if not quote or locate_verbatim(quote, canonical) is None:
                    errs.append(f"claim[{cid}]: blog_stated quote not found verbatim "
                                f"in the article")
                else:
                    errs += [f"claim[{cid}]: {e}"
                             for e in gate_claim_numbers(c["text"], quote, s.get("terms"))]
            elif kind == "illustrative" and has_number(c.get("text", "")):
                errs.append(f"claim[{cid}]: illustrative claims must not contain numbers")
    return errs


# ── plan document helpers ───────────────────────────────────────────────────
def finalize_claim(raw: dict) -> dict:
    """Planner claim -> stored claim (adds support/verdict/needs_number)."""
    return {
        "id": raw["id"],
        "text": raw["text"],
        "kind": raw["kind"],
        "support": {"type": "blog_quote" if raw["kind"] == "blog_stated" else None,
                    "quote": raw.get("quote", ""), "url": None},
        "verdict": None,
        "needs_number": has_number(raw["text"]),
    }


def to_plan_document(raw: dict, attempts: int) -> dict:
    """Planner output -> explanation_plan.json document (input is not mutated)."""
    doc = copy.deepcopy(raw)
    for step in doc["steps"]:
        step["claims"] = [finalize_claim(c) for c in step["claims"]]
    doc["status"] = "planned"
    doc["attempts"] = attempts
    doc["dropped_claims"] = []
    doc["dropped_steps"] = []
    doc["hold_reasons"] = []
    return doc


def supported_claims(step: dict) -> list[dict]:
    return [c for c in step.get("claims", []) if c.get("verdict") == "supported"]
```

- [ ] **Step 4: Run to verify pass**

Run: `cd _shorts_engine_impl && python -m pytest tests/shorts_engine/test_explanation.py -v`
Expected: all PASS. (If `test_years_ph_and_chemical_names_are_not_flagged` fails on "H2S", the `(?<![\w.])` look-behind is missing from `_NUMBER_RE`.)

- [ ] **Step 5: Commit**

```bash
git add _shorts_engine_impl/shorts_engine/explanation.py _shorts_engine_impl/tests/shorts_engine/test_explanation.py
git commit -m "feat: explanation plan schema, validation and number+unit gate

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Retrieval package (text search, fetch, passages, ranked cached Retriever)

**Files:**
- Create: `_shorts_engine_impl/shorts_engine/retrieval/__init__.py`, `search.py`, `fetch.py`, `passages.py`
- Test: `_shorts_engine_impl/tests/shorts_engine/test_retrieval.py` (create)

**Interfaces:**
- Consumes: `config.RETRIEVAL_*`, `config.PAPER_DOMAINS`, `config.STANDARD_DOMAINS`, `stages.facts.split_sentences`, `normalize_for_match`.
- Produces: `search.SearchHit(url,title,snippet)`, `search.web_search(query, max_results=..., *, ddgs_factory=None, sleep=time.sleep) -> list[SearchHit]` (never raises); `fetch.fetch_text(url, *, get=None, timeout=...) -> str` (never raises; `""` on any failure/non-HTML); `passages.best_passages(text, claim, k) -> list[str]`; `retrieval.Passage(url,text,kind)`, `retrieval.source_kind(url) -> str`, `retrieval.Retriever(citation_urls, *, search_fn=None, fetch_fn=None).retrieve(claim_text) -> list[Passage]` (never raises, cached per claim).

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_retrieval.py
from __future__ import annotations

from types import SimpleNamespace

from shorts_engine.retrieval import Passage, Retriever, source_kind
from shorts_engine.retrieval.fetch import fetch_text
from shorts_engine.retrieval.passages import best_passages
from shorts_engine.retrieval.search import SearchHit, web_search


class FakeDDGS:
    def __init__(self, results=None, fail_times=0):
        self.results, self.fail_times, self.calls = results or [], fail_times, 0

    def text(self, query, max_results=5):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("rate limited")
        return self.results


class TestWebSearch:
    def test_maps_results_to_hits(self):
        ddgs = FakeDDGS([{"href": "https://a.gov/x", "title": "T", "body": "B"}])
        hits = web_search("q", ddgs_factory=lambda: ddgs, sleep=lambda s: None)
        assert hits == [SearchHit("https://a.gov/x", "T", "B")]

    def test_retries_then_succeeds(self):
        ddgs = FakeDDGS([{"href": "https://a.gov/x", "title": "T", "body": "B"}], fail_times=1)
        hits = web_search("q", ddgs_factory=lambda: ddgs, sleep=lambda s: None)
        assert len(hits) == 1 and ddgs.calls == 2

    def test_persistent_failure_returns_empty_not_raise(self):
        ddgs = FakeDDGS(fail_times=99)
        assert web_search("q", ddgs_factory=lambda: ddgs, sleep=lambda s: None) == []

    def test_missing_package_returns_empty(self):
        assert web_search("q", ddgs_factory=lambda: None, sleep=lambda s: None) == []


class FakeResp:
    def __init__(self, text, status=200, ctype="text/html; charset=utf-8"):
        self.text, self.status_code, self.headers = text, status, {"content-type": ctype}


class TestFetchText:
    def test_extracts_paragraph_text_and_drops_scripts(self):
        html = "<html><script>bad()</script><p>Calcium nitrate dissolves.</p><p>Second.</p></html>"
        assert fetch_text("https://x", get=lambda u, timeout: FakeResp(html)) == \
            "Calcium nitrate dissolves. Second."

    def test_non_200_non_html_and_exceptions_return_empty(self):
        assert fetch_text("https://x", get=lambda u, timeout: FakeResp("x", status=404)) == ""
        assert fetch_text("https://x", get=lambda u, timeout: FakeResp("x", ctype="application/pdf")) == ""

        def boom(u, timeout):
            raise TimeoutError("slow")
        assert fetch_text("https://x", get=boom) == ""


class TestBestPassages:
    TEXT = ("The weather was nice. Calcium nitrate dissolves readily in water at room "
            "temperature. Sand drains quickly. Foliar sprays need soluble calcium nitrate.")

    def test_returns_most_relevant_sentences_first(self):
        out = best_passages(self.TEXT, "calcium nitrate dissolves in water", k=2)
        assert "dissolves readily in water" in out[0]
        assert not any("weather" in p for p in out)

    def test_no_overlap_returns_empty(self):
        assert best_passages(self.TEXT, "quantum chromodynamics", k=3) == []


class TestSourceKind:
    def test_kinds(self):
        assert source_kind("https://www.epa.gov/a") == "authoritative"
        assert source_kind("https://pubmed.ncbi.nlm.nih.gov/1") == "authoritative"
        assert source_kind("https://some.edu/p") == "authoritative"
        assert source_kind("https://randomblog.com/p") == "web"


class TestRetriever:
    def _make(self, hits, pages, calls=None):
        def search_fn(q):
            if calls is not None:
                calls.append(q)
            return hits

        return Retriever(["https://blog-cite.org/ref"], search_fn=search_fn,
                         fetch_fn=lambda u: pages.get(u, ""))

    PAGE = "Calcium nitrate dissolves readily in water. It is used in fertigation."

    def test_ranks_blog_citation_then_authoritative_then_web(self):
        hits = [SearchHit("https://randomblog.com/a", "", ""),
                SearchHit("https://www.epa.gov/b", "", "")]
        pages = {u: self.PAGE for u in ("https://blog-cite.org/ref", "https://randomblog.com/a",
                                        "https://www.epa.gov/b")}
        out = self._make(hits, pages).retrieve("calcium nitrate dissolves in water")
        assert [p.kind for p in out][:3] == ["blog_citation", "authoritative", "web"]

    def test_empty_search_and_failed_fetch_return_empty_list(self):
        assert self._make([], {}).retrieve("anything at all") == []

    def test_results_are_cached_per_claim(self):
        calls = []
        r = self._make([SearchHit("https://www.epa.gov/b", "", "")],
                       {"https://www.epa.gov/b": self.PAGE}, calls)
        r.retrieve("calcium nitrate dissolves in water")
        r.retrieve("Calcium nitrate dissolves in water")  # same after normalisation
        assert len(calls) == 1

    def test_never_raises_when_search_fn_blows_up(self):
        def bad(q):
            raise RuntimeError("boom")
        r = Retriever([], search_fn=bad, fetch_fn=lambda u: "")
        assert r.retrieve("x y z") == []
```

- [ ] **Step 2: Run to verify failure**

Run: `cd _shorts_engine_impl && python -m pytest tests/shorts_engine/test_retrieval.py -v`
Expected: FAIL (`ModuleNotFoundError: shorts_engine.retrieval`).

- [ ] **Step 3: Implement**

```python
# shorts_engine/retrieval/search.py
"""Text web search (never raises). The repo's only other DuckDuckGo code is image search."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from shorts_engine import config

logger = logging.getLogger(__name__)

try:
    from ddgs import DDGS  # new package name
except ImportError:
    try:
        from duckduckgo_search import DDGS  # legacy name (in requirements.txt)
    except ImportError:
        DDGS = None


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str
    snippet: str


def _default_factory():
    return DDGS() if DDGS is not None else None


def web_search(query: str, max_results: int = config.RETRIEVAL_MAX_RESULTS, *,
               ddgs_factory=None, sleep=time.sleep) -> list[SearchHit]:
    """Search the web for `query`. Returns [] on any failure after bounded retries."""
    factory = ddgs_factory or _default_factory
    for attempt in range(1, config.RETRIEVAL_SEARCH_RETRIES + 1):
        try:
            client = factory()
            if client is None:
                logger.warning("ddgs not installed; web search unavailable")
                return []
            raw = client.text(query, max_results=max_results)
            return [SearchHit(r.get("href", ""), r.get("title", ""), r.get("body", ""))
                    for r in raw if r.get("href")]
        except Exception as exc:  # network, rate limit, library errors
            logger.warning(f"web_search attempt {attempt} failed for {query!r}: {exc}")
            if attempt < config.RETRIEVAL_SEARCH_RETRIES:
                sleep(config.LLM_RETRY_DELAY_S * attempt)
    return []
```

```python
# shorts_engine/retrieval/fetch.py
"""Fetch a web page and reduce it to readable paragraph text (never raises)."""
from __future__ import annotations

import logging

import requests
from bs4 import BeautifulSoup

from shorts_engine import config

logger = logging.getLogger(__name__)


def fetch_text(url: str, *, get=None, timeout: float = config.RETRIEVAL_FETCH_TIMEOUT_S) -> str:
    """Return the page's paragraph text, or "" on any failure or non-HTML content."""
    get = get or (lambda u, timeout: requests.get(
        u, timeout=timeout, headers={"User-Agent": "Mozilla/5.0 (HRSU explainer fact-check)"}))
    try:
        resp = get(url, timeout=timeout)
        if resp.status_code != 200:
            return ""
        if "html" not in resp.headers.get("content-type", "").lower():
            return ""
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        return " ".join(p.get_text(" ", strip=True) for p in soup.find_all("p")
                        if p.get_text(strip=True))
    except Exception as exc:
        logger.warning(f"fetch_text failed for {url}: {exc}")
        return ""
```

```python
# shorts_engine/retrieval/passages.py
"""Deterministic passage selection: sentences of a page that overlap a claim."""
from __future__ import annotations

import re

from shorts_engine.stages.facts import normalize_for_match, split_sentences

_STOP = frozenset("a an the of in on at to for and or is are was were be been it its this that "
                  "with by as from can may will than then so such not no".split())
_WORD_RE = re.compile(r"[a-z0-9]+")


def _content_words(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(normalize_for_match(text)) if w not in _STOP and len(w) > 2}


def best_passages(text: str, claim: str, k: int) -> list[str]:
    """Top-k sentences by content-word overlap with `claim` (needs >=2 shared words,
    or >=1 when the claim has fewer than 3 content words). Ties keep page order."""
    want = _content_words(claim)
    if not want:
        return []
    need = 1 if len(want) < 3 else 2
    scored = []
    for i, sentence in enumerate(split_sentences(text)):
        overlap = len(want & _content_words(sentence))
        if overlap >= need:
            scored.append((-overlap, i, sentence))
    scored.sort()
    return [s for _o, _i, s in scored[:k]]
```

```python
# shorts_engine/retrieval/__init__.py
"""Retrieval for the claim verifier: ranked, cached, never-raising web evidence."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlparse

from shorts_engine import config
from shorts_engine.retrieval.fetch import fetch_text
from shorts_engine.retrieval.passages import best_passages
from shorts_engine.retrieval.search import SearchHit, web_search
from shorts_engine.stages.facts import normalize_for_match

logger = logging.getLogger(__name__)

__all__ = ["Passage", "Retriever", "SearchHit", "source_kind"]

_AUTHORITATIVE = tuple(config.PAPER_DOMAINS) + tuple(config.STANDARD_DOMAINS)
_RANK = {"blog_citation": 0, "authoritative": 1, "web": 2}


@dataclass(frozen=True)
class Passage:
    url: str
    text: str
    kind: str  # "blog_citation" | "authoritative" | "web"


def source_kind(url: str) -> str:
    """'authoritative' for government/edu/standards/paper hosts, else 'web'."""
    host = (urlparse(url).hostname or "").lower()
    if host.endswith((".gov", ".edu")):
        return "authoritative"
    if any(host == d or host.endswith("." + d) for d in _AUTHORITATIVE):
        return "authoritative"
    return "web"


class Retriever:
    """Finds evidence passages for a claim. Blog citations first, then ranked web hits.

    Never raises: search/fetch failures yield fewer (or zero) passages. Results are
    cached per normalised claim so repair rounds do not repeat searches.
    """

    def __init__(self, citation_urls: list[str], *, search_fn=None, fetch_fn=None) -> None:
        self._citations = list(citation_urls)
        self._search = search_fn or web_search
        self._fetch = fetch_fn or fetch_text
        self._cache: dict[str, list[Passage]] = {}

    def retrieve(self, claim_text: str) -> list[Passage]:
        key = normalize_for_match(claim_text)
        if key not in self._cache:
            try:
                self._cache[key] = self._retrieve(claim_text)
            except Exception as exc:
                logger.warning(f"retrieval failed for {claim_text!r}: {exc}")
                self._cache[key] = []
        return self._cache[key]

    def _retrieve(self, claim_text: str) -> list[Passage]:
        try:
            hits = list(self._search(claim_text))
        except Exception as exc:
            logger.warning(f"search failed for {claim_text!r}: {exc}")
            hits = []
        candidates = [(u, "blog_citation") for u in self._citations]
        ranked = sorted(((h.url, source_kind(h.url)) for h in hits),
                        key=lambda uk: _RANK[uk[1]])
        candidates += [c for c in ranked if c[0] not in self._citations]
        passages: list[Passage] = []
        for url, kind in candidates[:config.RETRIEVAL_MAX_FETCHES]:
            if len(passages) >= config.RETRIEVAL_MAX_PASSAGES:
                break
            text = self._fetch(url)
            for p in best_passages(text, claim_text, 2):
                passages.append(Passage(url, p, kind))
        passages.sort(key=lambda p: _RANK[p.kind])  # stable: keeps page order within a kind
        return passages[:config.RETRIEVAL_MAX_PASSAGES]
```

- [ ] **Step 4: Run to verify pass**

Run: `cd _shorts_engine_impl && python -m pytest tests/shorts_engine/test_retrieval.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add _shorts_engine_impl/shorts_engine/retrieval _shorts_engine_impl/tests/shorts_engine/test_retrieval.py
git commit -m "feat: never-raising text retrieval (search, fetch, passages, ranked cache)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: EXPLAIN stage

**Files:**
- Create: `_shorts_engine_impl/shorts_engine/stages/explain.py`
- Test: `_shorts_engine_impl/tests/shorts_engine/test_explain.py` (create)

**Interfaces:**
- Consumes: `explanation.PLAN_SCHEMA`, `explanation.validate_plan`, `explanation.to_plan_document`, `text_llm.generate_schema_json(..., role="planner")`, `brand.load_brand_facts`, `errors.HoldForReview`, workspace files `canonical.txt`, `post.json`, `factsheet.json`.
- Produces: `explain.run(ctx) -> {"explanation_plan": "explanation_plan.json"}`; `explain._planner_prompt(post_meta, canonical, factsheet, brand, *, errors=None) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_explain.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

from shorts_engine import config
from shorts_engine.errors import HoldForReview
from shorts_engine.manifest import RunManifest
from shorts_engine.runner import StageContext
from shorts_engine.stages import explain

URL = "https://hrsuindore.com/blog/x/"
CANONICAL = ("Calcium nitrate dissolves readily in water. In sandy soils, electrical "
             "conductivity (EC) peaks at 425 during infiltration. A 20% calcium carbonate "
             "and 80% river sand mixture models desert soil.")


def _ctx(tmp_path: Path) -> StageContext:
    m = RunManifest.create(URL, workspace_root=tmp_path)
    ws = Path(m.workspace)
    (ws / "canonical.txt").write_text(CANONICAL, encoding="utf-8")
    (ws / "post.json").write_text(json.dumps(
        {"url": URL, "title": "T", "region": "eu", "category": "c", "citations": [], "images": []}),
        encoding="utf-8")
    (ws / "factsheet.json").write_text(json.dumps({"facts": [], "brand_facts": [], "dropped": []}),
                                       encoding="utf-8")
    return StageContext(manifest=m, workspace=ws, flags={})


def _good_plan():
    def step(sid, claims):
        return {"step_id": sid, "claim_text": f"goal {sid}", "claims": claims, "terms": [],
                "visual_intent": {"entities": ["water"], "relationship": "r", "quantity": "q"}}
    return {
        "question": "Why does calcium nitrate suit foliar sprays?",
        "steps": [
            step("s1", [{"id": "c1", "text": "Calcium nitrate dissolves readily in water.",
                         "kind": "blog_stated",
                         "quote": "Calcium nitrate dissolves readily in water."}]),
            step("s2", [{"id": "c2", "text": "Dissolved salt passes through spray nozzles.",
                         "kind": "reasoning", "quote": ""}]),
            step("s3", [{"id": "c3", "text": "Soluble powder avoids clogged nozzles.",
                         "kind": "reasoning", "quote": ""}]),
        ],
        "payoff": {"takeaway": "Choose a consistently soluble powder.",
                   "differentiator_id": "b_purity"},
    }


def test_writes_plan_document_and_returns_artifact(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    seen = {}

    def fake(prompt, system, schema, **kw):
        seen["role"] = kw.get("role")
        return _good_plan()

    monkeypatch.setattr(explain.text_llm, "generate_schema_json", fake)
    assert explain.run(ctx) == {"explanation_plan": "explanation_plan.json"}
    doc = json.loads((ctx.workspace / "explanation_plan.json").read_text(encoding="utf-8"))
    assert seen["role"] == "planner"
    assert doc["status"] == "planned" and doc["attempts"] == 1
    assert doc["steps"][0]["claims"][0]["support"]["type"] == "blog_quote"
    assert doc["steps"][0]["claims"][0]["verdict"] is None


def test_validation_errors_are_echoed_into_the_retry_prompt(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    bad = _good_plan()
    bad["steps"][0]["claims"][0]["quote"] = "a sentence that is not in the article"
    prompts = []

    def fake(prompt, system, schema, **kw):
        prompts.append(prompt)
        return bad if len(prompts) == 1 else _good_plan()

    monkeypatch.setattr(explain.text_llm, "generate_schema_json", fake)
    explain.run(ctx)
    assert len(prompts) == 2
    assert "not found verbatim" in prompts[1]
    assert "not found verbatim" not in prompts[0]


def test_prompt_contains_full_article_and_differentiators(tmp_path):
    from shorts_engine.brand import load_brand_facts
    prompt = explain._planner_prompt({"title": "T", "region": "eu", "category": "c"},
                                     CANONICAL, {"facts": []}, load_brand_facts())
    assert CANONICAL in prompt
    assert "b_purity" in prompt


def test_exhausted_retries_hold_for_review_and_write_no_plan(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    bad = _good_plan()
    bad["steps"] = bad["steps"][:1]

    calls = []
    monkeypatch.setattr(explain.text_llm, "generate_schema_json",
                        lambda p, s, sc, **kw: calls.append(1) or bad)
    with pytest.raises(HoldForReview) as e:
        explain.run(ctx)
    assert len(calls) == config.LLM_MAX_RETRIES
    assert any("structure" in r for r in e.value.reasons)
    assert not (ctx.workspace / "explanation_plan.json").exists()
```

(Note: `bad["steps"][:1]` fails jsonschema in real `generate_schema_json`, but the test fakes the whole function, so the stage's own `validate_plan` is what's exercised.)

- [ ] **Step 2: Run to verify failure**

Run: `cd _shorts_engine_impl && python -m pytest tests/shorts_engine/test_explain.py -v`
Expected: FAIL (`ModuleNotFoundError`/`ImportError` for `shorts_engine.stages.explain`).

- [ ] **Step 3: Implement**

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `cd _shorts_engine_impl && python -m pytest tests/shorts_engine/test_explain.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add _shorts_engine_impl/shorts_engine/stages/explain.py _shorts_engine_impl/tests/shorts_engine/test_explain.py
git commit -m "feat: EXPLAIN stage builds a tagged causal explanation plan from the full article

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: VERIFY stage (`verify_claims`)

**Files:**
- Create: `_shorts_engine_impl/shorts_engine/stages/verify_claims.py`
- Test: `_shorts_engine_impl/tests/shorts_engine/test_verify_claims.py` (create)

**Interfaces:**
- Consumes: `explanation.*` (Task 3), `retrieval.Retriever`/`Passage` (Task 4), `text_llm.generate_schema_json(..., role=...)`, `errors.HoldForReview`, `config.MIN_VERIFIED_STEPS`, `config.PLAN_MAX_REPAIR_ROUNDS`, `stages.facts.locate_verbatim`.
- Produces: `VERDICT_SCHEMA`, `REPAIR_SCHEMA`, `verify_claim(claim, *, step, premises, canonical, retriever, local_only=False) -> dict` (returns a NEW claim dict with `verdict`, `reason`, and `support` filled in), `verify_plan(plan, *, canonical, retriever, local_only=False) -> None` (mutates in place; skips claims that already have a verdict), `run(ctx) -> {"explanation_plan": "explanation_plan.json"}`. After `run`, `explanation_plan.json` has `status` `"verified"` (or `"held"` + `hold_reasons`), only supported claims inside `steps`, and everything removed listed in `dropped_claims`/`dropped_steps`.

Verification rules (all must be implemented as written):
- `blog_stated`: verdict `supported` iff the quote is found verbatim in the canonical text **and** `gate_claim_numbers(text, quote, terms)` is empty; otherwise `unsupported` with the reason.
- `illustrative`: `supported` iff the text contains no number.
- `external_fact`: no passages -> `unsupported`, reason `no_retrieval` (**no LLM call**). Otherwise ask the verifier (role `"verifier"`); if `supported`, the cited passage index must be valid, the claim's numbers must pass `gate_claim_numbers(text, passage.text, terms)`, and `support` becomes `{"type":"source","quote":passage.text,"url":passage.url}`.
- `reasoning`: no earlier supported premises -> `unsupported`; otherwise ask the verifier with the premises; numbers must pass `gate_claim_numbers(text, " ".join(premises), terms)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_verify_claims.py
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from shorts_engine import explanation as ex
from shorts_engine.errors import HoldForReview
from shorts_engine.manifest import RunManifest
from shorts_engine.retrieval import Passage
from shorts_engine.runner import StageContext
from shorts_engine.stages import verify_claims as vc

CANONICAL = ("Calcium nitrate dissolves readily in water. A 20% calcium carbonate and "
             "80% river sand mixture models desert soil.")


class FakeRetriever:
    def __init__(self, passages=None):
        self.passages, self.calls = passages or [], []

    def retrieve(self, claim_text):
        self.calls.append(claim_text)
        return self.passages


class Router:
    """Fake text_llm.generate_schema_json: answers by schema identity."""

    def __init__(self, verdicts=None, repair=None):
        self.verdicts = list(verdicts or [])
        self.repair = repair
        self.calls = []

    def __call__(self, prompt, system, schema, **kw):
        self.calls.append((schema, kw.get("role"), prompt))
        if schema is vc.VERDICT_SCHEMA:
            return self.verdicts.pop(0)
        if schema is vc.REPAIR_SCHEMA:
            return self.repair or {"claims": []}
        raise AssertionError(schema)


def _claim(cid, text, kind, quote="", verdict=None):
    c = ex.finalize_claim({"id": cid, "text": text, "kind": kind, "quote": quote})
    c["verdict"] = verdict
    return c


def _step(sid, claims):
    return {"step_id": sid, "claim_text": "g", "claims": claims, "terms": [],
            "visual_intent": {"entities": ["x"], "relationship": "r", "quantity": "q"}}


@pytest.fixture
def llm(monkeypatch):
    router = Router()
    monkeypatch.setattr(vc.text_llm, "generate_schema_json", router)
    return router


class TestVerifyClaim:
    def test_blog_stated_supported_when_quote_found(self, llm):
        c = _claim("c1", "Calcium nitrate dissolves in water.", "blog_stated",
                   "Calcium nitrate dissolves readily in water.")
        out = vc.verify_claim(c, step=_step("s", [c]), premises=[], canonical=CANONICAL,
                              retriever=FakeRetriever())
        assert out["verdict"] == "supported" and llm.calls == []

    def test_blog_stated_unsupported_when_quote_missing(self, llm):
        c = _claim("c1", "x", "blog_stated", "not in the article")
        out = vc.verify_claim(c, step=_step("s", [c]), premises=[], canonical=CANONICAL,
                              retriever=FakeRetriever())
        assert out["verdict"] == "unsupported" and "quote" in out["reason"]

    def test_illustrative_with_number_is_unsupported(self, llm):
        c = _claim("c1", "Like 3 sugar cubes", "illustrative")
        out = vc.verify_claim(c, step=_step("s", [c]), premises=[], canonical=CANONICAL,
                              retriever=FakeRetriever())
        assert out["verdict"] == "unsupported"

    def test_external_fact_without_retrieval_is_unsupported_and_makes_no_llm_call(self, llm):
        c = _claim("c2", "Calcium nitrate is very soluble.", "external_fact")
        out = vc.verify_claim(c, step=_step("s", [c]), premises=[], canonical=CANONICAL,
                              retriever=FakeRetriever([]))
        assert out["verdict"] == "unsupported" and out["reason"] == "no_retrieval"
        assert llm.calls == []

    def test_external_fact_supported_records_source(self, llm):
        llm.verdicts = [{"verdict": "supported", "passage_index": 0}]
        c = _claim("c2", "Calcium nitrate is very soluble in water.", "external_fact")
        p = Passage("https://www.epa.gov/x", "Calcium nitrate is very soluble in water.", "authoritative")
        out = vc.verify_claim(c, step=_step("s", [c]), premises=[], canonical=CANONICAL,
                              retriever=FakeRetriever([p]))
        assert out["verdict"] == "supported"
        assert out["support"] == {"type": "source", "quote": p.text, "url": p.url}
        assert llm.calls[0][1] == "verifier"

    def test_external_fact_contradicted(self, llm):
        llm.verdicts = [{"verdict": "contradicted", "passage_index": 0}]
        c = _claim("c2", "Calcium nitrate is insoluble.", "external_fact")
        p = Passage("https://a.gov", "Calcium nitrate is very soluble.", "authoritative")
        out = vc.verify_claim(c, step=_step("s", [c]), premises=[], canonical=CANONICAL,
                              retriever=FakeRetriever([p]))
        assert out["verdict"] == "contradicted"

    def test_supported_with_bad_passage_index_is_unsupported(self, llm):
        llm.verdicts = [{"verdict": "supported", "passage_index": 7}]
        c = _claim("c2", "Calcium nitrate is very soluble.", "external_fact")
        p = Passage("https://a.gov", "Calcium nitrate is very soluble.", "authoritative")
        out = vc.verify_claim(c, step=_step("s", [c]), premises=[], canonical=CANONICAL,
                              retriever=FakeRetriever([p]))
        assert out["verdict"] == "unsupported"

    def test_external_number_must_trace_to_the_passage(self, llm):
        llm.verdicts = [{"verdict": "supported", "passage_index": 0}]
        c = _claim("c2", "It dissolves 50 g per 100 ml of water.", "external_fact")
        p = Passage("https://a.gov", "Calcium nitrate is very soluble.", "authoritative")
        out = vc.verify_claim(c, step=_step("s", [c]), premises=[], canonical=CANONICAL,
                              retriever=FakeRetriever([p]))
        assert out["verdict"] == "unsupported" and "trace" in out["reason"]

    def test_reasoning_needs_premises(self, llm):
        c = _claim("c3", "So nozzles stay clear.", "reasoning")
        out = vc.verify_claim(c, step=_step("s", [c]), premises=[], canonical=CANONICAL,
                              retriever=FakeRetriever())
        assert out["verdict"] == "unsupported" and llm.calls == []

    def test_reasoning_supported_by_logic_check(self, llm):
        llm.verdicts = [{"verdict": "supported", "passage_index": None}]
        c = _claim("c3", "So nozzles stay clear.", "reasoning")
        out = vc.verify_claim(c, step=_step("s", [c]), premises=["It dissolves fully."],
                              canonical=CANONICAL, retriever=FakeRetriever())
        assert out["verdict"] == "supported"
        assert "It dissolves fully." in llm.calls[0][2]

    def test_does_not_mutate_input(self, llm):
        c = _claim("c1", "x", "blog_stated", "not in the article")
        before = copy.deepcopy(c)
        vc.verify_claim(c, step=_step("s", [c]), premises=[], canonical=CANONICAL,
                        retriever=FakeRetriever())
        assert c == before


def _plan(steps):
    return {"question": "q?", "steps": steps,
            "payoff": {"takeaway": "t", "differentiator_id": "b_purity"},
            "status": "planned", "attempts": 1, "dropped_claims": [], "dropped_steps": [],
            "hold_reasons": []}


def _ctx(tmp_path, plan):
    m = RunManifest.create("https://hrsuindore.com/blog/x/", workspace_root=tmp_path)
    ws = Path(m.workspace)
    (ws / "canonical.txt").write_text(CANONICAL, encoding="utf-8")
    (ws / "post.json").write_text(json.dumps({"citations": []}), encoding="utf-8")
    (ws / "explanation_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    return StageContext(manifest=m, workspace=ws, flags={})


def _good_steps():
    q = "Calcium nitrate dissolves readily in water."
    return [_step(f"s{i}", [_claim(f"c{i}", "Calcium nitrate dissolves in water.",
                                   "blog_stated", q)]) for i in (1, 2, 3)]


class TestRun:
    def test_all_supported_writes_verified_plan(self, tmp_path, monkeypatch, llm):
        ctx = _ctx(tmp_path, _plan(_good_steps()))
        monkeypatch.setattr(vc, "Retriever", lambda urls: FakeRetriever())
        assert vc.run(ctx) == {"explanation_plan": "explanation_plan.json"}
        doc = json.loads((ctx.workspace / "explanation_plan.json").read_text(encoding="utf-8"))
        assert doc["status"] == "verified"
        assert all(c["verdict"] == "supported" for s in doc["steps"] for c in s["claims"])

    def test_unsupported_claims_are_dropped_and_recorded(self, tmp_path, monkeypatch, llm):
        steps = _good_steps()
        steps[0]["claims"].append(_claim("cx", "Calcium nitrate is miraculous.", "external_fact"))
        ctx = _ctx(tmp_path, _plan(steps))
        monkeypatch.setattr(vc, "Retriever", lambda urls: FakeRetriever([]))  # empty search
        vc.run(ctx)
        doc = json.loads((ctx.workspace / "explanation_plan.json").read_text(encoding="utf-8"))
        assert [c["id"] for c in doc["dropped_claims"]] == ["cx"]
        assert len(doc["steps"]) == 3 and doc["status"] == "verified"

    def test_holds_when_fewer_than_three_steps_survive(self, tmp_path, monkeypatch, llm):
        steps = _good_steps()
        steps[2]["claims"] = [_claim("cz", "Unsourceable claim.", "external_fact")]
        ctx = _ctx(tmp_path, _plan(steps))
        monkeypatch.setattr(vc, "Retriever", lambda urls: FakeRetriever([]))
        with pytest.raises(HoldForReview) as e:
            vc.run(ctx)
        assert any("3" in r for r in e.value.reasons)
        doc = json.loads((ctx.workspace / "explanation_plan.json").read_text(encoding="utf-8"))
        assert doc["status"] == "held" and doc["hold_reasons"]
        assert [s["step_id"] for s in doc["dropped_steps"]] == ["s3"]

    def test_step_with_only_illustrative_claims_is_dropped(self, tmp_path, monkeypatch, llm):
        steps = _good_steps()
        steps.append(_step("s4", [_claim("ci", "Like sugar in tea.", "illustrative")]))
        ctx = _ctx(tmp_path, _plan(steps))
        monkeypatch.setattr(vc, "Retriever", lambda urls: FakeRetriever())
        vc.run(ctx)
        doc = json.loads((ctx.workspace / "explanation_plan.json").read_text(encoding="utf-8"))
        assert [s["step_id"] for s in doc["steps"]] == ["s1", "s2", "s3"]

    def test_repair_round_replaces_a_failed_claim_and_reverifies(self, tmp_path, monkeypatch, llm):
        steps = _good_steps()
        steps[0]["claims"] = [_claim("cx", "Invented fact.", "blog_stated", "not in article")]
        llm.repair = {"claims": [{"id": "new", "text": "Calcium nitrate dissolves in water.",
                                  "kind": "blog_stated",
                                  "quote": "Calcium nitrate dissolves readily in water."}]}
        ctx = _ctx(tmp_path, _plan(steps))
        monkeypatch.setattr(vc, "Retriever", lambda urls: FakeRetriever())
        vc.run(ctx)
        doc = json.loads((ctx.workspace / "explanation_plan.json").read_text(encoding="utf-8"))
        ids = [c["id"] for c in doc["steps"][0]["claims"]]
        assert len(ids) == 1 and ids[0].startswith("s1_r1_")
        assert [c["id"] for c in doc["dropped_claims"]] == ["cx"]
        assert any(s is vc.REPAIR_SCHEMA and role == "planner" for s, role, _ in llm.calls)

    def test_repair_is_bounded_by_max_rounds(self, tmp_path, monkeypatch, llm):
        from shorts_engine import config
        steps = _good_steps()
        steps[0]["claims"] = [_claim("cx", "Invented.", "blog_stated", "nope")]
        llm.repair = {"claims": [{"id": "n", "text": "Still invented.", "kind": "blog_stated",
                                  "quote": "nope again"}]}
        ctx = _ctx(tmp_path, _plan(steps))
        monkeypatch.setattr(vc, "Retriever", lambda urls: FakeRetriever())
        with pytest.raises(HoldForReview):
            vc.run(ctx)
        repairs = [c for c in llm.calls if c[0] is vc.REPAIR_SCHEMA]
        assert len(repairs) == config.PLAN_MAX_REPAIR_ROUNDS
```

- [ ] **Step 2: Run to verify failure**

Run: `cd _shorts_engine_impl && python -m pytest tests/shorts_engine/test_verify_claims.py -v`
Expected: FAIL (`ImportError` for `shorts_engine.stages.verify_claims`).

- [ ] **Step 3: Implement**

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `cd _shorts_engine_impl && python -m pytest tests/shorts_engine/test_verify_claims.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add _shorts_engine_impl/shorts_engine/stages/verify_claims.py _shorts_engine_impl/tests/shorts_engine/test_verify_claims.py
git commit -m "feat: VERIFY stage checks, repairs or drops every claim; holds on thin plans

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Rewrite SCRIPT to narrate the verified plan

**Files:**
- Rewrite: `_shorts_engine_impl/shorts_engine/stages/script.py`
- Rewrite: `_shorts_engine_impl/tests/shorts_engine/test_script_run.py`
- Modify: `_shorts_engine_impl/tests/shorts_engine/test_script.py`, `_shorts_engine_impl/tests/shorts_engine/test_script_gates.py`

**Interfaces:**
- Consumes: verified `explanation_plan.json` (Task 6), `factsheet.json`, `post.json`, `brand.load_brand_facts`, `text_llm.generate_schema_json(..., role="writer"|"critic")`, `config.SCRIPT_MAX_REWRITES`, `errors.HoldForReview`.
- Produces: `script.run(ctx) -> {"script": "script.json"}`; `script.json` = `{"beats": [...], "critique": {...}, "attempts": int, "rewrites": int}`; each beat = `{"beat","purpose","narration","fact_ids","card_text","broll_wish"}` plus, for step beats, `"step_id"`, `"diagram_labels"` (<=4 labels, <=3 words each, from `visual_intent.entities`) and `"visual_intent"`. Names: first beat `hook` (purpose `hook`), step beats `step_1..N` (purpose `mechanism`), last beat `cta` (purpose `cta`, `fact_ids == [payoff.differentiator_id]`). Public helpers kept: `extract_numeric_tokens`, `gate_numbers(beats, factsheet, brand, extra_by_beat=None)`, `gate_banned`, `gate_total_duration`, `gate_card_text`, `gate_differentiator`, `run_gates(beats, factsheet, brand, extra_by_beat=None)`. New: `SCRIPT_SCHEMA`, `CRITIQUE_SCHEMA`, `build_beats(doc, plan, factsheet, brand) -> (beats, errors)`, `extra_pool_by_beat(plan) -> dict[str,str]`, `_writer_prompt`, `_critic_prompt`. **Removed:** `gate_word_budget`, `apply_word_topup`, `_TOPUP_PHRASES`, `_only_duration_shortfall`, `_beat_rules`.

- [ ] **Step 1: Write the new tests**

Replace the whole of `tests/shorts_engine/test_script_run.py` with:

```python
"""SCRIPT stage: narrate the verified explanation plan; gates; final-script critic loop."""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from shorts_engine import config
from shorts_engine.brand import load_brand_facts
from shorts_engine.errors import HoldForReview
from shorts_engine.manifest import RunManifest
from shorts_engine.runner import StageContext
from shorts_engine.stages import script as script_stage

URL = "https://hrsuindore.com/blog/x/"
FACTSHEET = {"facts": [{"id": "f1", "verbatim_quote": "a 20% calcium carbonate and 80% river sand mixture",
                        "value": "20", "unit": "%", "procurement_significance": 5,
                        "citation_marker": None}]}


def _claim(cid, text, kind, quote="", verdict="supported"):
    return {"id": cid, "text": text, "kind": kind, "verdict": verdict, "needs_number": False,
            "support": {"type": "blog_quote" if kind == "blog_stated" else None,
                        "quote": quote, "url": None}}


def _plan():
    def step(sid, claims, ents):
        return {"step_id": sid, "claim_text": "goal", "claims": claims, "terms": [],
                "visual_intent": {"entities": ents, "relationship": "r", "quantity": "q"}}
    return {
        "question": "Why model desert soil?",
        "steps": [
            step("s1", [_claim("c1", "Engineers model desert soil with a sand mixture.",
                               "blog_stated", "a 20% calcium carbonate and 80% river sand mixture")],
                 ["calcium carbonate", "river sand", "mixture of the two soils here"]),
            step("s2", [_claim("c2", "Sandy soil drains nutrients quickly.", "external_fact")],
                 ["sand", "water"]),
            step("s3", [_claim("c3", "So nutrients must be held in solution.", "reasoning")],
                 ["nutrient"]),
        ],
        "payoff": {"takeaway": "Pick a soluble powder.", "differentiator_id": "b_purity"},
        "status": "verified", "attempts": 1, "dropped_claims": [], "dropped_steps": [],
        "hold_reasons": [],
    }


def _doc(**over):
    words = " ".join(["word"] * 12)
    doc = {
        "hook": {"narration": f"Why do engineers model desert soil? {words}.",
                 "card_text": "Why model sand?", "broll_wish": "desert"},
        "steps": [
            {"step_id": "s1", "narration": f"Engineers model desert soil with sand. {words}.",
             "card_text": "Sand mixture", "broll_wish": ""},
            {"step_id": "s2", "narration": f"Sandy soil drains nutrients quickly. {words}.",
             "card_text": "Fast drainage", "broll_wish": ""},
            {"step_id": "s3", "narration": f"So nutrients must be held in solution. {words}.",
             "card_text": "Hold in solution", "broll_wish": ""},
        ],
        "cta": {"narration": f"Consistent high-purity calcium nitrate powder helps. Visit hrsuindore.com. {words}.",
                "card_text": "hrsuindore.com", "broll_wish": ""},
    }
    doc.update(over)
    return doc


GOOD_CRITIQUE = {"actionable_score": 8, "coherence_score": 9, "hrsu_reason_score": 8,
                 "faithfulness_score": 9, "revise_notes": ""}
LOW_CRITIQUE = dict(GOOD_CRITIQUE, coherence_score=3, revise_notes="Step 2 does not follow.")


def _ctx(tmp_path: Path, plan=None) -> StageContext:
    m = RunManifest.create(URL, workspace_root=tmp_path)
    ws = Path(m.workspace)
    (ws / "post.json").write_text(json.dumps(
        {"url": URL, "title": "T", "region": "eu", "category": "c", "citations": [], "images": []}),
        encoding="utf-8")
    (ws / "factsheet.json").write_text(json.dumps(FACTSHEET), encoding="utf-8")
    (ws / "explanation_plan.json").write_text(json.dumps(plan or _plan()), encoding="utf-8")
    return StageContext(manifest=m, workspace=ws, flags={})


class Router:
    def __init__(self, docs, critiques):
        self.docs, self.critiques = list(docs), list(critiques)
        self.writer_prompts, self.roles = [], []

    def __call__(self, prompt, system, schema, **kw):
        self.roles.append(kw.get("role"))
        if schema is script_stage.SCRIPT_SCHEMA:
            self.writer_prompts.append(prompt)
            return self.docs.pop(0) if len(self.docs) > 1 else self.docs[0]
        if schema is script_stage.CRITIQUE_SCHEMA:
            return self.critiques.pop(0) if len(self.critiques) > 1 else self.critiques[0]
        raise AssertionError(schema)


def _install(monkeypatch, docs, critiques):
    r = Router(docs, critiques)
    monkeypatch.setattr(script_stage.text_llm, "generate_schema_json", r)
    return r


class TestBuildBeats:
    def test_beat_shape_names_and_purposes(self):
        beats, errs = script_stage.build_beats(_doc(), _plan(), FACTSHEET, load_brand_facts())
        assert errs == []
        assert [b["beat"] for b in beats] == ["hook", "step_1", "step_2", "step_3", "cta"]
        assert [b["purpose"] for b in beats] == ["hook", "mechanism", "mechanism", "mechanism", "cta"]
        assert beats[4]["fact_ids"] == ["b_purity"]
        assert beats[1]["step_id"] == "s1" and beats[1]["fact_ids"] == ["f1"]
        assert beats[1]["diagram_labels"] == ["calcium carbonate", "river sand", "mixture of the"]
        assert beats[1]["visual_intent"]["entities"][0] == "calcium carbonate"

    def test_build_beats_rejects_step_id_mismatch(self):
        doc = _doc()
        doc["steps"][0]["step_id"], doc["steps"][1]["step_id"] = "s2", "s1"
        beats, errs = script_stage.build_beats(doc, _plan(), FACTSHEET, load_brand_facts())
        assert beats == [] and any("steps must be exactly" in e for e in errs)

    def test_extra_pool_contains_supported_claim_text_and_quotes(self):
        pool = script_stage.extra_pool_by_beat(_plan())
        assert "80% river sand" in pool["step_1"]
        assert "Sandy soil drains nutrients quickly." in pool["step_2"]


class TestSchemas:
    def test_script_schema_accepts_well_formed_doc(self):
        jsonschema.validate(_doc(), script_stage.SCRIPT_SCHEMA)

    def test_script_schema_rejects_extra_key(self):
        bad = _doc()
        bad["hook"]["surprise"] = "x"
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, script_stage.SCRIPT_SCHEMA)

    def test_critique_schema_requires_faithfulness_score(self):
        bad = {k: v for k, v in GOOD_CRITIQUE.items() if k != "faithfulness_score"}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, script_stage.CRITIQUE_SCHEMA)


class TestRunHappyPath:
    def test_writes_script_json_with_final_critique(self, tmp_path, monkeypatch):
        ctx = _ctx(tmp_path)
        router = _install(monkeypatch, [_doc()], [GOOD_CRITIQUE])
        assert script_stage.run(ctx) == {"script": "script.json"}
        out = json.loads((ctx.workspace / "script.json").read_text(encoding="utf-8"))
        assert out["critique"] == GOOD_CRITIQUE
        assert out["attempts"] == 1 and out["rewrites"] == 0
        assert out["beats"][0]["beat"] == "hook"
        assert "writer" in router.roles and "critic" in router.roles

    def test_accepted_script_passes_every_gate(self, tmp_path, monkeypatch):
        ctx = _ctx(tmp_path)
        _install(monkeypatch, [_doc()], [GOOD_CRITIQUE])
        script_stage.run(ctx)
        out = json.loads((ctx.workspace / "script.json").read_text(encoding="utf-8"))
        extra = script_stage.extra_pool_by_beat(_plan())
        assert script_stage.run_gates(out["beats"], FACTSHEET, load_brand_facts(),
                                      extra_by_beat=extra) == []

    def test_not_verified_plan_is_refused(self, tmp_path, monkeypatch):
        from shorts_engine.errors import EngineError
        plan = _plan()
        plan["status"] = "planned"
        ctx = _ctx(tmp_path, plan)
        _install(monkeypatch, [_doc()], [GOOD_CRITIQUE])
        with pytest.raises(EngineError, match="not verified"):
            script_stage.run(ctx)


class TestWriterPrompt:
    def test_prompt_has_supported_claims_terms_and_no_ceiling(self):
        plan = _plan()
        plan["steps"][0]["terms"] = [{"term": "EC", "definition": "conductivity", "unit": ""}]
        prompt = script_stage._writer_prompt(plan, {"title": "T", "region": "eu", "category": "c"},
                                             load_brand_facts())
        assert "Sandy soil drains nutrients quickly." in prompt
        assert "EC: conductivity" in prompt
        assert "b_purity" in prompt
        assert "no maximum" in prompt.lower()

    def test_prompt_echoes_gate_errors_and_revise_notes(self):
        prompt = script_stage._writer_prompt(
            _plan(), {"title": "T"}, load_brand_facts(),
            gate_errors=["numbers[step_1]: '9' bad"], revise_notes="Fix step 2.")
        assert "numbers[step_1]" in prompt and "Fix step 2." in prompt


class TestGateRetryAndHold:
    def test_gate_errors_are_echoed_into_retry(self, tmp_path, monkeypatch):
        ctx = _ctx(tmp_path)
        bad = _doc()
        bad["steps"][1]["narration"] += " It drains 150 liters."
        router = _install(monkeypatch, [bad, _doc()], [GOOD_CRITIQUE])
        script_stage.run(ctx)
        assert len(router.writer_prompts) == 2
        assert "'150'" in router.writer_prompts[1] and "'150'" not in router.writer_prompts[0]

    def test_exhausted_writer_retries_hold_and_write_nothing(self, tmp_path, monkeypatch):
        ctx = _ctx(tmp_path)
        bad = _doc()
        bad["steps"][1]["narration"] += " It drains 150 liters."
        router = _install(monkeypatch, [bad], [GOOD_CRITIQUE])
        with pytest.raises(HoldForReview) as e:
            script_stage.run(ctx)
        assert len(router.writer_prompts) == config.LLM_MAX_RETRIES
        assert any("150" in r for r in e.value.reasons)
        assert not (ctx.workspace / "script.json").exists()

    def test_short_script_is_not_padded_it_holds(self, tmp_path, monkeypatch):
        ctx = _ctx(tmp_path)
        tiny = _doc()
        for key in ("hook", "cta"):
            tiny[key]["narration"] = tiny[key]["narration"].split(".")[0] + "."
        for s in tiny["steps"]:
            s["narration"] = s["narration"].split(".")[0] + "."
        _install(monkeypatch, [tiny], [GOOD_CRITIQUE])
        with pytest.raises(HoldForReview) as e:
            script_stage.run(ctx)
        assert any("total_duration" in r for r in e.value.reasons)


class TestCriticLoop:
    def test_low_critique_triggers_rewrite_and_final_script_is_rescored(self, tmp_path, monkeypatch):
        ctx = _ctx(tmp_path)
        router = _install(monkeypatch, [_doc()], [LOW_CRITIQUE, GOOD_CRITIQUE])
        script_stage.run(ctx)
        out = json.loads((ctx.workspace / "script.json").read_text(encoding="utf-8"))
        assert out["rewrites"] == 1
        assert out["critique"] == GOOD_CRITIQUE          # the FINAL script's critique
        assert "Step 2 does not follow." in router.writer_prompts[1]

    def test_threshold_score_of_seven_passes_without_rewrite(self, tmp_path, monkeypatch):
        ctx = _ctx(tmp_path)
        seven = dict(GOOD_CRITIQUE, actionable_score=7)
        _install(monkeypatch, [_doc()], [seven])
        script_stage.run(ctx)
        assert json.loads((ctx.workspace / "script.json").read_text(encoding="utf-8"))["rewrites"] == 0

    def test_faithfulness_score_gates_too(self, tmp_path, monkeypatch):
        ctx = _ctx(tmp_path)
        unfaithful = dict(GOOD_CRITIQUE, faithfulness_score=2, revise_notes="Adds unsupported facts.")
        router = _install(monkeypatch, [_doc()], [unfaithful, GOOD_CRITIQUE])
        script_stage.run(ctx)
        assert "Adds unsupported facts." in router.writer_prompts[1]

    def test_still_low_after_max_rewrites_holds(self, tmp_path, monkeypatch):
        ctx = _ctx(tmp_path)
        _install(monkeypatch, [_doc()], [LOW_CRITIQUE])
        with pytest.raises(HoldForReview) as e:
            script_stage.run(ctx)
        assert "critique below bar" in e.value.reasons[0]
        assert not (ctx.workspace / "script.json").exists()
```

- [ ] **Step 2: Edit the two older test files**

In `tests/shorts_engine/test_script_gates.py`:
1. Remove `gate_word_budget,` from the import list.
2. Delete the whole `class TestGateWordBudget` (the block starting `class TestGateWordBudget:` up to the line before `class TestGateTotalDuration:`).
3. In `TestGateTotalDuration`, replace `test_too_short_message_names_exact_deficit_and_headroom_beats` with:

```python
    def test_too_short_message_names_exact_deficit(self) -> None:
        """The error does the arithmetic for the writer: exact words needed."""
        short = _beats(**{
            "0": {"narration": " ".join(["word"] * 5)},
            "1": {"narration": " ".join(["word"] * 8)},
            "2": {"narration": " ".join(["word"] * 15)},
            "3": {"narration": " ".join(["word"] * 12)},
            "4": {"narration": " ".join(["word"] * 10)},
        })  # total = 50 words -> 29.4s at 1.7 w/s
        errs = gate_total_duration(short)
        assert len(errs) == 1
        assert "AT LEAST" in errs[0]
        assert "4" in errs[0]  # 51 - 50 + 3 buffer = 4
```
4. Delete the whole `class TestScriptSchemaDiagramLabels` (it exercised the old beats-array schema). Keep `TestDiagramLabelsGate`.

In `tests/shorts_engine/test_script.py`:
1. Change the import to `from shorts_engine.stages.script import gate_total_duration, run_gates`.
2. Delete `test_gate_word_budget_keys_off_purpose_not_position`, `test_apply_word_topup_uses_purpose_keyed_headroom` and `test_schema_has_no_max_items_and_requires_purpose`.

- [ ] **Step 3: Run to verify failure**

Run: `cd _shorts_engine_impl && python -m pytest tests/shorts_engine/test_script_run.py tests/shorts_engine/test_script.py tests/shorts_engine/test_script_gates.py -v`
Expected: FAIL (`build_beats` missing / `gate_word_budget` import errors resolved by the edits but new names absent).

- [ ] **Step 4: Rewrite `shorts_engine/stages/script.py`**

Replace the file with:

```python
"""
SCRIPT stage -- verified explanation_plan.json (+ post.json, factsheet.json) to script.json.

The writer no longer invents the logic: it narrates the verified plan, one beat per
step, with a hook that poses the plan's question and a CTA built on the payoff. All
deterministic gates stay (number tracing, banned phrases, card_text hygiene, exactly
one differentiator in the CTA, the 30 s floor) but there are no per-purpose word
budgets and no filler top-up: length follows the content, with no ceiling.

A critic from a different model family then scores the FINAL script (coherence,
actionability, HRSU reason, faithfulness to the verified claims). A score below the
bar triggers a rewrite that is itself gated and re-scored, up to
config.SCRIPT_MAX_REWRITES times. A script that cannot reach the bar, or fails the
gates after LLM_MAX_RETRIES attempts, raises HoldForReview -- it never ships and is
never padded.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from shorts_engine import config
from shorts_engine.brand import BrandFacts, load_brand_facts
from shorts_engine.errors import EngineError, HoldForReview
from shorts_engine.llm import text_llm
from shorts_engine.stages.facts import normalize_for_match

logger = logging.getLogger(__name__)

_CRITIQUE_PASS_THRESHOLD = 7

# ── Numeric token extraction ────────────────────────────────────────────────
_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def extract_numeric_tokens(text: str) -> list[str]:
    """Numeric tokens in order of appearance, thousands commas stripped ("10,000" -> "10000")."""
    return [t.replace(",", "") for t in _NUM_RE.findall(text or "")]


# ── gate_numbers: the never-unverified invariant ────────────────────────────
def _allowed_pool(fact_ids: list[str], factsheet: dict, brand: BrandFacts,
                  extra: str = "") -> str:
    """Normalized, comma-stripped text a beat's numbers may come from: the verbatim quotes of
    its fact_ids, referenced brand differentiators, every CTA line, the domain, plus `extra`
    (the verified claims of the beat's plan step)."""
    facts_by_id = {f["id"]: f for f in factsheet.get("facts", [])}
    diffs_by_id = {d["id"]: d["text"] for d in brand.differentiators}
    parts: list[str] = []
    for fid in fact_ids:
        if fid in facts_by_id:
            parts.append(facts_by_id[fid]["verbatim_quote"])
        elif fid in diffs_by_id:
            parts.append(diffs_by_id[fid])
    parts.extend(brand.cta_lines)
    parts.append(brand.domain)
    if extra:
        parts.append(extra)
    return normalize_for_match(" | ".join(parts)).replace(",", "")


def gate_numbers(beats: list[dict], factsheet: dict, brand: BrandFacts,
                 extra_by_beat: dict[str, str] | None = None) -> list[str]:
    """Every numeric token in a beat's narration, card_text and diagram_labels must appear
    as a standalone number in that beat's allowed pool (see _allowed_pool).
    `extra_by_beat` maps beat name -> extra allowed text (verified plan claims)."""
    errs: list[str] = []
    for b in beats:
        extra = (extra_by_beat or {}).get(b.get("beat"), "")
        pool = _allowed_pool(b.get("fact_ids", []), factsheet, brand, extra)
        sources = [("narration", b.get("narration", "")), ("card_text", b.get("card_text", ""))]
        sources += [("diagram label", label) for label in b.get("diagram_labels") or []]
        for name, text in sources:
            for tok in extract_numeric_tokens(text):
                if not re.search(rf"(?<![\d.]){re.escape(tok)}(?![\d])", pool):
                    errs.append(f"numbers[{b.get('beat')}]: {tok!r} in {name} does not "
                                f"trace to any referenced fact")
    return errs


# ── gate_banned ──────────────────────────────────────────────────────────────
def gate_banned(beats: list[dict], brand: BrandFacts) -> list[str]:
    """Reject SCRIPT_BANNED_PHRASES, FEAR_FILLER_PATTERNS and brand.banned_claims
    (case-insensitive substring) in a beat's narration or card_text."""
    banned = ([p.lower() for p in config.SCRIPT_BANNED_PHRASES]
              + [p.lower() for p in config.FEAR_FILLER_PATTERNS]
              + [p.lower() for p in brand.banned_claims])
    errs: list[str] = []
    for b in beats:
        text = f"{b.get('narration', '')} {b.get('card_text', '')}".lower()
        for phrase in banned:
            if phrase in text:
                errs.append(f"banned[{b.get('beat')}]: contains {phrase!r}")
    return errs


# ── gate_total_duration ──────────────────────────────────────────────────────
def gate_total_duration(beats: list[dict]) -> list[str]:
    """Aggregate estimated duration vs the config.TOTAL_MIN_S floor. No ceiling."""
    total_words = sum(len(b.get("narration", "").split()) for b in beats)
    total_s = total_words / config.WORDS_PER_SECOND
    if total_s >= config.TOTAL_MIN_S:
        return []
    min_words = round(config.TOTAL_MIN_S * config.WORDS_PER_SECOND)
    deficit = min_words - total_words + 3  # small buffer against undershoot
    return [f"total_duration: {total_s:.1f}s ({total_words} words) is short of the "
            f"{config.TOTAL_MIN_S:.0f}s floor ({min_words} words). Add AT LEAST {deficit} "
            f"more words in total by explaining the steps more fully (never with filler)."]


# ── gate_card_text ───────────────────────────────────────────────────────────
def gate_card_text(beats: list[dict]) -> list[str]:
    """card_text: at most 7 words and no 5-gram copied from the beat's own narration."""
    errs: list[str] = []
    for b in beats:
        card = normalize_for_match(b.get("card_text", ""))
        narr = normalize_for_match(b.get("narration", ""))
        words = card.split()
        if len(words) >= 5:
            for i in range(len(words) - 4):
                if " ".join(words[i:i + 5]) in narr:
                    errs.append(f"card[{b.get('beat')}]: card_text duplicates narration")
                    break
        if len(words) > 7:
            errs.append(f"card[{b.get('beat')}]: card_text longer than 7 words")
    return errs


# ── gate_differentiator ──────────────────────────────────────────────────────
def gate_differentiator(beats: list[dict], brand: BrandFacts) -> list[str]:
    """Exactly one brand differentiator id, cited only in the final (cta) beat."""
    diff_ids = {d["id"] for d in brand.differentiators}
    errs: list[str] = []
    if not beats:
        return errs
    in_cta = [f for f in beats[-1].get("fact_ids", []) if f in diff_ids]
    if len(in_cta) != 1:
        errs.append(f"differentiator[cta]: expected exactly one of {sorted(diff_ids)}, "
                    f"got {in_cta}")
    for b in beats[:-1]:
        early = [f for f in b.get("fact_ids", []) if f in diff_ids]
        if early:
            errs.append(f"differentiator[{b.get('beat')}]: brand differentiators belong "
                        f"in the CTA beat only, found {early}")
    return errs


# ── run_gates ────────────────────────────────────────────────────────────────
def run_gates(beats: list[dict], factsheet: dict, brand: BrandFacts,
              extra_by_beat: dict[str, str] | None = None) -> list[str]:
    """Structure check (>= MIN_BEATS beats, final purpose "cta", unique names) then the
    content gates. A structural mismatch short-circuits with a single error."""
    if len(beats) < config.MIN_BEATS:
        return [f"structure: expected at least {config.MIN_BEATS} beats, got {len(beats)}"]
    if beats[-1].get("purpose") != "cta":
        return ['structure: the final beat must have purpose "cta"']
    names = [b.get("beat") for b in beats]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        return [f"structure: beat names must be unique, found duplicate(s): {dupes}"]
    return (gate_numbers(beats, factsheet, brand, extra_by_beat)
            + gate_banned(beats, brand)
            + gate_total_duration(beats)
            + gate_card_text(beats)
            + gate_differentiator(beats, brand))


# ── Schemas ──────────────────────────────────────────────────────────────────
_BEAT_FIELDS = {"narration": {"type": "string"}, "card_text": {"type": "string"},
                "broll_wish": {"type": "string"}}
_BEAT_REQUIRED = ["narration", "card_text", "broll_wish"]

SCRIPT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "hook": {"type": "object", "properties": _BEAT_FIELDS,
                 "required": _BEAT_REQUIRED, "additionalProperties": False},
        "steps": {"type": "array", "items": {
            "type": "object",
            "properties": {"step_id": {"type": "string"}, **_BEAT_FIELDS},
            "required": ["step_id"] + _BEAT_REQUIRED, "additionalProperties": False}},
        "cta": {"type": "object", "properties": _BEAT_FIELDS,
                "required": _BEAT_REQUIRED, "additionalProperties": False},
    },
    "required": ["hook", "steps", "cta"],
    "additionalProperties": False,
}

CRITIQUE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "actionable_score": {"type": "integer", "minimum": 0, "maximum": 10},
        "coherence_score": {"type": "integer", "minimum": 0, "maximum": 10},
        "hrsu_reason_score": {"type": "integer", "minimum": 0, "maximum": 10},
        "faithfulness_score": {"type": "integer", "minimum": 0, "maximum": 10},
        "revise_notes": {"type": "string"},
    },
    "required": ["actionable_score", "coherence_score", "hrsu_reason_score",
                 "faithfulness_score", "revise_notes"],
    "additionalProperties": False,
}

_WRITER_SYSTEM = (
    "You write the narration for an explainer video for procurement managers sourcing "
    "industrial chemicals. Voice: concrete, technical, zero hype, like a good teacher. You "
    "are given a VERIFIED explanation plan: narrate it step by step, in order. Open with a "
    "hook that poses the plan's question. Explain each step so a viewer who knows nothing "
    "about it can follow: define each term the first time you use it, and say why the step "
    "leads to the next. Convey ONLY the verified claims listed for each step -- never add a "
    "new fact, number or unit. The last item is the CTA: state the takeaway and cite the "
    "brand differentiator. card_text is at most 7 words and must not repeat the narration. "
    "Length follows the explanation: there is a 30 second floor and no maximum, and you must "
    "never pad with filler."
)

_CRITIC_SYSTEM = (
    "You are a skeptical procurement manager reviewing an explainer script against the "
    "verified claims it was written from. Score 0-10: actionable_score (did I learn "
    "something usable?), coherence_score (does each step follow from the last, are terms "
    "defined before use, is the chemistry described clearly?), hrsu_reason_score (is there "
    "one credible reason to consider HRSU?), faithfulness_score (does the narration assert "
    "ANYTHING not in the verified claims? any such addition scores below 5). Give concrete "
    "revise_notes."
)


# ── Beat construction ────────────────────────────────────────────────────────
def _labels(entities: list[str]) -> list[str]:
    return [" ".join(e.split()[:3]) for e in entities[:4] if e.strip()]


def _fact_ids_for_step(step: dict, factsheet: dict) -> list[str]:
    """Ids of factsheet facts whose verbatim quote overlaps a supported blog quote of the step."""
    quotes = [normalize_for_match(c["support"]["quote"]) for c in step["claims"]
              if c.get("support", {}).get("type") == "blog_quote" and c["support"]["quote"]]
    ids = []
    for f in factsheet.get("facts", []):
        fq = normalize_for_match(f["verbatim_quote"])
        if any(q in fq or fq in q for q in quotes):
            ids.append(f["id"])
    return ids


def extra_pool_by_beat(plan: dict) -> dict[str, str]:
    """beat name -> text of the step's supported claims and their quotes (number provenance)."""
    pools = {}
    for i, step in enumerate(plan["steps"], start=1):
        parts = []
        for c in step["claims"]:
            if c.get("verdict") == "supported":
                parts += [c["text"], c.get("support", {}).get("quote", "")]
        pools[f"step_{i}"] = " | ".join(p for p in parts if p)
    return pools


def build_beats(doc: dict, plan: dict, factsheet: dict,
                brand: BrandFacts) -> tuple[list[dict], list[str]]:
    """Writer document -> (beats, errors). Errors when step ids differ from the plan's."""
    expected = [s["step_id"] for s in plan["steps"]]
    got = [s["step_id"] for s in doc["steps"]]
    if got != expected:
        return [], [f"structure: steps must be exactly {expected} in that order, got {got}"]
    beats = [{"beat": "hook", "purpose": "hook", "fact_ids": [], **doc["hook"]}]
    for i, (pstep, wstep) in enumerate(zip(plan["steps"], doc["steps"]), start=1):
        beats.append({
            "beat": f"step_{i}", "purpose": "mechanism", "step_id": pstep["step_id"],
            "narration": wstep["narration"], "card_text": wstep["card_text"],
            "broll_wish": wstep["broll_wish"],
            "fact_ids": _fact_ids_for_step(pstep, factsheet),
            "diagram_labels": _labels(pstep["visual_intent"]["entities"]),
            "visual_intent": pstep["visual_intent"],
        })
    beats.append({"beat": "cta", "purpose": "cta",
                  "fact_ids": [plan["payoff"]["differentiator_id"]], **doc["cta"]})
    return beats, []


# ── Prompts ──────────────────────────────────────────────────────────────────
def _writer_prompt(plan: dict, post_meta: dict, brand: BrandFacts, *,
                   gate_errors: list[str] | None = None, revise_notes: str = "") -> str:
    blocks = []
    for step in plan["steps"]:
        claims = "\n".join(f"  - {c['text']}" for c in step["claims"]
                           if c.get("verdict") == "supported")
        terms = "\n".join(f"  - {t['term']}: {t['definition']}" for t in step.get("terms", []))
        blocks.append(f"STEP {step['step_id']} (goal: {step['claim_text']})\n"
                      f"Verified claims:\n{claims}\n"
                      + (f"Terms to define:\n{terms}\n" if terms else ""))
    diff = next((d for d in brand.differentiators
                 if d["id"] == plan["payoff"]["differentiator_id"]), None)
    prompt = (
        f"Blog: {post_meta.get('title')} | region={post_meta.get('region')} | "
        f"category={post_meta.get('category')}\n\n"
        f"QUESTION the video answers: {plan['question']}\n\n" + "\n".join(blocks)
        + f"\nPAYOFF takeaway: {plan['payoff']['takeaway']}\n"
        f"Brand differentiator to cite in the CTA [{plan['payoff']['differentiator_id']}]: "
        f"{diff['text'] if diff else ''}\nCTA domain: {brand.domain}\n\n"
        f"Return a hook, exactly one narration per step using these step_ids in this order "
        f"{[s['step_id'] for s in plan['steps']]}, and a cta. Total narration must reach at "
        f"least {round(config.TOTAL_MIN_S * config.WORDS_PER_SECOND)} words (no maximum), "
        f"through explanation, never filler.\n\nWrite the script now as JSON."
    )
    if gate_errors:
        prompt += ("\n\nYour previous draft FAILED these checks -- fix every one:\n"
                   + "\n".join(f"- {e}" for e in gate_errors))
    if revise_notes:
        prompt += f"\n\nReviewer notes to address:\n{revise_notes}"
    return prompt


def _critic_prompt(plan: dict, beats: list[dict]) -> str:
    claims = "\n".join(f"- {c['text']}" for s in plan["steps"] for c in s["claims"]
                       if c.get("verdict") == "supported")
    return (f"Verified claims the script may rely on:\n{claims}\n\nScript:\n"
            + json.dumps([{"beat": b["beat"], "narration": b["narration"]} for b in beats],
                         ensure_ascii=False, indent=2))


# ── Writing loop ─────────────────────────────────────────────────────────────
def _write_until_gates_pass(plan, post_meta, factsheet, brand, extra, local_only, *,
                            revise_notes: str = "") -> tuple[list[dict], int]:
    errors: list[str] = []
    for attempt in range(1, config.LLM_MAX_RETRIES + 1):
        doc = text_llm.generate_schema_json(
            _writer_prompt(plan, post_meta, brand, gate_errors=errors or None,
                           revise_notes=revise_notes),
            _WRITER_SYSTEM, SCRIPT_SCHEMA, local_only=local_only, role="writer")
        beats, errors = build_beats(doc, plan, factsheet, brand)
        if not errors:
            errors = run_gates(beats, factsheet, brand, extra_by_beat=extra)
        if not errors:
            return beats, attempt
        logger.warning(f"script gates failed (attempt {attempt}/{config.LLM_MAX_RETRIES}): "
                       f"{errors}")
    raise HoldForReview(errors)


def run(ctx) -> dict[str, str]:
    """Run the SCRIPT stage and write script.json.

    Raises:
        EngineError: the plan on disk is not verified (run verify_claims first).
        HoldForReview: gates still failing after LLM_MAX_RETRIES attempts, or the final
            critique still below the bar after config.SCRIPT_MAX_REWRITES rewrites.
    """
    ws = Path(ctx.workspace)
    plan = json.loads((ws / "explanation_plan.json").read_text(encoding="utf-8"))
    if plan.get("status") != "verified":
        raise EngineError("explanation_plan.json is not verified; run verify_claims first")
    post_meta = json.loads((ws / "post.json").read_text(encoding="utf-8"))
    factsheet = json.loads((ws / "factsheet.json").read_text(encoding="utf-8"))
    brand = load_brand_facts()
    local_only = bool(ctx.flags.get("local_only", False))
    extra = extra_pool_by_beat(plan)

    beats, attempts = _write_until_gates_pass(plan, post_meta, factsheet, brand, extra,
                                              local_only)
    rewrites = 0
    while True:
        critique = text_llm.generate_schema_json(
            _critic_prompt(plan, beats), _CRITIC_SYSTEM, CRITIQUE_SCHEMA,
            local_only=local_only, role="critic")
        lowest = min(critique["actionable_score"], critique["coherence_score"],
                     critique["hrsu_reason_score"], critique["faithfulness_score"])
        if lowest >= _CRITIQUE_PASS_THRESHOLD:
            break
        if rewrites >= config.SCRIPT_MAX_REWRITES:
            raise HoldForReview([
                f"critique below bar after {rewrites} rewrite(s): {critique['revise_notes']}"])
        rewrites += 1
        logger.info(f"critique below bar ({lowest}), rewrite {rewrites}: "
                    f"{critique['revise_notes']}")
        beats, n = _write_until_gates_pass(plan, post_meta, factsheet, brand, extra,
                                           local_only, revise_notes=critique["revise_notes"])
        attempts += n

    payload = {"beats": beats, "critique": critique, "attempts": attempts,
               "rewrites": rewrites}
    (ws / "script.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                                    encoding="utf-8")
    logger.info(f"script written: {len(beats)} beats, attempts={attempts}, rewrites={rewrites}")
    return {"script": "script.json"}
```

- [ ] **Step 5: Run to verify pass**

Run: `cd _shorts_engine_impl && python -m pytest tests/shorts_engine/test_script_run.py tests/shorts_engine/test_script.py tests/shorts_engine/test_script_gates.py -v`
Expected: all PASS. If a remaining `test_script_gates.py` test fails only because it expected a `budget[` error, change its expectation to another gate's error from the same input; do not reintroduce the budget gate.

- [ ] **Step 6: Commit**

```bash
git add _shorts_engine_impl/shorts_engine/stages/script.py _shorts_engine_impl/tests/shorts_engine/test_script_run.py _shorts_engine_impl/tests/shorts_engine/test_script.py _shorts_engine_impl/tests/shorts_engine/test_script_gates.py
git commit -m "feat: SCRIPT narrates the verified plan; final-script critic loop; no padding

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Wire the new stages (manifest, CLI, harness)

**Files:**
- Modify: `_shorts_engine_impl/shorts_engine/manifest.py` (`STATUS_ORDER`)
- Modify: `_shorts_engine_impl/shorts_engine/cli.py` (imports, `build_stages`, `--until` choices, `until_map`)
- Modify: `_shorts_engine_impl/tests/shorts_engine/test_manifest.py`, `test_cli.py`, `test_integration.py`, `test_integration_phase3.py`
- Modify: `harness/packages/tool-shorts-stage/src/index.ts`, `harness/system_prompt.md`, `harness/cordis.yml`

**Interfaces:**
- Produces: statuses `"explained"` and `"claims_verified"` between `"facts"` and `"scripted"`; stages `("explain","explained",explain.run)` and `("verify_claims","claims_verified",verify_claims.run)` between `facts` and `script`; harness tools `stage_explain`, `stage_verify_claims`.

- [ ] **Step 1: Update the tests first**

`test_manifest.py` `test_status_order_values`: the `expected` list becomes `["init","ingested","facts","explained","claims_verified","scripted","shotlisted","audio","visuals","assembled","verified","packaged","published"]`.

`test_cli.py`:
- `test_build_stages_script_stage`: use `stages[4]` instead of `stages[2]`.
- `test_build_stages_has_seven_in_order`: replace names/statuses assertions with
```python
        assert names[:9] == ["ingest", "facts", "explain", "verify_claims", "script",
                             "shotlist", "audio", "visuals", "assemble"]
        statuses = [s[1] for s in build_stages()]
        assert statuses[:9] == ["ingested", "facts", "explained", "claims_verified",
                                "scripted", "shotlisted", "audio", "visuals", "assembled"]
```
- Add:
```python
    def test_build_stages_explain_and_verify_claims(self):
        from shorts_engine.cli import build_stages
        from shorts_engine.stages import explain, verify_claims
        stages = build_stages()
        assert stages[2] == ("explain", "explained", explain.run)
        assert stages[3] == ("verify_claims", "claims_verified", verify_claims.run)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd _shorts_engine_impl && python -m pytest tests/shorts_engine/test_manifest.py tests/shorts_engine/test_cli.py -v`
Expected: FAIL on the changed assertions.

- [ ] **Step 3: Implement the Python wiring**

`manifest.py`: insert `"explained", "claims_verified",` after `"facts",` in `STATUS_ORDER`.

`cli.py`:
- `from shorts_engine.stages import (facts, ingest, script, shotlist, audio, verify, package, publish,)` -> add `explain, verify_claims,`.
- In `build_stages()` insert after the `facts` tuple:
```python
        ("explain", "explained", explain.run),
        ("verify_claims", "claims_verified", verify_claims.run),
```
- `--until` `choices=[...]`: add `"explain", "verify_claims"` after `"facts"`.
- `until_map`: add `"explain": "explained", "verify_claims": "claims_verified",`.
- Update the module docstring's `--until {...}` line the same way.

- [ ] **Step 4: Fix the integration tests that drive facts -> script directly**

In `tests/shorts_engine/test_integration.py` (around lines 160-231) and `tests/shorts_engine/test_integration_phase3.py` (around lines 60-135) the old flow calls `script_stage.run` right after facts with `{"beats": GOOD_BEATS}` for the old `SCRIPT_SCHEMA`. Replace each such block so the router answers by schema identity for the four LLM calls the new flow makes, and checkpoint the two new stages first:

```python
        from shorts_engine.stages import explain as explain_stage, verify_claims as vc_stage
        # planner -> a verified-able plan whose blog_stated quotes exist in canonical.txt
        # writer  -> {"hook": {...}, "steps": [...], "cta": {...}} for script_stage.SCRIPT_SCHEMA
        # critic  -> a dict matching script_stage.CRITIQUE_SCHEMA (all four scores >= 7)
        monkeypatch.setattr(explain_stage.text_llm, "generate_schema_json", router)
        ctx.manifest.checkpoint("explained", **explain_stage.run(ctx))
        monkeypatch.setattr(vc_stage, "Retriever", lambda urls: _EmptyRetriever())
        monkeypatch.setattr(vc_stage.text_llm, "generate_schema_json", router)
        ctx.manifest.checkpoint("claims_verified", **vc_stage.run(ctx))
        monkeypatch.setattr(script_stage.text_llm, "generate_schema_json", router)
        ctx.manifest.checkpoint("scripted", **script_stage.run(ctx))
```
with `class _EmptyRetriever: def retrieve(self, claim_text): return []`. Build the plan from the fixture post's real sentences (use `blog_stated` claims with quotes copied from `canonical.txt` of the fixture, plus `reasoning` claims) so VERIFY passes with no web. Update assertions that referenced `len(script["beats"]) == 5` / `beats[3]["fact_ids"] == ["f1"]` to the new shape (`beats[0]["beat"] == "hook"`, `beats[-1]["fact_ids"] == ["b_purity"]`, `script_stage.run_gates(..., extra_by_beat=script_stage.extra_pool_by_beat(plan)) == []`). The two tests that assert a gate failure raises `GateFailure` (test_integration.py:149-183) now assert `HoldForReview`.

- [ ] **Step 5: Harness wiring**

`harness/packages/tool-shorts-stage/src/index.ts` — add after the `stage_facts` entry:

```ts
  { toolName: 'stage_explain', description: 'Plan the explanation (question, causal steps, tagged claims) from the full article.', stageName: 'explain' },
  { toolName: 'stage_verify_claims', description: 'Verify every claim against the article or retrieved sources; drop or repair what fails.', stageName: 'verify_claims' },
```

In BOTH `harness/system_prompt.md` (lines 4 and 31) and the identical text inside `harness/cordis.yml` (lines 126 and 153):
- status chain: `init → ingested → facts → explained → claims_verified → scripted → shotlisted → ...`
- stage tool list: add `stage_explain`, `stage_verify_claims` after `stage_facts`.
- Under "Non-negotiable invariants" replace invariant 3 with: `3. **Verified facts only.** Every factual claim in the script was verified by the explain/verify_claims stages against the article or a retrieved source; claims that cannot be verified are dropped, never softened.`
- Add this paragraph to "How to use your tools": `If a stage tool returns {"status": "hold", ...}, the run is in hold_for_review: stop immediately, do not call any later stage, and report the "reasons" array to the user verbatim.`

- [ ] **Step 6: Run to verify pass**

Run: `cd _shorts_engine_impl && python -m pytest tests/shorts_engine -x -q`
Expected: PASS (existing slow-marked tests excluded by default behaviour).
Run: `cd harness && pnpm vitest run packages/tool-shorts-stage`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add _shorts_engine_impl/shorts_engine/manifest.py _shorts_engine_impl/shorts_engine/cli.py _shorts_engine_impl/tests harness/packages/tool-shorts-stage/src/index.ts harness/system_prompt.md harness/cordis.yml
git commit -m "feat: wire explain and verify_claims stages into manifest, CLI and harness

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Forward `visual_intent` through SHOTLIST (gated on the user's WIP)

> **Pre-condition:** `shotlist.py` and `tests/shorts_engine/test_shotlist.py` have uncommitted user changes. Ask the user to commit or stash them first. Do not start otherwise.

**Files:**
- Modify: `_shorts_engine_impl/shorts_engine/stages/shotlist.py` (the `if purpose == "mechanism":` block, around line 177)
- Test: `_shorts_engine_impl/tests/shorts_engine/test_shotlist.py` (add one test)

**Interfaces:**
- Consumes: beats from Task 7 carrying `visual_intent`.
- Produces: every mechanism shot's `payload["visual_intent"]` equals the beat's `visual_intent` (sub-project 2's diagram brief).

- [ ] **Step 1: Write the failing test** — append inside `class TestFreeformShotPlanning` in `test_shotlist.py`, right after `test_multi_shot_mechanism_beat_gets_a_focus_label_per_shot` (it uses the file's existing module-level `_beat` and `_fake_brand` helpers):

```python
    def test_mechanism_shots_carry_the_beats_visual_intent(self):
        """The plan's visual_intent is the diagram brief for sub-project 2."""
        from shorts_engine.stages.shotlist import plan_beat_shots
        intent = {"entities": ["sand", "water"], "relationship": "water leaves sand",
                  "quantity": "nutrient"}
        beat = {**_beat("step_1", "mechanism", "Sandy soil drains nutrients quickly.",
                        card_text="Fast drainage"),
                "visual_intent": intent}
        shots = plan_beat_shots(beat, {}, {}, _fake_brand())
        assert shots and all(s["payload"]["visual_intent"] == intent for s in shots)

    def test_beats_without_visual_intent_keep_their_payload_unchanged(self):
        from shorts_engine.stages.shotlist import plan_beat_shots
        shots = plan_beat_shots(_beat("m", "mechanism", "Short narration here.",
                                      card_text="Card"), {}, {}, _fake_brand())
        assert all("visual_intent" not in s["payload"] for s in shots)
```

- [ ] **Step 2: Run to verify failure** — `cd _shorts_engine_impl && python -m pytest tests/shorts_engine/test_shotlist.py -k visual_intent -v` -> FAIL (`KeyError: 'visual_intent'`).

- [ ] **Step 3: Implement** — inside `if purpose == "mechanism":` add as the first statement:

```python
            if beat.get("visual_intent"):
                payload["visual_intent"] = beat["visual_intent"]
```

- [ ] **Step 4: Run** — `cd _shorts_engine_impl && python -m pytest tests/shorts_engine/test_shotlist.py -v` -> PASS.

- [ ] **Step 5: Commit**

```bash
git add _shorts_engine_impl/shorts_engine/stages/shotlist.py _shorts_engine_impl/tests/shorts_engine/test_shotlist.py
git commit -m "feat: shotlist forwards each mechanism beat's visual_intent as the diagram brief

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 10: `--print-plan` dry-run report

**Files:**
- Create: `_shorts_engine_impl/shorts_engine/review/plan_report.py`
- Modify: `_shorts_engine_impl/shorts_engine/cli.py` (new flag; print after the run)
- Test: `_shorts_engine_impl/tests/shorts_engine/test_plan_report.py` (create)

**Interfaces:**
- Produces: `plan_report.format_plan(plan: dict) -> str`; CLI flag `--print-plan` prints `format_plan` of `<workspace>/explanation_plan.json` after the run (also when a stage holds). Use with `--until verify_claims` to see the plan and verdicts without rendering anything.

- [ ] **Step 1: Write the failing test**

```python
# tests/shorts_engine/test_plan_report.py
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
```

- [ ] **Step 2: Run to verify failure** — `cd _shorts_engine_impl && python -m pytest tests/shorts_engine/test_plan_report.py -v` -> FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

```python
# shorts_engine/review/plan_report.py
"""Human-readable dry-run report of explanation_plan.json (question, steps, verdicts)."""
from __future__ import annotations


def format_plan(plan: dict) -> str:
    lines = [f"Question: {plan.get('question', '')}", f"Status: {plan.get('status', '').upper()}"]
    for reason in plan.get("hold_reasons", []):
        lines.append(f"  HELD: {reason}")
    for step in plan.get("steps", []):
        lines.append(f"\nStep {step['step_id']}: {step['claim_text']}")
        for c in step["claims"]:
            url = (c.get("support") or {}).get("url")
            lines.append(f"  [{c.get('verdict')}] {c['text']} ({c['kind']})"
                         + (f" <- {url}" if url else ""))
    dropped = plan.get("dropped_claims", [])
    if dropped:
        lines.append("\nDROPPED claims:")
        lines += [f"  [{c.get('verdict')}] {c['text']} -- {c.get('reason', '')}" for c in dropped]
    for s in plan.get("dropped_steps", []):
        lines.append(f"DROPPED step {s['step_id']}")
    return "\n".join(lines)
```

In `cli.py` add `parser.add_argument("--print-plan", action="store_true", help="Print the explanation plan and claim verdicts after the run (dry-run review)")`, and a helper called from both the success path and the `except` path of `main` (wrap in try/except so reporting never changes the exit code):

```python
def _maybe_print_plan(args, workspace) -> None:
    if not args.print_plan or workspace is None:
        return
    import json
    from shorts_engine.review.plan_report import format_plan
    path = Path(workspace) / "explanation_plan.json"
    if path.exists():
        print(format_plan(json.loads(path.read_text(encoding="utf-8"))))
```
Call `_maybe_print_plan(args, manifest.workspace)` after the "Pipeline completed" prints. Also print `[HOLD]` instead of `[OK]` when `manifest.status == "hold_for_review"` (return code stays 0) so a hold is visible from the CLI.

- [ ] **Step 4: Run** — `cd _shorts_engine_impl && python -m pytest tests/shorts_engine/test_plan_report.py tests/shorts_engine/test_cli.py -v` -> PASS.

- [ ] **Step 5: Commit**

```bash
git add _shorts_engine_impl/shorts_engine/review/plan_report.py _shorts_engine_impl/shorts_engine/cli.py _shorts_engine_impl/tests/shorts_engine/test_plan_report.py
git commit -m "feat: --print-plan dry-run report of the explanation plan and claim verdicts

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Model reachability check and verifier bake-off

**Files:**
- Create: `scripts/smoke_models.py`, `scripts/eval_verifier.py`
- Create: `_shorts_engine_impl/tests/shorts_engine/fixtures/claims_eval.json`
- Test: `_shorts_engine_impl/tests/shorts_engine/test_eval_verifier.py` (create)

**Interfaces:**
- Consumes: `text_llm.generate_schema_json(..., role=...)`, `stages.verify_claims.VERDICT_SCHEMA`, `config.model_for_role`.
- Produces: `scripts.eval_verifier.score_model(cases, ask) -> dict` where `ask(claim, passages) -> verdict_str`; `python scripts/smoke_models.py [model ...]` prints OK/FAIL per model; `python scripts/eval_verifier.py <model>` prints accuracy per category. Both live scripts are manual (network/model access).

The fixture holds cases `{"claim": str, "passages": [str], "expected": "supported|contradicted|unsupported"}` — at least 6 of each expected class, written from standard, easily checked chemistry (e.g. calcium nitrate is very soluble in water -> supported; "calcium nitrate is insoluble in water" -> contradicted; a claim about an unrelated passage -> unsupported).

- [ ] **Step 1: Write the failing test** (offline; tests the scoring math only)

```python
# tests/shorts_engine/test_eval_verifier.py
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("eval_verifier", ROOT / "scripts" / "eval_verifier.py")
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)


def test_fixture_has_balanced_classes():
    cases = json.loads((Path(__file__).parent / "fixtures" / "claims_eval.json").read_text(encoding="utf-8"))
    for label in ("supported", "contradicted", "unsupported"):
        assert sum(c["expected"] == label for c in cases) >= 6


def test_score_model_reports_per_class_accuracy_and_false_support_rate():
    cases = [{"claim": "a", "passages": ["p"], "expected": "supported"},
             {"claim": "b", "passages": ["p"], "expected": "contradicted"},
             {"claim": "c", "passages": ["p"], "expected": "unsupported"},
             {"claim": "d", "passages": ["p"], "expected": "unsupported"}]
    answers = {"a": "supported", "b": "supported", "c": "unsupported", "d": "supported"}
    res = ev.score_model(cases, lambda claim, passages: answers[claim])
    assert res["per_class"]["supported"] == 1.0
    assert res["per_class"]["contradicted"] == 0.0
    assert res["per_class"]["unsupported"] == 0.5
    # false support = said "supported" when the truth was contradicted/unsupported
    assert res["false_support_rate"] == 2 / 3
```

- [ ] **Step 2: Run to verify failure** — FAIL (`FileNotFoundError` for `scripts/eval_verifier.py` / fixture).

- [ ] **Step 3: Implement**

`scripts/eval_verifier.py`:
```python
"""Score a candidate verifier model on the claims fixture.

Usage: python scripts/eval_verifier.py <model-name>   (needs the model reachable via Ollama)
The metric that matters most is false_support_rate: how often the model calls a
contradicted/unsupported claim "supported" (that is how a wrong fact reaches a video).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "_shorts_engine_impl" / "tests" / "shorts_engine" / "fixtures" / "claims_eval.json"


def score_model(cases: list[dict], ask) -> dict:
    labels = ("supported", "contradicted", "unsupported")
    hits = {l: [0, 0] for l in labels}
    false_support = wrong_total = 0
    for c in cases:
        got = ask(c["claim"], c["passages"])
        hits[c["expected"]][1] += 1
        hits[c["expected"]][0] += got == c["expected"]
        if c["expected"] != "supported":
            wrong_total += 1
            false_support += got == "supported"
    return {"per_class": {l: (h / n if n else 0.0) for l, (h, n) in hits.items()},
            "false_support_rate": false_support / wrong_total if wrong_total else 0.0}


def _ask_factory(model: str):
    sys.path.insert(0, str(ROOT / "_shorts_engine_impl"))
    sys.path.insert(0, str(ROOT))
    from video_agent.ollama_client import OllamaClient
    from shorts_engine.llm import text_llm
    from shorts_engine.stages import verify_claims as vc
    client = OllamaClient(model=model)
    if "cloud" in model:
        client.generate = lambda prompt, system=None, **_k: client._generate_via_sdk(prompt, system)

    def ask(claim, passages):
        listing = "\n".join(f"[{i}] {p}" for i, p in enumerate(passages))
        res = text_llm.generate_schema_json(
            f"Claim: {claim}\n\nPassages:\n{listing}", vc._VERIFIER_SYSTEM, vc.VERDICT_SCHEMA,
            client_factory=lambda: client)
        return res["verdict"]
    return ask


if __name__ == "__main__":
    model = sys.argv[1]
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))
    print(model, json.dumps(score_model(cases, _ask_factory(model)), indent=2))
```

`scripts/smoke_models.py`:
```python
"""Confirm each model answers a trivial JSON prompt through the same client path the pipeline
uses. Needed because this account's cloud access varies by model (kimi-k2.7-code returned 403;
see harness/cordis.yml). Usage: python scripts/smoke_models.py [model ...]
Default models: gemma4:31b-cloud glm-5.2:cloud glm-5.1:cloud nemotron-3-ultra:cloud minimax-m3:cloud
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "_shorts_engine_impl"))
sys.path.insert(0, str(ROOT))

DEFAULTS = ["gemma4:31b-cloud", "glm-5.2:cloud", "glm-5.1:cloud",
            "nemotron-3-ultra:cloud", "minimax-m3:cloud"]


def main(models: list[str]) -> int:
    from video_agent.ollama_client import OllamaClient
    bad = 0
    for model in models:
        try:
            client = OllamaClient(model=model)
            if "cloud" in model:
                client.generate = lambda p, system=None, _c=client, **_k: _c._generate_via_sdk(p, system)
            out = client.generate_json('Reply with exactly {"ok": true}', retries=1)
            print(f"OK    {model}: {out}")
        except Exception as exc:
            bad += 1
            print(f"FAIL  {model}: {exc}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or DEFAULTS))
```

`tests/shorts_engine/fixtures/claims_eval.json`: write 18+ cases in the format above (>=6 per class), e.g.
```json
[
  {"claim": "Calcium nitrate is very soluble in water.", "passages": ["Calcium nitrate dissolves readily in water, with solubility above 100 g per 100 mL at room temperature."], "expected": "supported"},
  {"claim": "Calcium nitrate is insoluble in water.", "passages": ["Calcium nitrate dissolves readily in water, with solubility above 100 g per 100 mL at room temperature."], "expected": "contradicted"},
  {"claim": "Calcium nitrate is very soluble in water.", "passages": ["Sodium chloride is used to de-ice roads in winter."], "expected": "unsupported"}
]
```
(Write all 18+: supported/contradicted/unsupported variants for calcium nitrate solubility, ANFO oxidiser role, H2S control by nitrate dosing, concrete set acceleration by calcium nitrate, nitrate as an electron acceptor for denitrifying bacteria, and fertigation use. Each `passages` entry must be a real, checkable statement.)

- [ ] **Step 4: Run** — `cd _shorts_engine_impl && python -m pytest tests/shorts_engine/test_eval_verifier.py -v` -> PASS.

- [ ] **Step 5: Manual live checks (record the results in the commit message)**

Run: `python scripts/smoke_models.py` — note which models answer. Then for each reachable non-Gemma candidate: `python scripts/eval_verifier.py <model>`. Pick the model with the lowest `false_support_rate` (ties: higher per-class accuracy). If it is not `glm-5.2:cloud`, change `MODEL_ROLES["verifier"]`/`["critic"]` in `config.py` to the winner and re-run `tests/shorts_engine/test_model_roles.py`. If **no** non-Gemma model is reachable, stop and report to the user; do not fall back to Gemma (the independence check will refuse anyway).

- [ ] **Step 6: Commit**

```bash
git add scripts/smoke_models.py scripts/eval_verifier.py _shorts_engine_impl/tests/shorts_engine/test_eval_verifier.py _shorts_engine_impl/tests/shorts_engine/fixtures/claims_eval.json _shorts_engine_impl/shorts_engine/config.py
git commit -m "feat: model reachability smoke check and verifier bake-off harness

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Regression replay of `run-e721c494`

**Files:**
- Create: `_shorts_engine_impl/tests/shorts_engine/fixtures/regression_run_e721c494/{canonical.txt,post.json,factsheet.json}` (copy from `output/shorts/run-e721c494/`)
- Test: `_shorts_engine_impl/tests/shorts_engine/test_regression_foliar_soil.py` (create)

The saved run is the post that produced the foliar-to-soil jump and the unit-less `425`. The test replays EXPLAIN -> VERIFY with a scripted fake planner that behaves like the bad old writer (soil-infiltration claims presented as proof of a foliar-solubility point, a unit-less `425`), and asserts the new pipeline refuses to ship it.

- [ ] **Step 1: Copy the fixtures**

```bash
mkdir -p _shorts_engine_impl/tests/shorts_engine/fixtures/regression_run_e721c494
cp output/shorts/run-e721c494/canonical.txt output/shorts/run-e721c494/post.json output/shorts/run-e721c494/factsheet.json _shorts_engine_impl/tests/shorts_engine/fixtures/regression_run_e721c494/
```

- [ ] **Step 2: Write the test**

```python
# tests/shorts_engine/test_regression_foliar_soil.py
"""Replay of run-e721c494: a foliar-blend post whose old script jumped to desert soil and
printed '425 EC' with no unit. The new pipeline must not ship that."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from shorts_engine import explanation as ex
from shorts_engine.brand import load_brand_facts
from shorts_engine.errors import HoldForReview
from shorts_engine.manifest import RunManifest
from shorts_engine.runner import StageContext
from shorts_engine.stages import explain, verify_claims as vc

FIX = Path(__file__).parent / "fixtures" / "regression_run_e721c494"
EC_QUOTE = "electrical conductivity (EC) peaking at 425"


def _ctx(tmp_path):
    m = RunManifest.create("https://hrsuindore.com/blog/calcium-nitrate-solubility-in-precision/",
                           workspace_root=tmp_path)
    ws = Path(m.workspace)
    for name in ("canonical.txt", "post.json", "factsheet.json"):
        shutil.copy(FIX / name, ws / name)
    return StageContext(manifest=m, workspace=ws, flags={})


def _step(sid, claims):
    return {"step_id": sid, "claim_text": "goal", "claims": claims, "terms": [],
            "visual_intent": {"entities": ["soil"], "relationship": "r", "quantity": "q"}}


def _claim(cid, text, kind, quote=""):
    return {"id": cid, "text": text, "kind": kind, "quote": quote}


def _plan(steps):
    return {"question": "Why does soil retention matter for foliar blends?", "steps": steps,
            "payoff": {"takeaway": "Choose a soluble powder.", "differentiator_id": "b_purity"}}


def test_unitless_ec_number_is_rejected_at_plan_time():
    canonical = (FIX / "canonical.txt").read_text(encoding="utf-8")
    plan = _plan([_step(f"s{i}", [_claim(f"c{i}", "EC peaks at 425 in sandy soil.",
                                         "blog_stated", EC_QUOTE)]) for i in (1, 2, 3)])
    errs = ex.validate_plan(plan, canonical, load_brand_facts())
    assert any("425" in e and "unit" in e for e in errs)


def test_soil_to_foliar_leap_is_dropped_when_nothing_supports_it(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    ok_quote = "a 20% calcium carbonate and 80% river sand mixture"
    steps = [
        _step("s1", [_claim("c1", "Engineers model desert soil with a sand mixture.",
                            "blog_stated", ok_quote)]),
        _step("s2", [_claim("c2", "Soil infiltration data proves foliar solubility.",
                            "external_fact")]),
        _step("s3", [_claim("c3", "So foliar sprays never clog nozzles.", "reasoning")]),
    ]
    monkeypatch.setattr(explain.text_llm, "generate_schema_json",
                        lambda p, s, sc, **kw: _plan(steps))
    explain.run(ctx)

    class NoWeb:
        def retrieve(self, claim_text):
            return []

    monkeypatch.setattr(vc, "Retriever", lambda urls: NoWeb())

    def fake_llm(prompt, system, schema, **kw):
        if schema is vc.VERDICT_SCHEMA:      # logic check on c3: premises don't carry the leap
            return {"verdict": "unsupported", "passage_index": None}
        return {"claims": []}                # planner repair finds nothing

    monkeypatch.setattr(vc.text_llm, "generate_schema_json", fake_llm)
    with pytest.raises(HoldForReview):
        vc.run(ctx)
    doc = json.loads((ctx.workspace / "explanation_plan.json").read_text(encoding="utf-8"))
    assert doc["status"] == "held"
    dropped = {c["id"] for c in doc["dropped_claims"]}
    assert {"c2", "c3"} <= dropped            # the unsupported leap and the claim built on it
```

- [ ] **Step 3: Run** — `cd _shorts_engine_impl && python -m pytest tests/shorts_engine/test_regression_foliar_soil.py -v` -> PASS. (If `test_unitless_ec_number...` fails because the fixture's canonical text phrases the sentence differently, copy the exact sentence containing `425` from `canonical.txt` into `EC_QUOTE`.)

- [ ] **Step 4: Commit**

```bash
git add _shorts_engine_impl/tests/shorts_engine/test_regression_foliar_soil.py _shorts_engine_impl/tests/shorts_engine/fixtures/regression_run_e721c494
git commit -m "test: regression replay of the foliar-to-soil run must not ship

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Packaging format label and run-size logging

**Files:**
- Modify: `_shorts_engine_impl/shorts_engine/stages/package.py` (add `format_label`; write `"format"` into `publish_package.json`)
- Test: `_shorts_engine_impl/tests/shorts_engine/test_package_publish.py` (add tests)

**Interfaces:**
- Produces: `package.format_label(duration_s: float | None) -> str` returning `"short"` when `duration_s <= config.SHORT_FORM_MAX_S`, `"long"` when above, `"unknown"` for `None`; `publish_package.json` gains `"format"` and `"duration_s"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_format_label_boundaries():
    from shorts_engine import config
    from shorts_engine.stages.package import format_label
    assert format_label(45.0) == "short"
    assert format_label(config.SHORT_FORM_MAX_S) == "short"
    assert format_label(config.SHORT_FORM_MAX_S + 0.1) == "long"
    assert format_label(None) == "unknown"
```
and, reusing the existing happy-path test's setup in this file (the test at `test_package_writes_all_artifacts`), a second test that monkeypatches `shorts_engine.stages.package.probe_duration` to return `240.0`, runs `package.run`, and asserts `publish_package.json` has `"format": "long"` and `"duration_s": 240.0`; and a third where `video_short.mp4` is absent and the file has `"format": "unknown"`.

- [ ] **Step 2: Run to verify failure** — `cd _shorts_engine_impl && python -m pytest tests/shorts_engine/test_package_publish.py -k "format" -v` -> FAIL.

- [ ] **Step 3: Implement**

In `package.py` add `from shorts_engine import config` and `from shorts_engine.cards.encoder import probe_duration` to the imports, then:

```python
def format_label(duration_s: float | None) -> str:
    """'short' (<= config.SHORT_FORM_MAX_S), 'long', or 'unknown' when duration is not known."""
    if duration_s is None:
        return "unknown"
    return "short" if duration_s <= config.SHORT_FORM_MAX_S else "long"
```

In `run()` just before writing `publish_package.json`:
```python
    video = ws / "video_short.mp4"
    try:
        duration_s = round(probe_duration(video), 2) if video.exists() else None
    except Exception as exc:
        logger.warning(f"package: could not probe duration: {exc}")
        duration_s = None
```
and add `"format": format_label(duration_s), "duration_s": duration_s,` to the dict written to `publish_package.json`.

- [ ] **Step 4: Run** — `cd _shorts_engine_impl && python -m pytest tests/shorts_engine/test_package_publish.py -v` -> PASS.

- [ ] **Step 5: Final full-suite run and commit**

Run: `cd _shorts_engine_impl && python -m pytest tests -q` -> PASS (slow-marked tests excluded unless `-m slow`).
Run: `cd harness && pnpm vitest run` -> PASS.

```bash
git add _shorts_engine_impl/shorts_engine/stages/package.py _shorts_engine_impl/tests/shorts_engine/test_package_publish.py
git commit -m "feat: package labels each video short-form or long-form from its duration

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage**

| Spec section | Task |
|---|---|
| 5 plan schema, claim kinds, numbers+units, terms, visual_intent, payoff | 3 (schema/gates), 5 (EXPLAIN) |
| 5 verifier independence | 1 (`check_role_independence`), 6 (role="verifier") |
| 6 retrieval tool (search, ranking, cache, never stops on empty) | 4; empty-search behaviour pinned in 6 (`test_unsupported_claims_are_dropped...`, no-retrieval test) |
| 7 repair rounds, re-verify, drop, hold, never pad | 6 (repair/prune/hold), 7 (final critic re-score, hold) |
| 8 model roles + bake-off + reachability | 1, 11 |
| 8 reuse (separate packages, brand in yaml) | 3-6 are separate modules; brand untouched |
| 9 kept/removed/changed script logic, `step_id`, no ceiling, short/long label, size logging | 7, 13; size logging is in 5/6 `logger.info` lines |
| 4 SHOTLIST passes `visual_intent` | 9 (gated on WIP) |
| 10 testing incl. regression, `--dry-run` report | each task's tests; 10, 12 |
| Harness stage chain + hold rule | 8 |

**Placeholder scan:** no TBD/TODO. Two spots rely on the implementer reading existing files rather than reproducing them: Task 8 step 4 (rewiring the two integration tests onto the new stage flow; it gives the exact call sequence and assertions but not the full rewritten files) and Task 11's fixture (lists required topics and format rather than all 18 cases verbatim).

**Type consistency:** `finalize_claim` shape (Task 3) is what `verify_claim` (Task 6) reads (`claim["support"]["quote"]`); `extra_pool_by_beat` keys (`step_N`) match `build_beats` beat names (Task 7); `VERDICT_SCHEMA`/`REPAIR_SCHEMA` identity is what the Task 6/12 routers switch on; `HoldForReview.reasons` (Task 2) is what Tasks 5-7 and 12 assert on.

**Known risks the implementer should watch:** (1) the account's cloud model access varies (see `harness/cordis.yml`: kimi 403) - Task 11's smoke check decides the verifier default, and the code never falls back to Gemma; (2) `OllamaClient._generate_via_sdk` for non-Gemma cloud models is assumed to work like it does for Gemma - Task 11 verifies it live; (3) pre-existing: `package.py` finds the hook beat by name `hook`, which the old free-form beats (`Introduction`...) would have broken; Task 7's beat naming fixes it.
