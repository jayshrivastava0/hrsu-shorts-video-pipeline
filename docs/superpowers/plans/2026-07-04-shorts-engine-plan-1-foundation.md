# Shorts Engine — Plan 1 of 3: Foundation (Skeleton + INGEST + FACTS + SCRIPT)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `shorts_engine` package skeleton (manifest, runner, CLI, LLM helper) plus the first three pipeline stages, so that `python -m shorts_engine <blog_url> --until scripted` turns a live HRSU blog post into a gate-verified, verbatim-grounded 5-beat video script.

**Architecture:** Clean-slate package `shorts_engine/` per spec `docs/superpowers/specs/2026-07-04-shorts-engine-design.md` §3. Ten checkpointed stages over a durable JSON manifest; this plan implements stages 1–3 (INGEST → FACTS → SCRIPT). Zero imports from the old creative stack; reuses `video_agent` leaf modules (`config`, `ollama_client`) as libraries. All LLM calls are schema-validated JSON against `gemma4:31b-cloud` with retry-with-error-echo and **no silent model fallback**.

**Tech Stack:** Python 3.11+, BeautifulSoup4, PyYAML, jsonschema, requests, pytest. LLM via existing `video_agent.ollama_client.OllamaClient` (SDK transport, `SMART_TEXT_TRANSPORT = "sdk"`).

**Plan roadmap:** Plan 1 (this doc) = spec Phases 1–2. Plan 2 = spec Phases 3–4 (cards, shotlist, audio, assemble → torture-test video). Plan 3 = spec Phases 5–7 (BROLL ladder, PAPER_CARD, verify loop, publish).

## Global Constraints

- **No git.** This project does not use git. Task steps end with test runs, never commits.
- **Smart model:** `SMART_TEXT_MODEL = "gemma4:31b-cloud"` (import from `video_agent.config`). Local fallback `OLLAMA_MODEL = "gemma3:4b"` is used ONLY when the CLI flag `--local-only` is explicitly passed; it is never an automatic fallback (spec §8.2).
- **Verbatim-only facts:** every FactSheet entry must string-locate in `canonical.txt` after normalization; every numeric token in narration/card text must trace to a referenced fact (spec §2, §4).
- **Approved differentiator ids:** `b_purity`, `b_supply`, `b_esg` (from `brand_facts.yaml`). REACH/certification claims are hard-banned (spec §7).
- **Word rate:** 2.6 words/sec, ±20% tolerance per beat (spec §4 Stage 3).
- **Beat template (exact):** hook 2–4s · stakes 4–6s · mechanism 8–12s · proof 6–10s · cta 6–8s.
- **Test command:** `python -m pytest tests/shorts_engine -q` must pass at the end of every task; pre-existing suites (`python -m pytest tests -q`) must not regress at plan end.
- **Windows paths:** run commands from project root `E:\Projects\HRSU Blog`. Use `python`, not `python3`.
- All new modules start with `from __future__ import annotations` and use `logging.getLogger(__name__)`.

---

### Task 1: Package skeleton, errors, config, forbidden-import guard

**Files:**
- Create: `shorts_engine/__init__.py`
- Create: `shorts_engine/errors.py`
- Create: `shorts_engine/config.py`
- Create: `shorts_engine/llm/__init__.py`
- Create: `shorts_engine/stages/__init__.py`
- Create: `tests/shorts_engine/__init__.py`
- Test: `tests/shorts_engine/test_boundaries.py`

**Interfaces:**
- Consumes: `video_agent.config.SMART_TEXT_MODEL`, `OLLAMA_MODEL`, `SCRIPT_BANNED_PHRASES` (existing).
- Produces: `shorts_engine.config` constants (`BEAT_TEMPLATE`, `WORDS_PER_SECOND`, `WORD_BUDGET_TOLERANCE`, `FEAR_FILLER_PATTERNS`, `PAPER_DOMAINS`, `STANDARD_DOMAINS`, `LLM_MAX_RETRIES`, `OUTPUT_BASE`, `BRAND_FACTS_PATH`, `PROJECT_ROOT`); exceptions `EngineError`, `EngineConfigError`, `EngineLLMError`, `GateFailure` — all later tasks import these.

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_boundaries.py
from __future__ import annotations
from pathlib import Path

FORBIDDEN_IMPORT_SUBSTRINGS = [
    "video_agent.agents", "video_agent.orchestrator", "video_agent.storyboard",
    "video_agent.script_builder", "video_agent.composer",
    "video_agent.harness.runner", "video_agent.harness.rubric",
    "video_agent.harness.revise_router", "video_agent.harness.verify_vision",
    "video_agent.run_stage", "video_agent.visual_engine.footage_library",
    "video_agent.visual_engine.factory_broll", "video_agent.visual_engine.dispatcher",
    "video_agent.motion", "video_agent.sources.scoring",
]

def test_no_forbidden_imports():
    pkg = Path("shorts_engine")
    assert pkg.is_dir(), "shorts_engine package must exist"
    offenders = []
    for py in pkg.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for bad in FORBIDDEN_IMPORT_SUBSTRINGS:
            if bad in text:
                offenders.append(f"{py}: {bad}")
    assert offenders == [], f"forbidden old-stack imports found: {offenders}"

def test_config_constants():
    from shorts_engine import config
    assert config.WORDS_PER_SECOND == 2.6
    assert config.WORD_BUDGET_TOLERANCE == 0.20
    beats = [b["beat"] for b in config.BEAT_TEMPLATE]
    assert beats == ["hook", "stakes", "mechanism", "proof", "cta"]
    assert config.BEAT_TEMPLATE[2] == {"beat": "mechanism", "min_s": 8.0, "max_s": 12.0}
    assert config.LLM_MAX_RETRIES == 3
    assert config.SMART_TEXT_MODEL == "gemma4:31b-cloud"

def test_exceptions_hierarchy():
    from shorts_engine.errors import EngineError, EngineConfigError, EngineLLMError, GateFailure
    assert issubclass(EngineConfigError, EngineError)
    assert issubclass(EngineLLMError, EngineError)
    gf = GateFailure(["numbers: 42 untraced"])
    assert issubclass(GateFailure, EngineError)
    assert gf.errors == ["numbers: 42 untraced"]
    assert "42 untraced" in str(gf)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/shorts_engine/test_boundaries.py -v`
Expected: FAIL / ERROR with `ModuleNotFoundError: No module named 'shorts_engine'` (after the package dir assert passes once dirs exist; initial run may fail on the first assert — both are acceptable failure shapes).

- [ ] **Step 3: Create the package files**

```python
# shorts_engine/__init__.py
"""shorts_engine — clean-slate blog→short-video pipeline (spec 2026-07-04)."""
```

```python
# shorts_engine/errors.py
from __future__ import annotations


class EngineError(RuntimeError):
    """Base class for all shorts_engine failures."""


class EngineConfigError(EngineError):
    """Missing/invalid configuration (e.g., brand_facts.yaml absent)."""


class EngineLLMError(EngineError):
    """LLM call failed after retries. Never silently recovered."""


