"""Text stage for brief-driven videos.

Explainer briefs run the explanation-first engine on a synthetic source page
(`--html-override`). Other types write their script with the brief writer and
then run the audio stage through `shorts_engine.stage_cli`.
"""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Callable, Optional

from scheduler.director import CmdResult, Runner, run_cmd
from scheduler.textstage import (
    ENGINE_PATH,
    READY_STATUSES,
    REPO_ROOT,
    TEXT_TIMEOUT_S,
    TextResult,
    _find_run_dir,
    default_workspace_root,
)
from scheduler.briefs.claims import ThesisUnsourced, apply_verdicts
from scheduler.briefs.facts import load_facts
from scheduler.briefs.intake import extract_asset_text
from scheduler.briefs.model import Brief
from scheduler.briefs.research import (
    ClaimVerdict, Source, gather_sources, make_queries, trusted_site_queries, verify_claims,
)
from scheduler.briefs.types import Recipe, recipe_for
from scheduler.briefs.writer import WriterFailed, write_script, write_support_files

BRIEF_URL_PREFIX = "https://hrsuindore.com/brief/"
ARTIFACTS = ("script.json", "references.json", "video_description.txt", "beats_audio.json")
_RUN_ID = re.compile(r'"run_id":\s*"(run-[0-9a-f]+)"')
_VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "supported": {"type": "boolean"},
        "source_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["supported", "source_ids"],
}


def manifest_key(brief: Brief) -> str:
    """Manifest key URL for a brief. `Brief.url` is the owner's blog URL and is kept separate."""
    return f"{BRIEF_URL_PREFIX}{brief.id}/"


def _default_writer_llm(system: str, user: str, schema: dict) -> dict:
    from shorts_engine.llm.text_llm import generate_schema_json

    return generate_schema_json(user, system, schema, role="writer")


def _default_verifier(prompt: str) -> dict:
    from shorts_engine.llm.text_llm import generate_schema_json

    return generate_schema_json(prompt, "Check whether the sources support the claim.",
                                _VERDICT_SCHEMA, role="verifier")


def _env() -> dict:
    pypath = os.pathsep.join([str(ENGINE_PATH), str(REPO_ROOT), os.environ.get("PYTHONPATH", "")])
    return {"PYTHONPATH": pypath.strip(os.pathsep)}


def _stage_cli(*args: str) -> list[str]:
    return [sys.executable, "-m", "shorts_engine.stage_cli", *args]


def _explainer_html(brief: Brief, sources: list[Source]) -> str:
    lines = [ln.strip() for ln in brief.text.splitlines() if ln.strip()]
    title = html.escape(lines[0] if lines else brief.id)
    paras = "".join(f"<p>{html.escape(ln)}</p>" for ln in lines)
    items = "".join(
        f'<li><a href="{html.escape(s.url)}">{html.escape(s.title)}</a>: {html.escape(s.snippet)}</li>'
        for s in sources
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<title>{title}</title></head><body><article><h1>{title}</h1>{paras}"
        f"<h2>Sources</h2><ul>{items}</ul></article></body></html>\n"
    )


def _copy_inputs(run_dir: Path, brief: Brief) -> None:
    shutil.copy2(REPO_ROOT / "brand_facts.yaml", run_dir / "brand_facts.yaml")
    if brief.assets:
        text = "\n".join(extract_asset_text(Path(a)) for a in brief.assets)
        (run_dir / "assets_text.txt").write_text(text, encoding="utf-8")
        assets_dir = run_dir / "assets"
        assets_dir.mkdir(exist_ok=True)
        for a in brief.assets:
            shutil.copy2(Path(a), assets_dir / Path(a).name)


