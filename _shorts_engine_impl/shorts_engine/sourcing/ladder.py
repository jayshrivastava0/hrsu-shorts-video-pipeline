"""Acquisition ladder (spec §6.1): own library → blog images → free APIs →
scrape. Gates first (hard rejects are final, F2), then the describe-then-
match judge. First acceptance wins; nothing accepted ⇒ caller renders the
declared fallback (never-blank)."""
from __future__ import annotations

import logging
from pathlib import Path

from shorts_engine import config

logger = logging.getLogger(__name__)

_THRESHOLDS = {"own": None, "blog": None, "api": None, "scrape": None}


def _thresholds() -> dict:
    return {"own": config.JUDGE_MIN_OWN, "blog": config.JUDGE_MIN_BLOG,
            "api": config.JUDGE_MIN_API, "scrape": config.JUDGE_MIN_SCRAPE}


# ── Late-binding seams (audio.py pattern) ────────────────────────────────────
def _query_library(wish: str) -> list[dict]:
    from shorts_engine.sourcing.library_index import query
    return query(wish)


def _search_tier(tier: str, wish: str) -> list:
    from shorts_engine.sourcing.adapters import search_tier
    return search_tier(tier, wish)


def _download(cand, dest_dir: Path) -> Path | None:
    from shorts_engine.sourcing.adapters import download
    return download(cand, dest_dir)


def _watermarked(img_path: Path) -> bool:
    from shorts_engine.sourcing.gates import watermarked
    return watermarked(img_path)


def _judge(image_path: Path, wish: str, narration_span: str) -> dict:
    from shorts_engine.llm.vision_judge import judge
    return judge(image_path, wish, narration_span)


def _accept(tier_rec: dict, path: Path, verdict: dict, url: str) -> dict:
    tier_rec["accepted"] = {"path": str(path), "url": url,
                            "score": verdict["accepted_score"],
                            "focal_hint": verdict["focal_hint"]}
    return {"image_path": str(path), "focal_hint": verdict["focal_hint"]}


def acquire(wish: str, narration_span: str, workspace: Path,
            post_images: list[dict], torture: bool = False) -> dict:
    provenance: dict = {"tiers": [], "reason": None}
    result = {"image_path": None, "focal_hint": "center",
              "provenance": provenance}
    if torture:
        provenance["reason"] = "torture_mode"
        return result
    if not (wish or "").strip():
        provenance["reason"] = "no_wish"
        return result

    thresholds = _thresholds()
    dl_dir = Path(workspace) / "broll"

    for tier in ("own", "blog", "api", "scrape"):
        rec = {"tier": tier, "candidates_seen": 0, "rejections": [],
               "accepted": None}
        provenance["tiers"].append(rec)
        judged = 0
        seen: set[str] = set()

        if tier == "own":
            for hit in _query_library(wish):
                if judged >= config.PER_TIER_CANDIDATES:
                    break
                rec["candidates_seen"] += 1
                judged += 1
                v = _judge(Path(hit["path"]), wish, narration_span)
                if v["reject_reason"] is None and \
                        v["accepted_score"] >= thresholds[tier]:
                    result.update(_accept(rec, Path(hit["path"]), v, hit["path"]))
                    return result
                rec["rejections"].append(
                    {"url": hit["path"],
                     "reason": v["reject_reason"] or f"score_{v['accepted_score']}"})
            continue

        # candidate-producing tiers
        if tier == "blog":
            from types import SimpleNamespace
            # post.json's images carry {"src", "alt"} (ingest._extract_images
            # never sets "url" or dimensions) -- filtering on i.get("url")
            # silently dropped every blog image, making this whole tier dead.
            # Since real dimensions are unknown until download, give each
            # candidate a placeholder that clears the pre-download gate;
            # download() backfills the REAL PIL-read dimensions onto `cand`
            # (same as every other tier), and the post-download recheck at
            # resolution_ok(cand.width, cand.height) below is the actual
            # authority -- a genuinely small image is still rejected there.
            cands = [SimpleNamespace(url=i.get("url") or i.get("src", ""),
                                     width=config.MIN_LONG_EDGE_PX,
                                     height=config.MIN_LONG_EDGE_PX)
                     for i in (post_images or [])
                     if i.get("url") or i.get("src")]
        else:
            cands = _search_tier(tier, wish)

        for cand in cands:
            if judged >= config.PER_TIER_CANDIDATES:
                break
            rec["candidates_seen"] += 1
            from shorts_engine.sourcing.gates import run_pre_gates
            reason = run_pre_gates(cand, seen)
            if reason:
                rec["rejections"].append({"url": cand.url, "reason": reason})
                continue                     # hard reject — FINAL (F2)
            local = _download(cand, dl_dir)
            if local is None:
                rec["rejections"].append({"url": cand.url, "reason": "download_failed"})
                continue
            from shorts_engine.sourcing.gates import resolution_ok
            if not resolution_ok(cand.width, cand.height):
                rec["rejections"].append({"url": cand.url, "reason": "low_resolution_actual"})
                continue
            if _watermarked(local):
                rec["rejections"].append({"url": cand.url, "reason": "watermarked"})
                continue
            judged += 1
            v = _judge(local, wish, narration_span)
            if v["reject_reason"] is None and \
                    v["accepted_score"] >= thresholds[tier]:
                result.update(_accept(rec, local, v, cand.url))
                return result
            rec["rejections"].append(
                {"url": cand.url,
                 "reason": v["reject_reason"] or f"score_{v['accepted_score']}"})

    provenance["reason"] = "no_acceptance"
    return result
