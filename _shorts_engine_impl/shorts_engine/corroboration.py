"""Authority corroboration of verified claims: independently find a passage from an
authoritative source (government, regulator, standards body, publisher) that supports a
claim, so a video can truthfully show a source chip ("US EPA").

This never changes a claim's `verdict` or `support`: it only adds `claim["corroboration"]`
= {"url", "publisher", "quote", "tier": "authoritative", "kind"} when an authoritative
passage itself states or directly entails the claim as written (numbers included).
Every failure (search, fetch, LLM) means "not corroborated", never an exception.

CLI: python -m shorts_engine.corroboration <workspace>   (experiments on an existing run)
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from shorts_engine import config, explanation
from shorts_engine.llm import text_llm
from shorts_engine.references import is_weak_host, publisher_label
from shorts_engine.retrieval import Retriever, source_kind

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a very strict fact-checker deciding whether an authoritative source passage "
    "corroborates ONE claim. Answer 'supported' ONLY if the passage itself explicitly states "
    "the claim, or directly entails it, about the SAME substance, process and conditions "
    "the claim names. Answer 'unsupported' if the passage is only related, only about a "
    "different substance or situation, more general or more specific in a way that changes "
    "the meaning, or merely shares keywords. Do not use outside knowledge and do not fill "
    "gaps. 'contradicted' if the passage states the opposite. passage_index is the passage "
    "you relied on (null if none)."
)


def _vc():
    from shorts_engine.stages import verify_claims  # lazy: verify_claims imports this module
    return verify_claims


def _kind_of(url: str) -> str:
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    if host.endswith(".gov") or any(host == d or host.endswith("." + d)
                                    for d in config.STANDARD_DOMAINS):
        return "standard"
    if any(host == d or host.endswith("." + d) for d in config.PAPER_DOMAINS):
        return "paper"
    return "web"


def select_key_claims(plan: dict, limit: int = 10) -> list[dict]:
    """Surviving non-illustrative claims worth corroborating (references to the plan's own
    dicts): kinds blog_stated/external_fact only (`reasoning` claims inherit), mechanism
    before fact, taken round-robin across steps so every step is represented."""
    per_step: list[list[dict]] = []
    for step in plan.get("steps", []):
        cands = [c for c in step.get("claims", [])
                 if c.get("verdict") == "supported"
                 and c.get("kind") in ("blog_stated", "external_fact")]
        cands.sort(key=lambda c: 0 if c.get("role") == "mechanism" else 1)  # stable
        per_step.append(cands)
    out: list[dict] = []
    depth = 0
    while len(out) < limit and any(len(c) > depth for c in per_step):
        for cands in per_step:
            if len(cands) > depth and len(out) < limit:
                out.append(cands[depth])
        depth += 1
    return out


def corroborate_claim(claim: dict, retriever, *, local_only: bool = False,
                      terms: list | None = None) -> dict | None:
    """Corroboration dict for `claim` from an authoritative passage, or None."""
    text = claim.get("text", "")
    try:
        passages = [p for p in retriever.retrieve_authoritative(text)
                    if source_kind(p.url) == "authoritative" and not is_weak_host(p.url)]
        if not passages:
            return None
        vc = _vc()
        listing = "\n".join(f"[{i}] ({p.url}) {p.text}" for i, p in enumerate(passages))
        res = text_llm.generate_schema_json(
            f"Claim: {text}\n\nPassages:\n{listing}", _SYSTEM, vc.VERDICT_SCHEMA,
            local_only=local_only, role="verifier")
        if res.get("verdict") != "supported":
            return None
        idx = res.get("passage_index")
        if not vc._valid_index(idx, len(passages)):
            return None
        passage = passages[idx]
        if explanation.gate_claim_numbers(text, passage.text, terms or []):
            return None
        return {"url": passage.url, "publisher": publisher_label(passage.url),
                "quote": passage.text, "tier": "authoritative", "kind": _kind_of(passage.url)}
    except Exception as exc:
        logger.warning(f"corroboration failed for {text!r}: {exc}")
        return None


def corroborate_plan(plan: dict, retriever, *, limit: int | None = None,
                     local_only: bool = False) -> dict:
    """Add `corroboration` to the plan's key claims that an authoritative passage supports
    (mutates `plan`; verdicts/supports untouched). Returns {"attempted", "corroborated"}."""
    limit = config.CORROBORATION_LIMIT if limit is None else limit
    step_terms = {c["id"]: s.get("terms", []) for s in plan.get("steps", [])
                  for c in s.get("claims", []) if "id" in c}
    attempted = done = 0
    for claim in select_key_claims(plan, limit):
        attempted += 1
        found = corroborate_claim(claim, retriever, local_only=local_only,
                                  terms=step_terms.get(claim.get("id"), []))
        if found:
            claim["corroboration"] = found
            done += 1
        else:
            claim.pop("corroboration", None)
    logger.info(f"corroboration: {done}/{attempted} key claims corroborated by authorities")
    return {"attempted": attempted, "corroborated": done}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: python -m shorts_engine.corroboration <workspace>", file=sys.stderr)
        return 2
    from shorts_engine import references
    ws = Path(argv[0])
    plan_path = ws / "explanation_plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    post = json.loads((ws / "post.json").read_text(encoding="utf-8"))
    retriever = Retriever([c["url"] for c in post.get("citations", []) if c.get("url")])
    counts = corroborate_plan(plan, retriever)
    plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    refs = references.write_outputs(ws)
    selected = {c["id"] for c in select_key_claims(plan, config.CORROBORATION_LIMIT)}
    print(f"{'claim':62} | ok  | publisher | url | quote")
    for step in plan.get("steps", []):
        for c in step.get("claims", []):
            if c["id"] not in selected:
                continue
            co = c.get("corroboration")
            head = c["text"][:60].ljust(60)
            if co:
                print(f"{head} | yes | {co['publisher']} | {co['url']} | {co['quote'][:160]!r}")
            else:
                print(f"{head} | no")
    g = refs["grade"]
    print(f"corroborated {counts['corroborated']}/{counts['attempted']}; grade={g['verdict']} "
          f"authoritative={g['authoritative']}/{g['key_claims']} "
          f"chips={[c['label'] for c in refs['chips']]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