class GateFailure(EngineError):
    """One or more deterministic gates rejected an artifact."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))
```

```python
# shorts_engine/config.py
"""Engine knobs. Brand/model constants come from video_agent.config (allowed leaf)."""
from __future__ import annotations
from pathlib import Path

from video_agent.config import (  # noqa: F401  (re-exported for engine modules)
    SMART_TEXT_MODEL, OLLAMA_MODEL, SCRIPT_BANNED_PHRASES,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_BASE = PROJECT_ROOT / "output" / "videos"
BRAND_FACTS_PATH = PROJECT_ROOT / "brand_facts.yaml"
BLOG_HISTORY_PATH = PROJECT_ROOT / "blog_history.json"

LLM_MAX_RETRIES = 3

# ─── Script pacing (spec §4 Stage 3) ───────────────────────────────────────
WORDS_PER_SECOND = 2.6
WORD_BUDGET_TOLERANCE = 0.20
BEAT_TEMPLATE: list[dict] = [
    {"beat": "hook",      "min_s": 2.0, "max_s": 4.0},
    {"beat": "stakes",    "min_s": 4.0, "max_s": 6.0},
    {"beat": "mechanism", "min_s": 8.0, "max_s": 12.0},
    {"beat": "proof",     "min_s": 6.0, "max_s": 10.0},
    {"beat": "cta",       "min_s": 6.0, "max_s": 8.0},
]

# Fear-filler banned on top of video_agent SCRIPT_BANNED_PHRASES (spec §4).
FEAR_FILLER_PATTERNS = [
    "is everything", "crippling", "game-changer", "game changer",
    "revolutionary", "catastrophic", "skyrocket",
]

# ─── Citation classification (spec §4 Stage 1) ─────────────────────────────
PAPER_DOMAINS = (
    "springer", "sciencedirect", "mdpi", "wiley", "tandfonline",
    "nature.com", "acs.org", "rsc.org", "pubmed", "ncbi.nlm.nih.gov",
    "arxiv", "doi.org",
)
STANDARD_DOMAINS = ("europa.eu", "eur-lex", "epa.gov", "iso.org")
```

```python
# shorts_engine/llm/__init__.py
```

```python
# shorts_engine/stages/__init__.py
```

```python
# tests/shorts_engine/__init__.py
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/shorts_engine/test_boundaries.py -v`
Expected: 3 passed.

- [ ] **Step 5: Run the suite**

Run: `python -m pytest tests/shorts_engine -q`
Expected: 3 passed.

---

### Task 2: Run manifest (durable state, checkpoint, resume)

**Files:**
- Create: `shorts_engine/manifest.py`
- Test: `tests/shorts_engine/test_manifest.py`

**Interfaces:**
- Consumes: nothing engine-internal.
- Produces (used by runner/cli and all stages):
  - `STATUS_ORDER: list[str]` = `["init","ingested","facts","scripted","shotlisted","audio","visuals","assembled","verified","packaged","published"]`
  - `slug_from_url(url: str) -> str`
  - `@dataclass RunManifest(run_id: str, blog_url: str, slug: str, status: str, workspace: str, artifacts: dict[str, str], model_tier: str = "cloud", error: str | None = None, created_at: str = "", updated_at: str = "")`
  - `RunManifest.create(blog_url: str, workspace_root: Path) -> RunManifest` (mkdir workspace, status="init", saves)
  - `RunManifest.load(workspace: str | Path) -> RunManifest`
  - `RunManifest.checkpoint(status: str, **artifacts: str) -> None` (merge artifacts, set status, save)
  - `RunManifest.save() -> None` (writes `<workspace>/run_manifest.json`, refreshes `updated_at`)

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_manifest.py
from __future__ import annotations
import json
from pathlib import Path

from shorts_engine.manifest import RunManifest, STATUS_ORDER, slug_from_url

URL = "https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html"


def test_slug_from_url_strips_extension_and_paths():
    assert slug_from_url(URL) == "optimizing-nitrate-removal-via-granular"
    assert slug_from_url("https://x.com/a/b/My_Post.html") == "my-post"
    assert slug_from_url("https://x.com/post/") == "post"


def test_status_order_matches_spec():
    assert STATUS_ORDER == ["init", "ingested", "facts", "scripted", "shotlisted",
                            "audio", "visuals", "assembled", "verified",
                            "packaged", "published"]


def test_create_save_load_roundtrip(tmp_path: Path):
    m = RunManifest.create(URL, workspace_root=tmp_path)
    ws = Path(m.workspace)
    assert ws.is_dir() and ws.name == "optimizing-nitrate-removal-via-granular"
    assert m.status == "init" and len(m.run_id) == 12
    on_disk = json.loads((ws / "run_manifest.json").read_text(encoding="utf-8"))
    assert on_disk["blog_url"] == URL and on_disk["model_tier"] == "cloud"

    loaded = RunManifest.load(ws)
    assert loaded.run_id == m.run_id and loaded.status == "init"


def test_checkpoint_merges_artifacts_and_advances(tmp_path: Path):
    m = RunManifest.create(URL, workspace_root=tmp_path)
    m.checkpoint("ingested", post="post.json", canonical="canonical.txt")
    m.checkpoint("facts", factsheet="factsheet.json")
    loaded = RunManifest.load(m.workspace)
    assert loaded.status == "facts"
    assert loaded.artifacts == {"post": "post.json",
                                "canonical": "canonical.txt",
                                "factsheet": "factsheet.json"}
    assert loaded.updated_at >= loaded.created_at


def test_load_missing_raises(tmp_path: Path):
    import pytest
    from shorts_engine.errors import EngineConfigError
    with pytest.raises(EngineConfigError):
        RunManifest.load(tmp_path / "nope")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/shorts_engine/test_manifest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shorts_engine.manifest'`.

- [ ] **Step 3: Implement `shorts_engine/manifest.py`**

```python
# shorts_engine/manifest.py
"""Durable JSON run-state. Pattern follows video_agent/harness/manifest.py
(checkpoint after every stage, resumable), with the v3 status set."""
from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

from shorts_engine.errors import EngineConfigError

log = logging.getLogger(__name__)

STATUS_ORDER = ["init", "ingested", "facts", "scripted", "shotlisted",
                "audio", "visuals", "assembled", "verified",
                "packaged", "published"]
EXTRA_STATUSES = ["failed", "hold_for_review"]

_MANIFEST_NAME = "run_manifest.json"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slug_from_url(url: str) -> str:
    last = url.rstrip("/").split("/")[-1]
    last = re.sub(r"\.html?$", "", last, flags=re.IGNORECASE)
    slug = re.sub(r"[^a-z0-9]+", "-", last.lower()).strip("-")
    return slug or "post"


@dataclass
class RunManifest:
    run_id: str
    blog_url: str
    slug: str
    status: str
    workspace: str
    artifacts: dict[str, str] = field(default_factory=dict)
    model_tier: str = "cloud"
    error: str | None = None
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def create(cls, blog_url: str, workspace_root: Path) -> "RunManifest":
        slug = slug_from_url(blog_url)
        ws = Path(workspace_root) / slug
        ws.mkdir(parents=True, exist_ok=True)
        m = cls(run_id=uuid.uuid4().hex[:12], blog_url=blog_url, slug=slug,
                status="init", workspace=str(ws), created_at=_now())
        m.save()
        return m

    @classmethod
    def load(cls, workspace: str | Path) -> "RunManifest":
        path = Path(workspace) / _MANIFEST_NAME
        if not path.is_file():
            raise EngineConfigError(f"no run manifest at {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(**data)

    def save(self) -> None:
        self.updated_at = _now()
        path = Path(self.workspace) / _MANIFEST_NAME
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    def checkpoint(self, status: str, **artifacts: str) -> None:
        if status not in STATUS_ORDER + EXTRA_STATUSES:
            raise EngineConfigError(f"unknown status {status!r}")
        self.artifacts.update(artifacts)
        self.status = status
        self.save()
        log.info("checkpoint: %s (artifacts: %s)", status, list(artifacts))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/shorts_engine/test_manifest.py -v`
Expected: 5 passed.

- [ ] **Step 5: Run the suite**

Run: `python -m pytest tests/shorts_engine -q`
Expected: 8 passed.

---

### Task 3: Stage runner (registry, checkpointing, fail-loud, resume, --until)

**Files:**
- Create: `shorts_engine/runner.py`
- Test: `tests/shorts_engine/test_runner.py`

**Interfaces:**
- Consumes: `RunManifest`, `STATUS_ORDER`, `slug_from_url` (Task 2); `EngineError` (Task 1).
- Produces (used by cli and stage modules):
  - `@dataclass StageContext(manifest: RunManifest, workspace: Path, flags: dict)`
  - `Stage = tuple[str, str, Callable[[StageContext], dict[str, str]]]` — `(name, status_after, fn)`; a stage fn returns an artifact dict merged into the manifest.
  - `run(blog_url: str, stages: list[Stage], workspace_root: Path, until: str | None = None, resume: bool = False, flags: dict | None = None) -> RunManifest`
  - Failure behavior: exception inside a stage sets `manifest.status = "failed"`, `manifest.error = f"{name}: {exc}"`, saves, then re-raises.

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_runner.py
from __future__ import annotations
from pathlib import Path

import pytest

from shorts_engine.manifest import RunManifest
from shorts_engine.runner import StageContext, run

URL = "https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html"


def _mk_stage(name, status_after, calls, fail=False):
    def fn(ctx: StageContext) -> dict[str, str]:
        calls.append(name)
        if fail:
            raise ValueError("boom")
        marker = ctx.workspace / f"{name}.txt"
        marker.write_text("done", encoding="utf-8")
        return {name: f"{name}.txt"}
    return (name, status_after, fn)


def test_runs_stages_in_order_and_checkpoints(tmp_path: Path):
    calls: list[str] = []
    stages = [_mk_stage("ingest", "ingested", calls),
              _mk_stage("facts", "facts", calls)]
    m = run(URL, stages, workspace_root=tmp_path)
    assert calls == ["ingest", "facts"]
    assert m.status == "facts"
    assert m.artifacts == {"ingest": "ingest.txt", "facts": "facts.txt"}


def test_until_stops_early(tmp_path: Path):
    calls: list[str] = []
    stages = [_mk_stage("ingest", "ingested", calls),
              _mk_stage("facts", "facts", calls)]
    m = run(URL, stages, workspace_root=tmp_path, until="ingest")
    assert calls == ["ingest"] and m.status == "ingested"


def test_failure_marks_manifest_and_reraises(tmp_path: Path):
    calls: list[str] = []
    stages = [_mk_stage("ingest", "ingested", calls, fail=True)]
    with pytest.raises(ValueError):
        run(URL, stages, workspace_root=tmp_path)
    loaded = RunManifest.load(tmp_path / "optimizing-nitrate-removal-via-granular")
    assert loaded.status == "failed"
    assert loaded.error.startswith("ingest: boom")


def test_resume_skips_completed_stages(tmp_path: Path):
    calls: list[str] = []
    stages = [_mk_stage("ingest", "ingested", calls),
              _mk_stage("facts", "facts", calls)]
    run(URL, stages, workspace_root=tmp_path, until="ingest")
    calls.clear()
    m = run(URL, stages, workspace_root=tmp_path, resume=True)
    assert calls == ["facts"], "ingest must be skipped on resume"
    assert m.status == "facts"


def test_resume_after_failure_reruns_failed_stage(tmp_path: Path):
    calls: list[str] = []
    ok = _mk_stage("ingest", "ingested", calls)
    bad = _mk_stage("facts", "facts", calls, fail=True)
    with pytest.raises(ValueError):
        run(URL, [ok, bad], workspace_root=tmp_path)
    calls.clear()
    good = _mk_stage("facts", "facts", calls)
    m = run(URL, [ok, good], workspace_root=tmp_path, resume=True)
    assert calls == ["facts"] and m.status == "facts"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/shorts_engine/test_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shorts_engine.runner'`.

- [ ] **Step 3: Implement `shorts_engine/runner.py`**

```python
# shorts_engine/runner.py
"""Deterministic stage state machine over RunManifest."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from shorts_engine.errors import EngineConfigError
from shorts_engine.manifest import RunManifest, STATUS_ORDER, slug_from_url

log = logging.getLogger(__name__)


@dataclass
class StageContext:
    manifest: RunManifest
    workspace: Path
    flags: dict


Stage = tuple[str, str, Callable[[StageContext], dict[str, str]]]


def _status_index(status: str) -> int:
    # "failed"/"hold_for_review" resume from the beginning of the unfinished work:
    # they are not in STATUS_ORDER, so treat them as the last completed ordered
    # status stored before failure (manifest.status is overwritten on failure,
    # so we rely on artifacts: a stage that checkpointed is complete).
    try:
        return STATUS_ORDER.index(status)
    except ValueError:
        return -1


def run(blog_url: str, stages: list[Stage], workspace_root: Path,
        until: str | None = None, resume: bool = False,
        flags: dict | None = None) -> RunManifest:
    flags = flags or {}
    ws = Path(workspace_root) / slug_from_url(blog_url)

    if resume and (ws / "run_manifest.json").is_file():
        manifest = RunManifest.load(ws)
        log.info("resuming run %s at status=%s", manifest.run_id, manifest.status)
    else:
        manifest = RunManifest.create(blog_url, workspace_root=Path(workspace_root))

    ctx = StageContext(manifest=manifest, workspace=Path(manifest.workspace), flags=flags)

    for name, status_after, fn in stages:
        target_idx = _status_index(status_after)
        if target_idx < 0:
            raise EngineConfigError(f"stage {name!r} has unknown status {status_after!r}")
        if resume and _status_index(manifest.status) >= target_idx:
            log.info("skip %s (already at %s)", name, manifest.status)
            continue
        log.info("stage %s → %s", name, status_after)
        try:
            artifacts = fn(ctx)
        except Exception as exc:
            manifest.error = f"{name}: {exc}"
            manifest.status = "failed"
            manifest.save()
            log.error("stage %s FAILED: %s", name, exc)
            raise
        manifest.error = None
        manifest.checkpoint(status_after, **(artifacts or {}))
        if until == name:
            break
    return manifest
```

Note on resume-after-failure: `manifest.status == "failed"` maps to index −1, so every stage whose artifacts weren't checkpointed re-runs; stages that already checkpointed are skipped via the `resume` comparison only when their status was reached. The test `test_resume_after_failure_reruns_failed_stage` pins this: after `ingest` checkpointed `ingested` and `facts` failed, a resumed run must skip nothing… **wait — it must skip `ingest`.** That works because on resume we load the manifest, whose status was overwritten to `"failed"` (index −1), which would re-run `ingest` — wrong. Fix: on failure, keep the last *ordered* status in a separate field. Implement exactly this:

```python
# In RunManifest (Task 2 file — modify): add field
#     last_ok_status: str = "init"
# In runner.run, replace the resume comparison and failure block with:

    for name, status_after, fn in stages:
        target_idx = _status_index(status_after)
        if target_idx < 0:
            raise EngineConfigError(f"stage {name!r} has unknown status {status_after!r}")
        done_idx = _status_index(manifest.status)
        if done_idx < 0:                       # failed / hold_for_review
            done_idx = _status_index(manifest.last_ok_status)
        if resume and done_idx >= target_idx:
            log.info("skip %s (already at %s)", name, manifest.last_ok_status)
            continue
        log.info("stage %s → %s", name, status_after)
        try:
            artifacts = fn(ctx)
        except Exception as exc:
            manifest.error = f"{name}: {exc}"
            manifest.status = "failed"
            manifest.save()
            log.error("stage %s FAILED: %s", name, exc)
            raise
        manifest.error = None
        manifest.last_ok_status = status_after
        manifest.checkpoint(status_after, **(artifacts or {}))
        if until == name:
            break
```

And in `shorts_engine/manifest.py`, add `last_ok_status: str = "init"` to the dataclass fields (after `error`) — `asdict`/`load` handle it automatically.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/shorts_engine/test_runner.py tests/shorts_engine/test_manifest.py -v`
Expected: 10 passed (manifest tests still green with the new field).

- [ ] **Step 5: Run the suite**

Run: `python -m pytest tests/shorts_engine -q`
Expected: 13 passed.

---

### Task 4: Schema-validated LLM helper (retry-with-echo, fail loud, no silent fallback)

**Files:**
- Create: `shorts_engine/llm/text_llm.py`
- Test: `tests/shorts_engine/test_text_llm.py`