def _unique(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


def _rewrite_references(run_dir: Path, recipe: Recipe, sources: list[Source],
                        verdicts: list[ClaimVerdict]) -> Optional[str]:
    """Rewrite references.json with verified claims, sources and chips. Returns an error or None."""
    refs_path = run_dir / "references.json"
    try:
        refs = json.loads(refs_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return f"unreadable references.json after write_support_files: {exc}"
    if not isinstance(refs, dict):
        return "malformed references.json after write_support_files: not an object"
    if recipe.name == "market":
        # Market claims come only from verdicts (empty when there are none); never the writer's.
        refs["claims"] = {
            f"c{i}": {
                "text": v.claim,
                "sources": list(v.source_ids),
                "tier": "verified",
                "chip": None,
                "stance": "checkable",
            }
            for i, v in enumerate((v for v in verdicts if v.supported), 1)
        }
    refs["sources"] = [
        {"id": s.id, "title": s.title, "publisher": s.publisher, "url": s.url,
         "kind": "web", "tier": "unverified"}
        for s in sources
    ]
    refs["chips"] = _unique([s.publisher for s in sources]) if recipe.name in ("market", "esg") else []
    refs_path.write_text(json.dumps(refs, indent=2, ensure_ascii=False), encoding="utf-8")
    return None


def _finish(run_dir: Optional[Path], r: CmdResult, timeout: float) -> TextResult:
    """Classify a finished text run the same way textstage.run_text_stages does."""
    if r.timed_out:
        return TextResult("failed", run_dir, f"text stages timed out after {timeout:.0f}s")
    if run_dir is None:
        return TextResult("failed", None, f"no run workspace found (rc={r.returncode}): {r.output[-300:]}")
    try:
        manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    except Exception as exc:
        return TextResult("failed", run_dir, f"unreadable run_manifest.json: {exc}")
    status = manifest.get("status")
    if status == "hold_for_review":
        return TextResult("held", run_dir, manifest.get("error") or "hold_for_review")
    if r.returncode != 0 or status == "failed":
        return TextResult("failed", run_dir, manifest.get("error") or f"rc={r.returncode}: {r.output[-300:]}")
    if status not in READY_STATUSES:
        return TextResult("failed", run_dir, f"stopped at status {status!r}")
    for name in ARTIFACTS:
        if not (run_dir / name).exists():
            return TextResult("failed", run_dir, f"{name} missing after text stages")
    return TextResult("ready", run_dir, None)


def _run_explainer(brief: Brief, root: Path, sources: list[Source], run: Runner,
                   timeout: float, log_path: Optional[Path]) -> TextResult:
    key = manifest_key(brief)
    html_path = root / f"{brief.id}.source.html"
    html_path.write_text(_explainer_html(brief, sources), encoding="utf-8")
    cmd = [sys.executable, "-m", "shorts_engine", key, "--until", "audio",
           "--workspace-root", str(root), "--html-override", str(html_path)]
    r = run(cmd, cwd=REPO_ROOT, timeout=timeout, env=_env(), log_path=log_path)
    run_dir = None if r.timed_out else _find_run_dir(root, key, r.output)
    if run_dir is not None:
        _copy_inputs(run_dir, brief)
    return _finish(run_dir, r, timeout)


def _run_scripted(brief: Brief, recipe: Recipe, root: Path, sources: list[Source], *,
                  run: Runner, llm, verifier, timeout: float,
                  log_path: Optional[Path]) -> TextResult:
    try:
        script = write_script(brief, recipe, load_facts(), sources, llm)
    except WriterFailed as exc:
        return TextResult("failed", None, str(exc))

    verdicts: list[ClaimVerdict] = []
    if recipe.name == "market":
        claims = [b["narration"] for b in script["beats"] if b.get("kind") == "fact"]
        verdicts = verify_claims(claims, sources, verifier or _default_verifier)
        try:
            script = apply_verdicts(script, verdicts, script.get("thesis"))
        except ThesisUnsourced as exc:
            return TextResult("failed", None, str(exc))

    key = manifest_key(brief)
    init = run(_stage_cli("init", key, "--workspace-root", str(root)),
               cwd=REPO_ROOT, timeout=timeout, env=_env(), log_path=log_path)
    if init.timed_out or init.returncode != 0:
        return TextResult("failed", None, f"stage_cli init failed (rc={init.returncode}): {init.output[-300:]}")
    m = _RUN_ID.search(init.output or "")
    if m and (root / m.group(1)).exists():
        run_dir: Optional[Path] = root / m.group(1)
    else:
        run_dir = _find_run_dir(root, key, init.output)
    if run_dir is None:
        return TextResult("failed", None, f"no run workspace found after init: {init.output[-300:]}")

    _copy_inputs(run_dir, brief)
    write_support_files(run_dir, brief, script, sources)
    err = _rewrite_references(run_dir, recipe, sources, verdicts)
    if err:
        return TextResult("failed", run_dir, err)

    # The engine's run-stage audio only runs from "shotlisted"; we authored script.json ourselves.
    checkpoint = run(_stage_cli("checkpoint", "--workspace", str(run_dir), "--status", "shotlisted"),
                     cwd=REPO_ROOT, timeout=timeout, env=_env(), log_path=log_path)
    if checkpoint.timed_out or checkpoint.returncode != 0:
        return TextResult("failed", run_dir,
                          f"stage_cli checkpoint failed (rc={checkpoint.returncode}): {checkpoint.output[-300:]}")

    audio = run(_stage_cli("run-stage", "audio", "--workspace", str(run_dir)),
                cwd=REPO_ROOT, timeout=timeout, env=_env(), log_path=log_path)
    return _finish(run_dir, audio, timeout)


def run_brief_text(brief: Brief, workspace_root: Path | None = None, *, run: Runner = run_cmd,
                   llm: Optional[Callable] = None, search: Optional[Callable] = None,
                   verifier: Optional[Callable] = None, timeout: float = TEXT_TIMEOUT_S,
                   log_path: Optional[Path] = None) -> TextResult:
    """Run the text stages for one brief.

    `llm` writes the script (default: writer role), `verifier` checks market claims
    (default: verifier role), `search` gathers sources (default: web search).
    """
    root = Path(workspace_root) if workspace_root else default_workspace_root()
    root.mkdir(parents=True, exist_ok=True)
    recipe = recipe_for(brief)
    sources: list[Source] = []
    if recipe.needs_research:
        queries = make_queries(brief.text, llm or _default_writer_llm)
        trusted = recipe.facts_source in ("sources", "facts_and_sources")
        if trusted:
            queries = queries + trusted_site_queries(queries[0])
        sources = gather_sources(brief.text, search=search, queries=queries, trusted_only=trusted)
        if recipe.facts_source == "sources" and not sources:
            return TextResult("failed", None,
                              "no trusted web sources found for this brief (government, standards, HRSU's own "
                              "site or a recognised market publisher); check the network connection and rerun "
                              "with --yes, or edit TRUSTED_PUBLISHERS in scheduler/briefs/research.py")
    if brief.type == "explainer":
        return _run_explainer(brief, root, sources, run, timeout, log_path)
    return _run_scripted(brief, recipe, root, sources, run=run, llm=llm or _default_writer_llm,
                         verifier=verifier, timeout=timeout, log_path=log_path)
