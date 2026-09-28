"""Real-figure acquisition for `request_source_figure`: reuses a chart/table/diagram
already present in a citation the shot's fact is ALREADY grounded in — never an open-web
search. A shot may only request a figure for a `fact_id` its own brief already cites, so
every number a returned figure could put on screen stays traceable to a source this run
already verified against (same invariant `paper_page.py`'s PAPER_CARD shot and the
`sourcing/ladder.py` acquisition tiers both rely on)."""
from __future__ import annotations

import logging
from pathlib import Path

from shorts_engine import config

logger = logging.getLogger(__name__)


def _find_fact(fact_id: str, factsheet: dict) -> dict | None:
    for f in factsheet.get("facts", []):
        if f.get("id") == fact_id:
            return f
    return None


def _find_citation_url(marker, post: dict) -> str | None:
    if marker is None:
        return None
    for c in post.get("citations", []):
        if c.get("marker") == marker:
            return c.get("url")
    return None


def _fetch_candidates(url: str, workspace: Path, torture: bool) -> list[Path]:
    from shorts_engine.sourcing.paper_page import fetch_figure_candidates
    return fetch_figure_candidates(url, workspace, torture=torture)


def _judge(image_path: Path, wish: str, narration_span: str) -> dict:
    from shorts_engine.llm.vision_judge import judge
    return judge(image_path, wish, narration_span)


def acquire_figure(fact_id: str, factsheet: dict, post: dict, workspace: Path,
                   torture: bool = False) -> dict:
    provenance: dict = {"fact_id": fact_id, "candidates": [], "reason": None}
    result = {"image_path": None, "focal_hint": "center", "provenance": provenance}
    if torture:
        provenance["reason"] = "torture_mode"
        return result

    fact = _find_fact(fact_id, factsheet)
    if fact is None:
        provenance["reason"] = "unknown_fact_id"
        return result

    url = _find_citation_url(fact.get("citation_marker"), post)
    if not url:
        provenance["reason"] = "no_citation"
        return result

    candidates = _fetch_candidates(url, workspace, torture)
    if not candidates:
        provenance["reason"] = "no_pages_fetched"
        return result

    wish = ("a chart, graph, table, or diagram showing: "
            f"{fact.get('claim_summary') or fact.get('verbatim_quote', '')}")
    narration_span = fact.get("verbatim_quote", "")

    for page_path in candidates:
        v = _judge(page_path, wish, narration_span)
        provenance["candidates"].append(
            {"path": str(page_path), "score": v["accepted_score"],
             "reject_reason": v["reject_reason"]})
        if v["reject_reason"] is None and v["accepted_score"] >= config.JUDGE_MIN_FIGURE:
            result["image_path"] = str(page_path)
            result["focal_hint"] = v["focal_hint"]
            return result

    provenance["reason"] = "no_acceptance"
    return result