**Interfaces:**
- Consumes: `video_agent.ollama_client.OllamaClient` (existing leaf; its `.generate_json(prompt, system=..., retries=1)` returns parsed dict/list or raises `OllamaError`), `config.SMART_TEXT_MODEL`, `config.OLLAMA_MODEL`, `config.LLM_MAX_RETRIES`, `EngineLLMError`.
- Produces (used by facts.py and script.py):
  - `generate_schema_json(prompt: str, system: str, schema: dict, *, retries: int = 3, local_only: bool = False, client_factory=None) -> dict`
  - `client_factory` (tests only): zero-arg callable returning an object with `.generate_json(prompt, system, retries)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_text_llm.py
from __future__ import annotations

import pytest

from shorts_engine.errors import EngineLLMError
from shorts_engine.llm.text_llm import generate_schema_json

SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts: list[str] = []

    def generate_json(self, prompt, system=None, retries=1):
        self.prompts.append(prompt)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_valid_first_try():
    fake = FakeClient([{"answer": "ok"}])
    out = generate_schema_json("p", "s", SCHEMA, client_factory=lambda: fake)
    assert out == {"answer": "ok"}
    assert len(fake.prompts) == 1


def test_schema_violation_retries_with_error_echo_then_succeeds():
    fake = FakeClient([{"wrong": 1}, {"answer": "ok"}])
    out = generate_schema_json("p", "s", SCHEMA, client_factory=lambda: fake)
    assert out == {"answer": "ok"}
    assert len(fake.prompts) == 2
    assert "invalid" in fake.prompts[1].lower()
    assert "'answer' is a required property" in fake.prompts[1]


def test_exhausted_retries_raises_engine_error():
    fake = FakeClient([{"w": 1}, {"w": 2}, {"w": 3}])
    with pytest.raises(EngineLLMError) as ei:
        generate_schema_json("p", "s", SCHEMA, retries=3, client_factory=lambda: fake)
    assert "3 attempts" in str(ei.value)


def test_ollama_error_consumes_attempts_and_raises():
    from video_agent.ollama_client import OllamaError
    fake = FakeClient([OllamaError("down"), OllamaError("down"), OllamaError("down")])
    with pytest.raises(EngineLLMError) as ei:
        generate_schema_json("p", "s", SCHEMA, retries=3, client_factory=lambda: fake)
    assert "not falling back" in str(ei.value).lower()


def test_list_response_rejected_by_schema():
    fake = FakeClient([["a", "b"], {"answer": "ok"}])
    out = generate_schema_json("p", "s", SCHEMA, client_factory=lambda: fake)
    assert out == {"answer": "ok"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/shorts_engine/test_text_llm.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shorts_engine.llm.text_llm'`.

- [ ] **Step 3: Implement `shorts_engine/llm/text_llm.py`**

```python
# shorts_engine/llm/text_llm.py
"""All engine text-LLM calls go through here: schema-validated JSON with
retry-with-error-echo. NO silent model fallback (spec §8): if the smart model
is unreachable after retries we raise EngineLLMError."""
from __future__ import annotations

import json
import logging

import jsonschema

from shorts_engine import config
from shorts_engine.errors import EngineLLMError
from video_agent.ollama_client import OllamaClient, OllamaError

log = logging.getLogger(__name__)


def _default_factory(local_only: bool):
    model = config.OLLAMA_MODEL if local_only else config.SMART_TEXT_MODEL
    return lambda: OllamaClient(model=model, timeout=300)


def generate_schema_json(prompt: str, system: str, schema: dict, *,
                         retries: int = config.LLM_MAX_RETRIES,
                         local_only: bool = False,
                         client_factory=None) -> dict:
    factory = client_factory or _default_factory(local_only)
    client = factory()
    current_prompt = prompt
    last_err: str = "no attempts made"
    for attempt in range(1, retries + 1):
        try:
            result = client.generate_json(current_prompt, system=system, retries=1)
            jsonschema.validate(result, schema)
            return result
        except jsonschema.ValidationError as e:
            last_err = e.message
            log.warning("LLM schema violation (attempt %d/%d): %s",
                        attempt, retries, e.message)
            current_prompt = (
                f"{prompt}\n\nYour previous response was invalid: {e.message}\n"
                f"Respond again with JSON matching this schema exactly:\n"
                f"{json.dumps(schema)}"
            )
        except OllamaError as e:
            last_err = str(e)
            log.warning("LLM call error (attempt %d/%d): %s", attempt, retries, e)
    raise EngineLLMError(
        f"LLM call failed after {retries} attempts (model="
        f"{config.OLLAMA_MODEL if local_only else config.SMART_TEXT_MODEL}): "
        f"{last_err}. NOT falling back to another model."
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/shorts_engine/test_text_llm.py -v`
Expected: 5 passed.

- [ ] **Step 5: Run the suite**

Run: `python -m pytest tests/shorts_engine -q`
Expected: 18 passed.

---

### Task 5: Test fixture — the real (poisoned) Blogger page

**Files:**
- Create: `tests/shorts_engine/fixtures/nitrate_post.html` (generated from the existing run artifact)
- Create: `tests/shorts_engine/fixtures/make_fixture.py` (the generator, kept for reproducibility)
- Test: `tests/shorts_engine/test_fixture.py`

**Interfaces:**
- Produces: the fixture file every ingest/facts/integration test loads. Canonical markers used by later tasks:
  - target-post marker: `"dosage range of 1.5 to 3 kg per cubic meter"`
  - sibling-teaser marker: `"150,000 metric tons"`
  - target post URL: `https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html`
  - target post title: `Optimizing Nitrate Removal via Granular Calcium Nitrate`

- [ ] **Step 1: Write the generator**

```python
# tests/shorts_engine/fixtures/make_fixture.py
"""One-time fixture generator: extracts the captured Blogger page HTML from the
2026-06-25 run's storyboard.json. Re-run only if the artifact moves."""
from __future__ import annotations
import json
from pathlib import Path

SRC = Path("output/videos/optimizing-nitrate-removal-via-granular-html/storyboard.json")
DST = Path(__file__).parent / "nitrate_post.html"

if __name__ == "__main__":
    sb = json.loads(SRC.read_text(encoding="utf-8"))
    DST.write_text(sb["blog"]["content_html"], encoding="utf-8")
    print(f"wrote {DST} ({DST.stat().st_size} bytes)")
```

- [ ] **Step 2: Generate the fixture**

Run: `python tests/shorts_engine/fixtures/make_fixture.py`
Expected: `wrote ...nitrate_post.html (213264 bytes)` (size ~213 KB).

- [ ] **Step 3: Write the fixture sanity test**

```python
# tests/shorts_engine/test_fixture.py
from __future__ import annotations
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "nitrate_post.html"


def test_fixture_contains_target_and_poison_markers():
    html = FIXTURE.read_text(encoding="utf-8")
    # target post content (must survive isolation):
    assert "dosage range of 1.5 to 3 kg per cubic meter" in html
    # sibling-post teaser (must be REMOVED by isolation):
    assert "150,000 metric tons" in html
    # multiple post containers — the poisoned-page condition:
    assert html.count('class="post-outer"') >= 3
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/shorts_engine/test_fixture.py -v`
Expected: 1 passed. (If the marker strings differ, open the fixture, locate the equivalent sentences, and update the marker constants in this test AND in Tasks 6/7/12 consistently — they are the shared ground truth.)

- [ ] **Step 5: Run the suite**

Run: `python -m pytest tests/shorts_engine -q`
Expected: 19 passed.

---

### Task 6: INGEST stage part 1 — post isolation + canonical text

**Files:**
- Create: `shorts_engine/stages/ingest.py`
- Test: `tests/shorts_engine/test_ingest.py`

**Interfaces:**
- Consumes: fixture (Task 5); `StageContext` (Task 3); `config.PAPER_DOMAINS/STANDARD_DOMAINS` (Task 1).
- Produces (used by facts/script/integration):
  - `@dataclass Citation(marker: int, url: str, kind: str, title: str = "")`
  - `@dataclass IsolatedPost(title: str, body_html: str, canonical_text: str, citations: list[Citation], images: list[dict])`
  - `isolate_post(page_html: str, url: str) -> IsolatedPost`
  - `classify_citation(url: str) -> str` (Task 7 adds tests; implemented here)
  - `run(ctx: StageContext) -> dict[str, str]` — writes `post.json` + `canonical.txt`, returns `{"post": "post.json", "canonical": "canonical.txt"}`. For tests, `ctx.flags["html_override"]` (a Path) skips the network fetch.

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_ingest.py
from __future__ import annotations
from pathlib import Path

from shorts_engine.stages.ingest import isolate_post

FIXTURE = Path(__file__).parent / "fixtures" / "nitrate_post.html"
URL = "https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html"


def _post():
    return isolate_post(FIXTURE.read_text(encoding="utf-8"), URL)


def test_isolates_single_post_body():
    post = _post()
    assert "dosage range of 1.5 to 3 kg per cubic meter" in post.canonical_text
    assert "150,000 metric tons" not in post.canonical_text  # sibling teaser gone
    assert "Skip to main content" not in post.canonical_text  # page chrome gone


def test_title_extracted():
    post = _post()
    assert post.title == "Optimizing Nitrate Removal via Granular Calcium Nitrate"


def test_canonical_is_normalized_plain_text():
    post = _post()
    assert "<" not in post.canonical_text and ">" not in post.canonical_text
    assert "  " not in post.canonical_text          # collapsed whitespace
    assert "&amp;" not in post.canonical_text        # entities decoded
    assert len(post.canonical_text) > 5000           # a real 16-min-read body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/shorts_engine/test_ingest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shorts_engine.stages.ingest'`.

- [ ] **Step 3: Implement isolation in `shorts_engine/stages/ingest.py`**

```python
# shorts_engine/stages/ingest.py
"""Stage 1 — INGEST: blog URL → post.json + canonical.txt.

Fixes the poisoned-scrape defect (spec §1 F7): the captured Blogger page
contains multiple post containers (sibling teasers, nav chrome). We isolate
exactly ONE post body:
  1. the div.post-outer whose h3.post-title anchor href == the input URL
  2. else the div.post-outer whose h3.post-title has NO anchor (Blogger marks
     the currently-viewed post with a plain-text title)
  3. else the div.post-outer with the longest post-body text
"""
from __future__ import annotations

import html as _html
import json
import logging
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from shorts_engine import config
from shorts_engine.errors import EngineError

log = logging.getLogger(__name__)

_STRIP_SELECTORS = [
    "style", "script", ".post-share-buttons", ".comments", ".comment-form",
    ".jump-link", ".post-footer", ".breadcrumbs", ".singleton-element",
]


@dataclass
class Citation:
    marker: int
    url: str
    kind: str
    title: str = ""


@dataclass
class IsolatedPost:
    title: str
    body_html: str
    canonical_text: str
    citations: list[Citation] = field(default_factory=list)
    images: list[dict] = field(default_factory=list)


def classify_citation(url: str) -> str:
    u = url.lower()
    if u.endswith(".pdf") or any(d in u for d in config.PAPER_DOMAINS):
        return "paper"
    if any(d in u for d in config.STANDARD_DOMAINS):
        return "standard"
    return "web"


def _normalize_text(el) -> str:
    text = el.get_text(" ")
    text = _html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _pick_post_container(soup: BeautifulSoup, url: str):
    outers = soup.select("div.post-outer")
    if not outers:
        raise EngineError("no div.post-outer found — not a Blogger page?")
    # 1) permalink match
    for outer in outers:
        a = outer.select_one("h3.post-title a[href]")
        if a and a["href"].rstrip("/") == url.rstrip("/"):
            return outer
    # 2) current post = title without anchor
    for outer in outers:
        t = outer.select_one("h3.post-title")
        if t and not t.find("a"):
            return outer
    # 3) longest body
    return max(outers, key=lambda o: len(_normalize_text(o.select_one("div.post-body") or o)))


def isolate_post(page_html: str, url: str) -> IsolatedPost:
    soup = BeautifulSoup(page_html, "html.parser")
    outer = _pick_post_container(soup, url)
    title_el = outer.select_one("h3.post-title")
    title = _normalize_text(title_el) if title_el else ""
    body = outer.select_one("div.post-body")
    if body is None:
        raise EngineError("post container has no div.post-body")
    for sel in _STRIP_SELECTORS:
        for el in body.select(sel):
            el.decompose()
    images = [{"src": img["src"], "alt": img.get("alt", "")}
              for img in body.select("img[src]")
              if str(img["src"]).startswith("http")]
    canonical = _normalize_text(body)
    return IsolatedPost(title=title, body_html=str(body),
                        canonical_text=canonical, images=images)
```

(`run()` and citations come in Task 7 — keep this task green with isolation only.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/shorts_engine/test_ingest.py -v`
Expected: 3 passed. If `test_title_extracted` fails because the current-post title element differs in the fixture, inspect with:
`python -c "from bs4 import BeautifulSoup;h=open('tests/shorts_engine/fixtures/nitrate_post.html',encoding='utf-8').read();s=BeautifulSoup(h,'html.parser');[print(t.get_text(strip=True)[:70],'| anchor:',bool(t.find('a'))) for t in s.select('h3.post-title')]"`
and adjust `_pick_post_container`'s ladder (the three strategies stay; only selector strings may need tuning to the real theme).

- [ ] **Step 5: Run the suite**

Run: `python -m pytest tests/shorts_engine -q`
Expected: 22 passed.

---

### Task 7: INGEST stage part 2 — citations, classification, stage `run()`

**Files:**
- Modify: `shorts_engine/stages/ingest.py` (add citation extraction + `run`)
- Test: `tests/shorts_engine/test_ingest.py` (extend)

**Interfaces:**
- Consumes: Task 6 dataclasses; `blog_history.json` (optional, project root) for region/category.
- Produces: `post.json` schema (consumed by facts/script/Plan-2 shotlist):

```json
{"url": "...", "title": "...", "region": "eu|null", "category": "...|null",
 "citations": [{"marker": 1, "url": "https://...", "kind": "paper", "title": "..."}],
 "images": [{"src": "...", "alt": "..."}]}
```

- [ ] **Step 1: Write the failing tests (append to test_ingest.py)**

```python
# append to tests/shorts_engine/test_ingest.py
import json
from shorts_engine.stages.ingest import classify_citation, extract_citations, run
from shorts_engine.manifest import RunManifest
from shorts_engine.runner import StageContext


def test_classify_citation():
    assert classify_citation("https://link.springer.com/article/10.1007/x") == "paper"
    assert classify_citation("https://doi.org/10.1000/xyz") == "paper"
    assert classify_citation("https://www.mdpi.com/2073-4441/12/1/1/pdf") == "paper"
    assert classify_citation("https://eur-lex.europa.eu/eli/dir/1991/676") == "standard"
    assert classify_citation("https://www.epa.gov/nutrient-policy") == "standard"
    assert classify_citation("https://somecompany.com/blog") == "web"


def test_extract_citations_from_fixture():
    post = _post()
    cites = extract_citations(post.body_html)
    assert len(cites) >= 3, "the post has a References section with linked sources"
    markers = [c.marker for c in cites]
    assert markers == sorted(markers) and markers[0] == 1
    assert all(c.url.startswith("http") for c in cites)
    assert all(c.kind in ("paper", "standard", "web") for c in cites)


def test_run_writes_post_json_and_canonical(tmp_path):
    m = RunManifest.create(URL, workspace_root=tmp_path)
    ctx = StageContext(manifest=m, workspace=Path(m.workspace),
                       flags={"html_override": FIXTURE})
    artifacts = run(ctx)
    assert artifacts == {"post": "post.json", "canonical": "canonical.txt"}
    post = json.loads((Path(m.workspace) / "post.json").read_text(encoding="utf-8"))
    assert post["title"].startswith("Optimizing Nitrate Removal")
    assert post["url"] == URL
    canonical = (Path(m.workspace) / "canonical.txt").read_text(encoding="utf-8")
    assert "1.5 to 3 kg" in canonical and "150,000 metric tons" not in canonical
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/shorts_engine/test_ingest.py -v`
Expected: new tests FAIL with `ImportError: cannot import name 'extract_citations'`.

- [ ] **Step 3: Add citations + run() to `shorts_engine/stages/ingest.py`**

```python
# append to shorts_engine/stages/ingest.py

def extract_citations(body_html: str) -> list[Citation]:
    """Find the References/Sources section; number its external links 1..N.
    Fallback: dedup external hrefs behind <sup> markers in reading order."""
    soup = BeautifulSoup(body_html, "html.parser")
    cites: list[Citation] = []

    heading = None
    for h in soup.find_all(["h2", "h3", "h4"]):
        if re.search(r"\b(references|sources)\b", h.get_text(), re.IGNORECASE):
            heading = h
            break
    if heading is not None:
        lst = heading.find_next(["ol", "ul"])
        if lst is not None:
            for i, li in enumerate(lst.find_all("li"), start=1):
                a = li.find("a", href=re.compile(r"^https?://"))
                if a:
                    cites.append(Citation(marker=i, url=a["href"],
                                          kind=classify_citation(a["href"]),
                                          title=_normalize_text(li)[:150]))
    if not cites:  # fallback: superscript anchors
        seen: set[str] = set()
        for sup in soup.select("sup a[href], a[href] sup"):
            a = sup if sup.name == "a" else sup.find_parent("a")
            href = a.get("href", "")
            if href.startswith("http") and href not in seen:
                seen.add(href)
                cites.append(Citation(marker=len(cites) + 1, url=href,
                                      kind=classify_citation(href)))
    return cites


def _region_category_from_history(url: str) -> tuple[str | None, str | None]:
    path = config.BLOG_HISTORY_PATH
    if not path.is_file():
        return None, None
    try:
        history = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, None
    entries = history if isinstance(history, list) else history.get("posts", [])
    for e in entries:
        if isinstance(e, dict) and e.get("url", "").rstrip("/") == url.rstrip("/"):
            return e.get("region"), e.get("category")
    return None, None


def run(ctx) -> dict[str, str]:
    url = ctx.manifest.blog_url
    override = ctx.flags.get("html_override")
    if override:
        page_html = Path(override).read_text(encoding="utf-8")
    else:
        resp = requests.get(url, timeout=30,
                            headers={"User-Agent": "Mozilla/5.0 (shorts_engine)"})
        resp.raise_for_status()
        page_html = resp.text

    post = isolate_post(page_html, url)
    post.citations = extract_citations(post.body_html)
    region, category = _region_category_from_history(url)

    payload = {"url": url, "title": post.title, "region": region,
               "category": category,
               "citations": [asdict(c) for c in post.citations],
               "images": post.images}
    (ctx.workspace / "post.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    (ctx.workspace / "canonical.txt").write_text(post.canonical_text, encoding="utf-8")
    log.info("ingested %r: %d chars canonical, %d citations, %d images",
             post.title, len(post.canonical_text), len(post.citations),
             len(post.images))
    return {"post": "post.json", "canonical": "canonical.txt"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/shorts_engine/test_ingest.py -v`
Expected: 6 passed. If `test_extract_citations_from_fixture` finds <3 citations, dump the References area (`python -c "..."` printing `heading.find_next(['ol','ul'])`) and adapt the list-item parsing to the theme's actual markup (keep the two-strategy structure).

- [ ] **Step 5: Run the suite**

Run: `python -m pytest tests/shorts_engine -q`
Expected: 25 passed.

---

### Task 8: FACTS stage part 1 — miner, normalizer, verbatim locator (pure functions)

**Files:**
- Create: `shorts_engine/stages/facts.py`
- Test: `tests/shorts_engine/test_facts.py`

**Interfaces:**
- Produces (used within facts stage + by script gates):
  - `normalize_for_match(s: str) -> str` — lowercase, collapse whitespace, unify `–`/`—`/`−`→`-`, curly quotes→straight, `&nbsp;`→space.
  - `locate_verbatim(quote: str, canonical: str) -> int | None` — char offset in normalized canonical, `None` if absent.
  - `split_sentences(text: str) -> list[str]`
  - `mine_candidate_sentences(canonical: str) -> list[str]` — sentences containing at least one digit, deduped, in document order.

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_facts.py
from __future__ import annotations

from shorts_engine.stages.facts import (
    normalize_for_match, locate_verbatim, split_sentences, mine_candidate_sentences,
)

CANON = ("Current industry best practice suggests a dosage range of 1.5 to 3 kg "
         "per cubic meter of wastewater volume, though this will vary. "
         "The EU’s stringent water quality directives mandate effective "
         "reduction. A denitrifying filter removes suspended solids alongside "
         "nitrate reduction.")


def test_normalize_unifies_dashes_quotes_whitespace():
    assert normalize_for_match("A – B’s  test") == "a - b's test"
    assert normalize_for_match("1.5–3 kg/m³") == "1.5-3 kg/m³"


def test_locate_verbatim_exact_and_normalized():
    assert locate_verbatim("dosage range of 1.5 to 3 kg", CANON) is not None
    assert locate_verbatim("The EU's stringent water quality directives", CANON) is not None
    assert locate_verbatim("reduces nitrate by 150 mg/L", CANON) is None  # fabricated


def test_split_sentences():
    sents = split_sentences(CANON)
    assert len(sents) == 3
    assert sents[0].startswith("Current industry")


def test_mine_candidates_only_numeric_sentences():
    cands = mine_candidate_sentences(CANON)
    assert len(cands) == 1
    assert "1.5 to 3 kg" in cands[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/shorts_engine/test_facts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shorts_engine.stages.facts'`.

- [ ] **Step 3: Implement the pure functions**

```python
# shorts_engine/stages/facts.py
"""Stage 2 — FACTS: canonical.txt → factsheet.json.

Facts are LOCATED, not generated (spec §4 Stage 2): a deterministic miner
finds numeric sentences; the LLM only wraps them into structured entries; a
verbatim gate drops anything that cannot be string-located in the canonical
text. This makes fabricated stats (spec §1 F6) structurally impossible."""
from __future__ import annotations

import json
import logging
import re

from shorts_engine import config
from shorts_engine.errors import EngineError
from shorts_engine.llm import text_llm

log = logging.getLogger(__name__)

_DASHES = {"–": "-", "—": "-", "−": "-"}
_QUOTES = {"‘": "'", "’": "'", "“": '"', "”": '"'}


def normalize_for_match(s: str) -> str:
    for k, v in {**_DASHES, **_QUOTES, " ": " "}.items():
        s = s.replace(k, v)
    return re.sub(r"\s+", " ", s).strip().lower()


def locate_verbatim(quote: str, canonical: str) -> int | None:
    pos = normalize_for_match(canonical).find(normalize_for_match(quote))
    return pos if pos >= 0 else None


_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z“\"(])")


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT_RE.split(text) if s.strip()]


def mine_candidate_sentences(canonical: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for sent in split_sentences(canonical):
        if re.search(r"\d", sent) and sent not in seen:
            seen.add(sent)
            out.append(sent)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/shorts_engine/test_facts.py -v`
Expected: 4 passed.

- [ ] **Step 5: Run the suite**

Run: `python -m pytest tests/shorts_engine -q`
Expected: 29 passed.

---

### Task 9: FACTS stage part 2 — LLM wrap, verbatim gate, `run()`

**Files:**
- Modify: `shorts_engine/stages/facts.py`
- Test: `tests/shorts_engine/test_facts.py` (extend)

**Interfaces:**
- Consumes: `text_llm.generate_schema_json` (Task 4), `brand.load_brand_facts` (Task 10 — this task writes `run()` to accept the brand facts *path missing gracefully is NOT allowed*; to keep Task 9 independently green, `run()` loads brand facts via a lazy import and Task 10 provides the module. Order note: **implement Task 10 before running Task 9's stage-level test** — the pure-gate tests here don't need it).
- Produces: `factsheet.json` (consumed by script stage; schema below), plus:
  - `FACT_WRAP_SCHEMA: dict` (module constant)
  - `wrap_sentences_into_facts(sentences: list[str], post_meta: dict, *, local_only: bool = False, llm=text_llm.generate_schema_json) -> list[dict]`
  - `gate_facts(entries: list[dict], canonical: str) -> tuple[list[dict], list[dict]]` → (kept-with-offsets, dropped-with-reason)
  - `run(ctx) -> {"factsheet": "factsheet.json"}`

```json
// factsheet.json
{"facts": [{"id": "f1", "verbatim_quote": "...", "char_offset": 3812,
            "value": "1.5-3", "unit": "kg/m3", "claim_summary": "...",
            "tags": ["spec"], "procurement_significance": 5,
            "citation_marker": 1}],
 "brand_facts": [{"id": "b_purity", "text": "...", "kind": "differentiator"}],
 "dropped": [{"verbatim_quote": "...", "reason": "not located in canonical"}]}
```

- [ ] **Step 1: Write the failing tests (append)**

```python
# append to tests/shorts_engine/test_facts.py
from shorts_engine.stages.facts import FACT_WRAP_SCHEMA, gate_facts, wrap_sentences_into_facts


def test_gate_keeps_located_drops_fabricated():
    entries = [
        {"id": "f1", "verbatim_quote": "dosage range of 1.5 to 3 kg per cubic meter",
         "value": "1.5-3", "unit": "kg/m3", "claim_summary": "dosing window",
         "tags": ["spec"], "procurement_significance": 5, "citation_marker": 1},
        {"id": "f2", "verbatim_quote": "reduces nitrate by 150 mg/L",
         "value": "150", "unit": "mg/L", "claim_summary": "fabricated",
         "tags": ["metric"], "procurement_significance": 5, "citation_marker": None},
        {"id": "f3", "verbatim_quote": "A denitrifying filter removes suspended solids alongside nitrate reduction",
         "value": "0", "unit": "", "claim_summary": "value not in quote",
         "tags": ["benefit"], "procurement_significance": 3, "citation_marker": None},
    ]
    kept, dropped = gate_facts(entries, CANON)
    assert [e["id"] for e in kept] == ["f1"]
    assert kept[0]["char_offset"] is not None
    reasons = {d["id"]: d["reason"] for d in dropped}
    assert "not located" in reasons["f2"]
    assert "value" in reasons["f3"]


def test_wrap_calls_llm_with_sentences_and_returns_entries():
    captured = {}
    def fake_llm(prompt, system, schema, **kw):
        captured["prompt"] = prompt
        assert schema is FACT_WRAP_SCHEMA
        return {"facts": [{"id": "f1",
                           "verbatim_quote": "dosage range of 1.5 to 3 kg per cubic meter",
                           "value": "1.5-3", "unit": "kg/m3",
                           "claim_summary": "dosing window", "tags": ["spec"],
                           "procurement_significance": 5, "citation_marker": 1}]}
    out = wrap_sentences_into_facts(
        ["Current industry best practice suggests a dosage range of 1.5 to 3 kg per cubic meter of wastewater volume."],
        {"title": "T", "region": "eu", "category": "wastewater_treatment"},
        llm=fake_llm)
    assert out[0]["id"] == "f1"
    assert "1.5 to 3 kg" in captured["prompt"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/shorts_engine/test_facts.py -v`
Expected: new tests FAIL with `ImportError` (names not defined).

- [ ] **Step 3: Implement wrap + gate + run**

```python
# append to shorts_engine/stages/facts.py

FACT_WRAP_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "verbatim_quote": {"type": "string"},
                    "value": {"type": "string"},
                    "unit": {"type": "string"},
                    "claim_summary": {"type": "string"},
                    "tags": {"type": "array", "items": {
                        "enum": ["metric", "spec", "benefit", "risk",
                                 "region", "compliance"]}},
                    "procurement_significance": {"type": "integer",
                                                 "minimum": 1, "maximum": 5},
                    "citation_marker": {"type": ["integer", "null"]},
                },
                "required": ["id", "verbatim_quote", "value", "unit",
                             "claim_summary", "tags",
                             "procurement_significance", "citation_marker"],
            },
        },
    },
    "required": ["facts"],
}

_WRAP_SYSTEM = (
    "You extract procurement-relevant facts for a B2B chemical supplier's video "
    "script. You are given sentences COPIED VERBATIM from a blog post. For each "
    "useful sentence, produce a fact entry whose verbatim_quote is an EXACT "
    "substring copy of one given sentence (never reworded). Skip boilerplate. "
    "value/unit must appear inside the quote. citation_marker is the superscript "
    "number if the sentence shows one, else null."
)


def wrap_sentences_into_facts(sentences: list[str], post_meta: dict, *,
                              local_only: bool = False,
                              llm=text_llm.generate_schema_json) -> list[dict]:
    numbered = "\n".join(f"- {s}" for s in sentences)
    prompt = (
        f"Blog: {post_meta.get('title')} (region={post_meta.get('region')}, "
        f"category={post_meta.get('category')})\n\nSentences:\n{numbered}\n\n"
        f"Return the facts JSON now. Use ids f1, f2, ... in order."
    )
    result = llm(prompt, _WRAP_SYSTEM, FACT_WRAP_SCHEMA, local_only=local_only)
    return result["facts"]


def gate_facts(entries: list[dict], canonical: str) -> tuple[list[dict], list[dict]]:
    kept, dropped = [], []
    for e in entries:
        offset = locate_verbatim(e["verbatim_quote"], canonical)
        if offset is None:
            dropped.append({**e, "reason": "not located in canonical text"})
            continue
        digits = re.sub(r"[^\d.]", " ", e["value"]).split()
        quote_norm = normalize_for_match(e["verbatim_quote"])
        if digits and not all(d in quote_norm for d in digits):
            dropped.append({**e, "reason": "value digits not inside the quote"})
            continue
        kept.append({**e, "char_offset": offset})
    return kept, dropped


def run(ctx) -> dict[str, str]:
    from shorts_engine.brand import load_brand_facts  # Task 10
    canonical = (ctx.workspace / "canonical.txt").read_text(encoding="utf-8")
    post_meta = json.loads((ctx.workspace / "post.json").read_text(encoding="utf-8"))
    sentences = mine_candidate_sentences(canonical)
    if not sentences:
        raise EngineError("no numeric sentences found in canonical text")
    log.info("mined %d candidate sentences", len(sentences))
    entries = wrap_sentences_into_facts(
        sentences[:40], post_meta, local_only=ctx.flags.get("local_only", False))
    kept, dropped = gate_facts(entries, canonical)
    if not kept:
        raise EngineError(
            f"all {len(entries)} fact entries failed the verbatim gate")
    brand = load_brand_facts()
    payload = {
        "facts": kept,
        "brand_facts": (
            [{"id": d["id"], "text": d["text"], "kind": "differentiator"}
             for d in brand.differentiators]
            + [{"id": f"cta{i}", "text": t, "kind": "cta"}
               for i, t in enumerate(brand.cta_lines, start=1)]
        ),
        "dropped": dropped,
    }
    (ctx.workspace / "factsheet.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("factsheet: %d kept, %d dropped", len(kept), len(dropped))
    return {"factsheet": "factsheet.json"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/shorts_engine/test_facts.py -v`
Expected: 6 passed.

- [ ] **Step 5: Run the suite**

Run: `python -m pytest tests/shorts_engine -q`
Expected: 31 passed.

---

### Task 10: `brand_facts.yaml` + loader (engine refuses to run without it)

**Files:**
- Create: `brand_facts.yaml` (project root)
- Create: `shorts_engine/brand.py`
- Test: `tests/shorts_engine/test_brand.py`

**Interfaces:**
- Produces (used by facts.py Task 9 and script.py Tasks 11–12):
  - `@dataclass BrandFacts(company: str, domain: str, tagline: str, differentiators: list[dict], cta_lines: list[str], banned_claims: list[str])` — differentiator dicts: `{"id": str, "text": str}`.
  - `load_brand_facts(path: Path = config.BRAND_FACTS_PATH) -> BrandFacts` — raises `EngineConfigError` if missing or invalid.

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_brand.py
from __future__ import annotations
from pathlib import Path

import pytest

from shorts_engine.brand import BrandFacts, load_brand_facts
from shorts_engine.errors import EngineConfigError


def test_load_real_project_file():
    bf = load_brand_facts()
    assert bf.domain == "hrsuindore.com"
    ids = [d["id"] for d in bf.differentiators]
    assert ids == ["b_purity", "b_supply", "b_esg"]
    assert bf.cta_lines and bf.banned_claims
    assert any("reach" in b.lower() for b in bf.banned_claims)


def test_missing_file_raises(tmp_path: Path):
    with pytest.raises(EngineConfigError) as ei:
        load_brand_facts(tmp_path / "brand_facts.yaml")
    assert "brand_facts.yaml" in str(ei.value)


def test_invalid_yaml_raises(tmp_path: Path):
    p = tmp_path / "brand_facts.yaml"
    p.write_text("company: X\n", encoding="utf-8")  # missing required keys
    with pytest.raises(EngineConfigError):
        load_brand_facts(p)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/shorts_engine/test_brand.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shorts_engine.brand'`.

- [ ] **Step 3: Create `brand_facts.yaml` and the loader**

```yaml
# brand_facts.yaml — HUMAN-OWNED. The engine reads this; it never writes it.
# EDIT THE EXAMPLE WORDING to match what HRSU can truthfully claim.
# Approved differentiator categories (2026-07-04): purity, supply/MOQ, ESG.
company: HRSU Indore Pvt. Ltd.
domain: hrsuindore.com
tagline: "Beyond Granules. The Purity of Powder."
differentiators:
  - id: b_purity
    text: "Consistent high-purity calcium nitrate powder with batch-level QC"
  - id: b_supply
    text: "Flexible minimum order quantities and responsive quoting for trial orders"
  - id: b_esg
    text: "Solar power and steam-reuse initiatives at the Indore plant"
cta_lines:
  - "Full technical guide on the HRSU blog — link in description."
  - "Sourcing calcium nitrate? Visit hrsuindore.com"
banned_claims:
  - "REACH registered"
  - "REACH-registered"
  - "certified"
  - "ISO 9001"
  - "FDA approved"
```

```python
# shorts_engine/brand.py
"""Loader for the human-owned brand facts file (spec §7). The engine refuses
to run without it — brand claims must never be invented."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

from shorts_engine import config
from shorts_engine.errors import EngineConfigError

log = logging.getLogger(__name__)

_REQUIRED = ["company", "domain", "tagline", "differentiators",
             "cta_lines", "banned_claims"]


@dataclass
class BrandFacts:
    company: str
    domain: str
    tagline: str
    differentiators: list[dict]
    cta_lines: list[str]
    banned_claims: list[str]


def load_brand_facts(path: Path = config.BRAND_FACTS_PATH) -> BrandFacts:
    if not Path(path).is_file():
        raise EngineConfigError(
            f"brand_facts.yaml not found at {path} — the engine cannot run "
            f"without human-approved brand claims (spec §7)")
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise EngineConfigError(f"brand_facts.yaml is not valid YAML: {e}") from e
    missing = [k for k in _REQUIRED if not (isinstance(data, dict) and data.get(k))]
    if missing:
        raise EngineConfigError(f"brand_facts.yaml missing keys: {missing}")
    for d in data["differentiators"]:
        if not (isinstance(d, dict) and d.get("id") and d.get("text")):
            raise EngineConfigError(f"malformed differentiator entry: {d!r}")
    return BrandFacts(**{k: data[k] for k in _REQUIRED})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/shorts_engine/test_brand.py -v`
Expected: 3 passed.

- [ ] **Step 5: Run the suite**

Run: `python -m pytest tests/shorts_engine -q`
Expected: 34 passed.

---

### Task 11: SCRIPT gates (pure functions, heavy TDD)

**Files:**
- Create: `shorts_engine/stages/script.py`
- Test: `tests/shorts_engine/test_script_gates.py`

**Interfaces:**
- Consumes: `normalize_for_match` (Task 8), `config.BEAT_TEMPLATE/WORDS_PER_SECOND/WORD_BUDGET_TOLERANCE/FEAR_FILLER_PATTERNS/SCRIPT_BANNED_PHRASES`, `BrandFacts` (Task 10).
- Produces (used by Task 12 `run()` and Plan-2 shotlist):
  - Beat dict shape: `{"beat": "hook", "narration": str, "fact_ids": list[str], "card_text": str, "broll_wish": str | ""}`
  - `extract_numeric_tokens(text: str) -> list[str]`
  - `gate_numbers(beats, factsheet: dict, brand: BrandFacts) -> list[str]`
  - `gate_banned(beats, brand: BrandFacts) -> list[str]`
  - `gate_word_budget(beats) -> list[str]`
  - `gate_card_text(beats) -> list[str]`
  - `gate_differentiator(beats, brand: BrandFacts) -> list[str]`
  - `run_gates(beats, factsheet, brand) -> list[str]` (concatenation of all)

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_script_gates.py
from __future__ import annotations

from shorts_engine.brand import BrandFacts
from shorts_engine.stages.script import (
    extract_numeric_tokens, gate_numbers, gate_banned, gate_word_budget,
    gate_card_text, gate_differentiator, run_gates,
)

BRAND = BrandFacts(
    company="HRSU", domain="hrsuindore.com", tagline="t",
    differentiators=[{"id": "b_purity", "text": "High-purity powder with batch QC"},
                     {"id": "b_supply", "text": "Flexible MOQs"},
                     {"id": "b_esg", "text": "Solar power at the plant"}],
    cta_lines=["Visit hrsuindore.com"],
    banned_claims=["REACH registered", "certified"],
)

FACTSHEET = {"facts": [
    {"id": "f1", "verbatim_quote": "dosage range of 1.5 to 3 kg per cubic meter",
     "value": "1.5-3", "unit": "kg/m3", "tags": ["spec"], "citation_marker": 1},
]}


def _beats(**over):
    beats = [
        {"beat": "hook", "narration": "EU nitrate limits are tightening for industry.",
         "fact_ids": [], "card_text": "EU limits tightening", "broll_wish": ""},
        {"beat": "stakes", "narration": "Non-compliance risks penalties and production downtime for your plant.",
         "fact_ids": [], "card_text": "Downtime risk", "broll_wish": ""},
        {"beat": "mechanism", "narration": "Dosing calcium nitrate supports denitrification, turning nitrate load into harmless nitrogen gas in the treatment train and stabilising the process.",
         "fact_ids": [], "card_text": "Nitrate to nitrogen gas", "broll_wish": ""},
        {"beat": "proof", "narration": "Best practice suggests a dosage range of 1.5 to 3 kg per cubic meter of wastewater.",
         "fact_ids": ["f1"], "card_text": "Dosing window", "broll_wish": ""},
        {"beat": "cta", "narration": "HRSU supplies high-purity powder with batch QC. Visit hrsuindore.com for the full guide today.",
         "fact_ids": ["b_purity"], "card_text": "hrsuindore.com", "broll_wish": ""},
    ]
    for i, patch in over.items():
        beats[int(i)].update(patch)
    return beats


def test_extract_numeric_tokens():
    assert extract_numeric_tokens("range of 1.5 to 3 kg, 10,000 tons") == ["1.5", "3", "10000"]
    assert extract_numeric_tokens("no numbers here") == []


def test_gate_numbers_passes_traced_and_fails_untraced():
    assert gate_numbers(_beats(), FACTSHEET, BRAND) == []
    bad = _beats(**{"3": {"narration": "Reduces nitrate by 150 mg per liter.",
                          "fact_ids": ["f1"]}})
    errs = gate_numbers(bad, FACTSHEET, BRAND)
    assert len(errs) == 1 and "150" in errs[0] and "proof" in errs[0]


def test_gate_numbers_checks_card_text_too():
    bad = _beats(**{"3": {"card_text": "42 percent better"}})
    errs = gate_numbers(bad, FACTSHEET, BRAND)
    assert len(errs) == 1 and "42" in errs[0]


def test_gate_banned_catches_ai_isms_fear_filler_and_brand_bans():
    errs = gate_banned(_beats(**{"1": {"narration": "Compliance is everything for plants."}}), BRAND)
    assert any("is everything" in e for e in errs)
    errs = gate_banned(_beats(**{"4": {"narration": "We are REACH registered suppliers. Visit hrsuindore.com now."}}), BRAND)
    assert any("reach registered" in e.lower() for e in errs)
    assert gate_banned(_beats(), BRAND) == []


def test_gate_word_budget():
    assert gate_word_budget(_beats()) == []
    too_long = _beats(**{"0": {"narration": " ".join(["word"] * 30)}})  # hook max 4s*2.6*1.2=12.5
    errs = gate_word_budget(too_long)
    assert len(errs) == 1 and "hook" in errs[0]


def test_gate_card_text_rejects_narration_echo():
    echo = _beats(**{"1": {"card_text": "risks penalties and production downtime for"}})
    errs = gate_card_text(echo)
    assert len(errs) == 1 and "duplicates narration" in errs[0]
    assert gate_card_text(_beats()) == []


def test_gate_differentiator_exactly_one_in_cta_only():
    assert gate_differentiator(_beats(), BRAND) == []
    none_ = _beats(**{"4": {"fact_ids": []}})
    assert len(gate_differentiator(none_, BRAND)) == 1
    early = _beats(**{"0": {"fact_ids": ["b_esg"]}})
    assert len(gate_differentiator(early, BRAND)) == 1


def test_run_gates_aggregates():
    bad = _beats(**{"3": {"narration": "Reduces nitrate by 150 mg per liter."},
                    "4": {"fact_ids": []}})
    errs = run_gates(bad, FACTSHEET, BRAND)
    assert len(errs) >= 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/shorts_engine/test_script_gates.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shorts_engine.stages.script'`.

- [ ] **Step 3: Implement the gates**

```python
# shorts_engine/stages/script.py
"""Stage 3 — SCRIPT: factsheet.json → script.json.

Five-beat procurement template with deterministic gates (spec §4 Stage 3).
The writer LLM never sees raw blog HTML; it sees located facts only."""
from __future__ import annotations

import json
import logging
import re

from shorts_engine import config
from shorts_engine.brand import BrandFacts, load_brand_facts
from shorts_engine.errors import GateFailure
from shorts_engine.llm import text_llm
from shorts_engine.stages.facts import normalize_for_match

log = logging.getLogger(__name__)

_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def extract_numeric_tokens(text: str) -> list[str]:
    return [t.replace(",", "") for t in _NUM_RE.findall(text)]


def _allowed_pool(fact_ids: list[str], factsheet: dict, brand: BrandFacts) -> str:
    facts_by_id = {f["id"]: f for f in factsheet.get("facts", [])}
    parts: list[str] = []
    for fid in fact_ids:
        if fid in facts_by_id:
            parts.append(facts_by_id[fid]["verbatim_quote"])
        else:
            for d in brand.differentiators:
                if d["id"] == fid:
                    parts.append(d["text"])
    parts.extend(brand.cta_lines)
    parts.append(brand.domain)
    return normalize_for_match(" | ".join(parts)).replace(",", "")


def gate_numbers(beats: list[dict], factsheet: dict, brand: BrandFacts) -> list[str]:
    errs: list[str] = []
    for b in beats:
        pool = _allowed_pool(b.get("fact_ids", []), factsheet, brand)
        for source in ("narration", "card_text"):
            for tok in extract_numeric_tokens(b.get(source, "")):
                if not re.search(rf"(?<![\d.]){re.escape(tok)}(?![\d])", pool):
                    errs.append(
                        f"numbers[{b['beat']}]: {tok!r} in {source} does not "
                        f"trace to any referenced fact")
    return errs


def gate_banned(beats: list[dict], brand: BrandFacts) -> list[str]:
    banned = ([p.lower() for p in config.SCRIPT_BANNED_PHRASES]
              + [p.lower() for p in config.FEAR_FILLER_PATTERNS]
              + [p.lower() for p in brand.banned_claims])
    errs: list[str] = []
    for b in beats:
        text = f"{b.get('narration', '')} {b.get('card_text', '')}".lower()
        for phrase in banned:
            if phrase in text:
                errs.append(f"banned[{b['beat']}]: contains {phrase!r}")
    return errs


def gate_word_budget(beats: list[dict]) -> list[str]:
    errs: list[str] = []
    tol = config.WORD_BUDGET_TOLERANCE
    for b, spec in zip(beats, config.BEAT_TEMPLATE):
        words = len(b.get("narration", "").split())
        lo = spec["min_s"] * config.WORDS_PER_SECOND * (1 - tol)
        hi = spec["max_s"] * config.WORDS_PER_SECOND * (1 + tol)
        if not (lo <= words <= hi):
            errs.append(f"budget[{spec['beat']}]: {words} words outside "
                        f"[{lo:.0f}, {hi:.0f}]")
    return errs


def gate_card_text(beats: list[dict]) -> list[str]:
    errs: list[str] = []
    for b in beats:
        card = normalize_for_match(b.get("card_text", ""))
        narr = normalize_for_match(b.get("narration", ""))
        words = card.split()
        if len(words) >= 5:
            for i in range(len(words) - 4):
                window = " ".join(words[i:i + 5])
                if window in narr:
                    errs.append(f"card[{b['beat']}]: card_text duplicates narration")
                    break
        if len(words) > 7:
            errs.append(f"card[{b['beat']}]: card_text longer than 7 words")
    return errs


def gate_differentiator(beats: list[dict], brand: BrandFacts) -> list[str]:
    diff_ids = {d["id"] for d in brand.differentiators}
    errs: list[str] = []
    cta = beats[-1]
    in_cta = [f for f in cta.get("fact_ids", []) if f in diff_ids]
    if len(in_cta) != 1:
        errs.append(f"differentiator[cta]: expected exactly one of {sorted(diff_ids)}, "
                    f"got {in_cta}")
    for b in beats[:-1]:
        early = [f for f in b.get("fact_ids", []) if f in diff_ids]
        if early:
            errs.append(f"differentiator[{b['beat']}]: brand differentiators "
                        f"belong in the CTA beat only, found {early}")
    return errs


def run_gates(beats: list[dict], factsheet: dict, brand: BrandFacts) -> list[str]:
    if len(beats) != len(config.BEAT_TEMPLATE):
        return [f"structure: expected {len(config.BEAT_TEMPLATE)} beats, got {len(beats)}"]
    if [b.get("beat") for b in beats] != [s["beat"] for s in config.BEAT_TEMPLATE]:
        return [f"structure: beat order must be "
                f"{[s['beat'] for s in config.BEAT_TEMPLATE]}"]
    return (gate_numbers(beats, factsheet, brand) + gate_banned(beats, brand)
            + gate_word_budget(beats) + gate_card_text(beats)
            + gate_differentiator(beats, brand))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/shorts_engine/test_script_gates.py -v`
Expected: 8 passed.

- [ ] **Step 5: Run the suite**

Run: `python -m pytest tests/shorts_engine -q`
Expected: 42 passed.

---

### Task 12: SCRIPT writer + critique + `run()` (retry-with-gate-echo)

**Files:**
- Modify: `shorts_engine/stages/script.py`
- Test: `tests/shorts_engine/test_script_run.py`

**Interfaces:**
- Consumes: Task 11 gates; `text_llm.generate_schema_json`; `factsheet.json` + `post.json` artifacts.
- Produces: `script.json` (consumed by Plan-2 shotlist):

```json
{"beats": [{"beat": "hook", "narration": "...", "fact_ids": [],
            "card_text": "...", "broll_wish": ""}],
 "critique": {"actionable_score": 8, "coherence_score": 9,
              "hrsu_reason_score": 8, "revise_notes": "..."},
 "attempts": 1}
```

- Module constants `SCRIPT_SCHEMA`, `CRITIQUE_SCHEMA`; function `run(ctx) -> {"script": "script.json"}`. Behavior: write → gates; on gate errors, retry writer with errors echoed (max 3 writer calls); then critique once; if any critique score < 7, one rewrite (with notes) → gates again; final gate failure raises `GateFailure`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_script_run.py
from __future__ import annotations
import json
from pathlib import Path

import pytest

from shorts_engine.errors import GateFailure
from shorts_engine.manifest import RunManifest
from shorts_engine.runner import StageContext
from shorts_engine.stages import script as script_stage

URL = "https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html"

GOOD_BEATS = [
    {"beat": "hook", "narration": "EU nitrate limits are tightening for industry.",
     "fact_ids": [], "card_text": "EU limits tightening", "broll_wish": "wastewater aeration basin"},
    {"beat": "stakes", "narration": "Non-compliance risks penalties and production downtime for your plant.",
     "fact_ids": [], "card_text": "Downtime risk", "broll_wish": ""},
    {"beat": "mechanism", "narration": "Dosing calcium nitrate supports denitrification, turning nitrate load into harmless nitrogen gas in the treatment train and stabilising the process.",
     "fact_ids": [], "card_text": "Nitrate to nitrogen gas", "broll_wish": ""},
    {"beat": "proof", "narration": "Best practice suggests a dosage range of 1.5 to 3 kg per cubic meter of wastewater.",
     "fact_ids": ["f1"], "card_text": "Dosing window", "broll_wish": ""},
    {"beat": "cta", "narration": "HRSU supplies high-purity powder with batch QC. Visit hrsuindore.com for the full guide today.",
     "fact_ids": ["b_purity"], "card_text": "hrsuindore.com", "broll_wish": ""},
]
BAD_BEATS = json.loads(json.dumps(GOOD_BEATS))
BAD_BEATS[3]["narration"] = "Reduces nitrate levels by 150 mg per liter of wastewater flow."

GOOD_CRITIQUE = {"actionable_score": 8, "coherence_score": 9,
                 "hrsu_reason_score": 8, "revise_notes": ""}


def _ctx(tmp_path: Path) -> StageContext:
    m = RunManifest.create(URL, workspace_root=tmp_path)
    ws = Path(m.workspace)
    (ws / "post.json").write_text(json.dumps(
        {"url": URL, "title": "T", "region": "eu",
         "category": "wastewater_treatment", "citations": [], "images": []}),
        encoding="utf-8")
    (ws / "factsheet.json").write_text(json.dumps({
        "facts": [{"id": "f1",
                   "verbatim_quote": "dosage range of 1.5 to 3 kg per cubic meter of wastewater volume",
                   "char_offset": 0, "value": "1.5-3", "unit": "kg/m3",
                   "claim_summary": "dosing", "tags": ["spec"],
                   "procurement_significance": 5, "citation_marker": 1}],
        "brand_facts": [], "dropped": []}), encoding="utf-8")
    return StageContext(manifest=m, workspace=ws, flags={})


def test_happy_path_writes_script(tmp_path, monkeypatch):
    calls = []
    def fake_llm(prompt, system, schema, **kw):
        calls.append(schema)
        if schema is script_stage.SCRIPT_SCHEMA:
            return {"beats": GOOD_BEATS}
        return GOOD_CRITIQUE
    monkeypatch.setattr(script_stage.text_llm, "generate_schema_json", fake_llm)
    out = script_stage.run(_ctx(tmp_path))
    assert out == {"script": "script.json"}
    written = json.loads((tmp_path / "optimizing-nitrate-removal-via-granular" /
                          "script.json").read_text(encoding="utf-8"))
    assert written["attempts"] == 1
    assert written["critique"]["coherence_score"] == 9
    assert len(calls) == 2  # one write + one critique


def test_gate_errors_are_echoed_and_retried(tmp_path, monkeypatch):
    prompts = []
    responses = [{"beats": BAD_BEATS}, {"beats": GOOD_BEATS}, GOOD_CRITIQUE]
    def fake_llm(prompt, system, schema, **kw):
        prompts.append(prompt)
        return responses.pop(0)
    monkeypatch.setattr(script_stage.text_llm, "generate_schema_json", fake_llm)
    script_stage.run(_ctx(tmp_path))
    assert "150" in prompts[1] and "does not trace" in prompts[1]


def test_exhausted_gate_retries_raise(tmp_path, monkeypatch):
    def fake_llm(prompt, system, schema, **kw):
        if schema is script_stage.SCRIPT_SCHEMA:
            return {"beats": BAD_BEATS}
        return GOOD_CRITIQUE
    monkeypatch.setattr(script_stage.text_llm, "generate_schema_json", fake_llm)
    with pytest.raises(GateFailure):
        script_stage.run(_ctx(tmp_path))


def test_low_critique_triggers_one_rewrite(tmp_path, monkeypatch):
    seq = [{"beats": GOOD_BEATS},
           {"actionable_score": 4, "coherence_score": 9, "hrsu_reason_score": 8,
            "revise_notes": "hook is vague"},
           {"beats": GOOD_BEATS}]
    prompts = []
    def fake_llm(prompt, system, schema, **kw):
        prompts.append(prompt)
        return seq.pop(0)
    monkeypatch.setattr(script_stage.text_llm, "generate_schema_json", fake_llm)
    script_stage.run(_ctx(tmp_path))
    assert seq == []                      # all three calls consumed
    assert "hook is vague" in prompts[2]  # rewrite saw the notes
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/shorts_engine/test_script_run.py -v`
Expected: FAIL with `AttributeError: ... has no attribute 'SCRIPT_SCHEMA'` (or ImportError).

- [ ] **Step 3: Implement writer/critique/run (append to script.py)**

```python
# append to shorts_engine/stages/script.py

SCRIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "beats": {
            "type": "array", "minItems": 5, "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "beat": {"enum": [s["beat"] for s in config.BEAT_TEMPLATE]},
                    "narration": {"type": "string"},
                    "fact_ids": {"type": "array", "items": {"type": "string"}},
                    "card_text": {"type": "string"},
                    "broll_wish": {"type": "string"},
                },
                "required": ["beat", "narration", "fact_ids", "card_text",
                             "broll_wish"],
            },
        },
    },
    "required": ["beats"],
}

CRITIQUE_SCHEMA = {
    "type": "object",
    "properties": {
        "actionable_score": {"type": "integer", "minimum": 0, "maximum": 10},
        "coherence_score": {"type": "integer", "minimum": 0, "maximum": 10},
        "hrsu_reason_score": {"type": "integer", "minimum": 0, "maximum": 10},
        "revise_notes": {"type": "string"},
    },
    "required": ["actionable_score", "coherence_score", "hrsu_reason_score",
                 "revise_notes"],
}

_WRITER_SYSTEM = (
    "You write 35-50 second video scripts for procurement managers sourcing "
    "industrial chemicals. Voice: concrete, technical, zero hype. HARD RULES: "
    "every number you use MUST come from a provided fact's verbatim quote, and "
    "that fact's id MUST be listed in the beat's fact_ids. Never invent "
    "statistics. card_text is at most 7 words and must not repeat the "
    "narration. The cta beat cites exactly one brand differentiator id."
)

_CRITIC_SYSTEM = (
    "You are a skeptical procurement manager reviewing a video script. Score "
    "0-10: actionable_score (did I learn something usable?), coherence_score "
    "(is the chemistry/mechanism described correctly and clearly?), "
    "hrsu_reason_score (is there one credible reason to consider HRSU?). "
    "Give concrete revise_notes."
)

_BEAT_RULES = "\n".join(
    f"- {s['beat']}: {s['min_s']:.0f}-{s['max_s']:.0f}s "
    f"(~{s['min_s']*config.WORDS_PER_SECOND:.0f}-"
    f"{s['max_s']*config.WORDS_PER_SECOND:.0f} words)"
    for s in config.BEAT_TEMPLATE)


def _writer_prompt(post_meta: dict, factsheet: dict, brand: BrandFacts,
                   gate_errors: list[str] | None = None,
                   revise_notes: str = "") -> str:
    facts_block = "\n".join(
        f"[{f['id']}] \"{f['verbatim_quote']}\" (value={f['value']} {f['unit']}, "
        f"citation={f['citation_marker']})" for f in factsheet["facts"])
    diff_block = "\n".join(f"[{d['id']}] {d['text']}"
                           for d in brand.differentiators)
    prompt = (
        f"Blog: {post_meta['title']} | region={post_meta.get('region')} | "
        f"category={post_meta.get('category')}\n\n"
        f"FACTS (the ONLY allowed sources of numbers):\n{facts_block}\n\n"
        f"BRAND DIFFERENTIATORS (cite exactly one, in the cta beat only):\n"
        f"{diff_block}\n\nCTA domain: {brand.domain}\n\n"
        f"Beat structure:\n{_BEAT_RULES}\n\n"
        f"Write the 5 beats now as JSON."
    )
    if gate_errors:
        prompt += ("\n\nYour previous draft FAILED these checks — fix every "
                   "one:\n" + "\n".join(f"- {e}" for e in gate_errors))
    if revise_notes:
        prompt += f"\n\nReviewer notes to address:\n{revise_notes}"
    return prompt


def run(ctx) -> dict[str, str]:
    post_meta = json.loads((ctx.workspace / "post.json").read_text(encoding="utf-8"))
    factsheet = json.loads((ctx.workspace / "factsheet.json").read_text(encoding="utf-8"))
    brand = load_brand_facts()
    local_only = ctx.flags.get("local_only", False)

    beats, errors = None, ["no attempt yet"]
    attempts = 0
    for attempt in range(1, 4):                      # ≤3 writer calls
        attempts = attempt
        result = text_llm.generate_schema_json(
            _writer_prompt(post_meta, factsheet, brand,
                           gate_errors=None if attempt == 1 else errors),
            _WRITER_SYSTEM, SCRIPT_SCHEMA, local_only=local_only)
        beats = result["beats"]
        errors = run_gates(beats, factsheet, brand)
        if not errors:
            break
        log.warning("script gates failed (attempt %d): %s", attempt, errors)
    if errors:
        raise GateFailure(errors)

    critique = text_llm.generate_schema_json(
        "Script:\n" + json.dumps(beats, ensure_ascii=False, indent=2),
        _CRITIC_SYSTEM, CRITIQUE_SCHEMA, local_only=local_only)

    if min(critique["actionable_score"], critique["coherence_score"],
           critique["hrsu_reason_score"]) < 7:
        log.info("critique below bar, one rewrite: %s", critique["revise_notes"])
        result = text_llm.generate_schema_json(
            _writer_prompt(post_meta, factsheet, brand,
                           revise_notes=critique["revise_notes"]),
            _WRITER_SYSTEM, SCRIPT_SCHEMA, local_only=local_only)
        rewritten = result["beats"]
        rewrite_errors = run_gates(rewritten, factsheet, brand)
        if rewrite_errors:
            raise GateFailure(rewrite_errors)
        beats = rewritten

    payload = {"beats": beats, "critique": critique, "attempts": attempts}
    (ctx.workspace / "script.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("script written: %d beats, attempts=%d", len(beats), attempts)
    return {"script": "script.json"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/shorts_engine/test_script_run.py -v`
Expected: 4 passed.

- [ ] **Step 5: Run the suite**

Run: `python -m pytest tests/shorts_engine -q`
Expected: 46 passed.

---

### Task 13: CLI (`python -m shorts_engine`)

**Files:**
- Create: `shorts_engine/cli.py`
- Create: `shorts_engine/__main__.py`
- Test: `tests/shorts_engine/test_cli.py`

**Interfaces:**
- Consumes: `runner.run` (Task 3), stage `run` functions (Tasks 7/9/12).
- Produces: `build_stages() -> list[Stage]` = `[("ingest","ingested",ingest.run), ("facts","facts",facts.run), ("script","scripted",script.run)]`; `main(argv: list[str] | None = None) -> int`. Flags: positional `blog_url`; `--until {ingest,facts,script}`; `--resume`; `--local-only`; `--workspace-root PATH` (default `config.OUTPUT_BASE`); `--html-override PATH` (testing/offline).

- [ ] **Step 1: Write the failing tests**

```python
# tests/shorts_engine/test_cli.py
from __future__ import annotations
import json
from pathlib import Path

from shorts_engine import cli
from shorts_engine.stages import facts as facts_stage
from shorts_engine.stages import script as script_stage

URL = "https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html"
FIXTURE = Path(__file__).parent / "fixtures" / "nitrate_post.html"


def test_build_stages_order():
    names = [s[0] for s in cli.build_stages()]
    assert names == ["ingest", "facts", "script"]


def test_cli_until_ingest_runs_offline(tmp_path, capsys):
    rc = cli.main([URL, "--until", "ingest",
                   "--workspace-root", str(tmp_path),
                   "--html-override", str(FIXTURE)])
    assert rc == 0
    ws = tmp_path / "optimizing-nitrate-removal-via-granular"
    assert (ws / "post.json").is_file() and (ws / "canonical.txt").is_file()
    out = capsys.readouterr().out
    assert "ingested" in out and str(ws) in out


def test_cli_failure_returns_nonzero(tmp_path, monkeypatch):
    def boom(ctx):
        raise RuntimeError("no network")
    monkeypatch.setattr(cli.ingest, "run", boom)
    rc = cli.main([URL, "--until", "ingest", "--workspace-root", str(tmp_path)])
    assert rc == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/shorts_engine/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shorts_engine.cli'`.

- [ ] **Step 3: Implement CLI**

```python
# shorts_engine/cli.py
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from shorts_engine import config
from shorts_engine.runner import Stage, run
from shorts_engine.stages import ingest, facts, script

log = logging.getLogger(__name__)


def build_stages() -> list[Stage]:
    return [("ingest", "ingested", ingest.run),
            ("facts", "facts", facts.run),
            ("script", "scripted", script.run)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="shorts_engine",
        description="Blog → short-form video pipeline (v3). Plan-1 scope: "
                    "ingest → facts → script.")
    parser.add_argument("blog_url")
    parser.add_argument("--until", choices=["ingest", "facts", "script"])
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--local-only", action="store_true",
                        help="use the local model (dev only; labeled in manifest)")
    parser.add_argument("--workspace-root", type=Path, default=config.OUTPUT_BASE)
    parser.add_argument("--html-override", type=Path,
                        help="use a local HTML file instead of fetching the URL")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")

    flags: dict = {"local_only": args.local_only}
    if args.html_override:
        flags["html_override"] = args.html_override

    try:
        manifest = run(args.blog_url, build_stages(),
                       workspace_root=args.workspace_root,
                       until=args.until, resume=args.resume, flags=flags)
    except Exception as exc:  # fail loud, but exit cleanly for shells
        log.error("run failed: %s", exc)
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    if args.local_only:
        manifest.model_tier = "local"
        manifest.save()
    print(f"status={manifest.status} workspace={manifest.workspace}")
    for name, rel in sorted(manifest.artifacts.items()):
        print(f"  {name}: {Path(manifest.workspace) / rel}")
    return 0
```

```python
# shorts_engine/__main__.py
from shorts_engine.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/shorts_engine/test_cli.py -v`
Expected: 3 passed.

- [ ] **Step 5: Run the suite**

Run: `python -m pytest tests/shorts_engine -q`
Expected: 49 passed.

---

### Task 14: End-to-end integration test (mocked LLM) — the never-unverified invariant

**Files:**
- Test: `tests/shorts_engine/test_integration.py`

**Interfaces:**
- Consumes: everything above. No new production code.

- [ ] **Step 1: Write the integration tests**

```python
# tests/shorts_engine/test_integration.py
"""Full INGEST→FACTS→SCRIPT run on the poisoned fixture with a scripted LLM.
Pins the two Plan-1 invariants end-to-end:
  1. isolation: sibling-post text cannot reach the factsheet
  2. never-unverified: a fabricated number cannot reach script.json"""
from __future__ import annotations
import json
from pathlib import Path

import pytest

from shorts_engine import cli
from shorts_engine.errors import GateFailure
from shorts_engine.stages import facts as facts_stage
from shorts_engine.stages import script as script_stage

URL = "https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html"
FIXTURE = Path(__file__).parent / "fixtures" / "nitrate_post.html"

REAL_QUOTE = ("dosage range of 1.5 to 3 kg per cubic meter of wastewater volume")

GOOD_BEATS = [
    {"beat": "hook", "narration": "EU nitrate limits are tightening for industry.",
     "fact_ids": [], "card_text": "EU limits tightening", "broll_wish": ""},
    {"beat": "stakes", "narration": "Non-compliance risks penalties and production downtime for your plant.",
     "fact_ids": [], "card_text": "Downtime risk", "broll_wish": ""},
    {"beat": "mechanism", "narration": "Dosing calcium nitrate supports denitrification, turning nitrate load into harmless nitrogen gas in the treatment train and stabilising the process.",
     "fact_ids": [], "card_text": "Nitrate to nitrogen gas", "broll_wish": ""},
    {"beat": "proof", "narration": "Best practice suggests a dosage range of 1.5 to 3 kg per cubic meter of wastewater.",
     "fact_ids": ["f1"], "card_text": "Dosing window", "broll_wish": ""},
    {"beat": "cta", "narration": "HRSU supplies high-purity powder with batch QC. Visit hrsuindore.com for the full guide today.",
     "fact_ids": ["b_purity"], "card_text": "hrsuindore.com", "broll_wish": ""},
]
CRITIQUE = {"actionable_score": 8, "coherence_score": 9,
            "hrsu_reason_score": 8, "revise_notes": ""}


def _fact_llm(prompt, system, schema, **kw):
    if schema is facts_stage.FACT_WRAP_SCHEMA:
        return {"facts": [
            {"id": "f1", "verbatim_quote": REAL_QUOTE, "value": "1.5-3",
             "unit": "kg/m3", "claim_summary": "dosing window",
             "tags": ["spec"], "procurement_significance": 5,
             "citation_marker": 1},
            {"id": "f2", "verbatim_quote": "approximately 150,000 metric tons",
             "value": "150000", "unit": "t", "claim_summary": "POISON from sibling post",
             "tags": ["metric"], "procurement_significance": 3,
             "citation_marker": None},
        ]}
    raise AssertionError(f"unexpected schema in facts phase: {schema}")


def test_full_run_produces_grounded_script(tmp_path, monkeypatch):
    def router(prompt, system, schema, **kw):
        if schema is facts_stage.FACT_WRAP_SCHEMA:
            return _fact_llm(prompt, system, schema, **kw)
        if schema is script_stage.SCRIPT_SCHEMA:
            return {"beats": GOOD_BEATS}
        if schema is script_stage.CRITIQUE_SCHEMA:
            return CRITIQUE
        raise AssertionError("unknown schema")
    monkeypatch.setattr(facts_stage.text_llm, "generate_schema_json", router)
    monkeypatch.setattr(script_stage.text_llm, "generate_schema_json", router)

    rc = cli.main([URL, "--until", "script", "--workspace-root", str(tmp_path),
                   "--html-override", str(FIXTURE)])
    assert rc == 0
    ws = tmp_path / "optimizing-nitrate-removal-via-granular"

    factsheet = json.loads((ws / "factsheet.json").read_text(encoding="utf-8"))
    kept_ids = [f["id"] for f in factsheet["facts"]]
    assert "f1" in kept_ids
    assert "f2" not in kept_ids, "sibling-post fact must be dropped by the verbatim gate"
    assert any("not located" in d["reason"] for d in factsheet["dropped"])

    script = json.loads((ws / "script.json").read_text(encoding="utf-8"))
    assert len(script["beats"]) == 5
    assert script["beats"][3]["fact_ids"] == ["f1"]


def test_fabricated_number_blocks_the_run(tmp_path, monkeypatch):
    bad_beats = json.loads(json.dumps(GOOD_BEATS))
    bad_beats[3]["narration"] = "Reduces nitrate levels by 150 mg per liter."
    def router(prompt, system, schema, **kw):
        if schema is facts_stage.FACT_WRAP_SCHEMA:
            return _fact_llm(prompt, system, schema, **kw)
        if schema is script_stage.SCRIPT_SCHEMA:
            return {"beats": bad_beats}
        return CRITIQUE
    monkeypatch.setattr(facts_stage.text_llm, "generate_schema_json", router)
    monkeypatch.setattr(script_stage.text_llm, "generate_schema_json", router)

    rc = cli.main([URL, "--until", "script", "--workspace-root", str(tmp_path),
                   "--html-override", str(FIXTURE)])
    assert rc == 1, "run must fail loudly when every draft contains untraced numbers"
    manifest = json.loads((tmp_path / "optimizing-nitrate-removal-via-granular" /
                           "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert "150" in manifest["error"]
```

- [ ] **Step 2: Run the tests**

Run: `python -m pytest tests/shorts_engine/test_integration.py -v`
Expected: 2 passed (all production code already exists; if either fails, the referenced gate/stage has a real bug — fix the production module, not the test).

- [ ] **Step 3: Run the full engine suite plus regression check**

Run: `python -m pytest tests/shorts_engine -q`
Expected: 51 passed.
Run: `python -m pytest tests -q`
Expected: all pre-existing suites still green (no regressions; count = previous total + 51).

---

### Task 15: Live smoke run (real blog, real 31B) + progress report

**Files:**
- Create: `docs/superpowers/progress/2026-XX-XX-shorts-engine-plan1-smoke.md` (fill actual date)

**Interfaces:**
- Consumes: the finished Plan-1 CLI; Ollama Cloud reachable; `brand_facts.yaml` present.

- [ ] **Step 1: Verify Ollama cloud model is reachable**

Run: `ollama list`
Expected: `gemma4:31b-cloud` appears. If not, stop and report — do NOT switch models.

- [ ] **Step 2: Run the live pipeline to script stage**

Run: `python -m shorts_engine https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html --until script`
Expected: exit 0; console shows `status=scripted` and artifact paths under `output\videos\optimizing-nitrate-removal-via-granular\`.

- [ ] **Step 3: Manually verify the three invariants on real output**

Run: `python -c "import json,re;ws='output/videos/optimizing-nitrate-removal-via-granular/';c=open(ws+'canonical.txt',encoding='utf-8').read();s=json.load(open(ws+'script.json',encoding='utf-8'));f=json.load(open(ws+'factsheet.json',encoding='utf-8'));fs={x['id']:x for x in f['facts']};print('facts kept:',len(fs),'dropped:',len(f['dropped']));print('sibling poison absent:','150,000 metric tons' not in c);[print(b['beat'],'| nums:',re.findall(r'\d[\d,.]*',b['narration']),'| fact_ids:',b['fact_ids']) for b in s['beats']]"`
Expected output: `sibling poison absent: True`; every beat's numeric list is either empty or its beat lists fact_ids whose quotes contain those numbers (spot-check by eye against factsheet.json).

- [ ] **Step 4: Read the script aloud (the taste check)**

Open `script.json`. Confirm: the hook names a real problem; the mechanism sentence is chemically coherent; the cta cites exactly one differentiator. If the writing is flat, tune `_WRITER_SYSTEM` / `_CRITIC_SYSTEM` wording ONLY (no gate changes) and re-run this task.

- [ ] **Step 5: Write the progress report**

Create `docs/superpowers/progress/<today>-shorts-engine-plan1-smoke.md` with: suite count (51 engine tests + full-suite total), the live run's kept/dropped fact counts, the final script text, any prompt tweaks made in Step 4, and open observations for Plan 2 (cards & assembly).

---

## Self-Review (performed while writing)

1. **Spec coverage (Plan-1 scope = spec Phases 1–2):** §3.1 layout (partial: llm/, stages/, manifest, runner, cli, config, errors — cards/sourcing/review are Plans 2–3) ✓; §3.2 reuse contract + forbidden-import guard → Task 1 ✓; §3.3 state machine → Tasks 2–3 ✓; §3.4 never-unverified → Tasks 8–12, 14 ✓ (never-blank lands with cards in Plan 2 by design); §4 Stage 1 → Tasks 5–7 (incl. citation classification for Plan-3 PAPER_CARD) ✓; §4 Stage 2 → Tasks 8–9 ✓; §4 Stage 3 → Tasks 11–12 ✓; §7 brand facts → Task 10 ✓; §8 reliability rules 1–2 → Task 4, rule 3 partially (per-stage logging; quality_report_v2 aggregation belongs to Plan 3) ✓; §9 unit+integration coverage for these stages → Tasks 1–14 ✓.
2. **Placeholder scan:** no TBD/TODO; every step has full code or an exact command with expected output. The two "if the fixture differs, adapt" notes give the exact inspection command and constrain what may change — investigation steps, not placeholders.
3. **Type consistency:** `RunManifest.checkpoint(status, **artifacts)` used identically in Tasks 2/3/7/9/12; `StageContext(manifest, workspace, flags)` consistent across 3/7/9/12/13/14; `generate_schema_json(prompt, system, schema, *, retries, local_only, client_factory)` consistent between Task 4 and callers (facts/script pass keyword args only); gate signatures in Task 11 match Task 12's `run_gates(beats, factsheet, brand)` and the tests; `last_ok_status` added to the manifest in Task 3 is exercised by Task 3's resume tests and persisted via `asdict`.

---

## After Plan 1

Plan 2 (cards & assembly — spec Phases 3–4) gets written when Plan 1's smoke run is approved; Plan 3 (acquisition & verification — spec Phases 5–7) after that. The `--torture` flag and never-blank guarantee arrive with Plan 2's card renderers.
