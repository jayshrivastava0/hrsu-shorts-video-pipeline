"""Standalone verification for the bar-chart and source-extraction fixes.

1. Replays the smoke_e2e Ollama output through `_inject_bar_chart` and asserts
   the new logic produces clean labels grouped by a single unit.
2. Fetches the real `lime-neutralization` blog post and runs
   `extract_blog_sources` on it to confirm the EPA PDF gets downloaded and
   rendered to PNG.

Run: python scripts/verify_bar_and_sources.py
"""
import sys
import json
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO,
                    format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("verify")

import requests
from video_agent.script_builder import (
    _inject_bar_chart, _build_bar_data_from_facts, _is_bad_bar_data,
)
from video_agent.visual_engine.source_extractor import extract_blog_sources
from video_agent.visual_engine.infographic import render_infographic


# ─── Part 1 : bar-chart logic ────────────────────────────────────────────────

# Facts as the smoke_e2e run would have produced them (from script.json
# narration / blog_record content).
SMOKE_FACTS = [
    {"value": "90", "unit": "%",
     "claim": "Calcium nitrate reduced H2S by 90% in field trials."},
    {"value": "15", "unit": "%",
     "claim": "Chemical costs were cut by 15% across Australian utilities."},
    {"value": "50", "unit": "mg/L",
     "claim": "Calcium nitrate dosed at 50 mg/L for H2S control."},
    {"value": "24", "unit": "hours",
     "claim": "Calcium nitrate cuts H2S within 24 hours of dosing."},
]

# A scene list mirroring what Ollama produced for the lime blog: a bar chart
# with garbage labels mixing % / mg/L / hours.
BAD_SCENES = [
    {"index": 0, "visual_type": "text_card",
     "visual_spec": {"layout": "hook"}, "narration": "hook"},
    {"index": 1, "visual_type": "infographic",
     "visual_spec": {"chart_type": "bar", "data": {
         "labels": ["Calcium nitrate reduced", "Chemical costs were",
                    "Calcium nitrate cut", "Calcium nitrate cut"],
         "values": [90.0, 15.0, 50.0, 24.0],
     }}, "narration": "stats"},
    {"index": 2, "visual_type": "text_card",
     "visual_spec": {"layout": "cta"}, "narration": "cta"},
]


def verify_bar_logic():
    print("\n=== PART 1 : bar-chart logic ===")
    bad = BAD_SCENES[1]["visual_spec"]["data"]
    assert _is_bad_bar_data(bad), "expected the LLM data to be flagged bad"
    print("✓ Detected smoke_e2e LLM bar data as bad (duplicate + sentence labels)")

    new_data = _build_bar_data_from_facts(SMOKE_FACTS)
    print(f"  Rebuilt data from facts: {new_data}")
    assert new_data is not None, "expected a same-unit group to exist"
    assert len(set(new_data["labels"])) == len(new_data["labels"]), "duplicate labels"
    # The largest same-unit group is "%" (90, 15) → exactly two bars, both %.
    assert len(new_data["values"]) == 2, f"expected 2 % bars, got {len(new_data['values'])}"
    for l in new_data["labels"]:
        assert "were" not in l.lower() and "cut" not in l.lower(), (
            f"sentence-fragment leaked into label: {l!r}")
    print("✓ Rebuilt labels are deduped, same-unit, and free of verb fragments")

    fixed = _inject_bar_chart([dict(s) for s in BAD_SCENES], SMOKE_FACTS)
    bar_scene = next(s for s in fixed
                     if s["visual_spec"].get("chart_type") == "bar")
    print(f"  Final bar scene labels: {bar_scene['visual_spec']['data']['labels']}")
    assert not _is_bad_bar_data(bar_scene["visual_spec"]["data"]), (
        "post-injection data still flagged bad")
    print("✓ _inject_bar_chart overwrote the bad LLM bar")

    # Render it so the user can eyeball the new chart side-by-side.
    out = Path("output/_chart_test/fixed_bar.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    render_infographic(out, chart_type="bar", title="DELIVERING TANGIBLE RESULTS",
                       data=bar_scene["visual_spec"]["data"])
    print(f"✓ Rendered fixed chart to {out}")


# ─── Part 2 : source extraction against the real blog ───────────────────────

REAL_BLOG_URL = ("https://blog.hrsuindore.com/2026/05/"
                 "lime-neutralization-efficiency-can-in.html")


def fetch_blog_html(url: str) -> str:
    print(f"  Fetching {url}")
    r = requests.get(url, timeout=30,
                     headers={"User-Agent": "HRSU-VideoBot/1.0"})
    r.raise_for_status()
    return r.text


def verify_source_extraction():
    print("\n=== PART 2 : source extraction (real blog) ===")
    html = fetch_blog_html(REAL_BLOG_URL)
    blog_record = {
        "blog_id": "lime_neutralization_real",
        "title": "Lime Neutralization Efficiency: CaN in AMD",
        "url": REAL_BLOG_URL,
        "content_html": html,
        "region": "australia",
        "category": "mining",
    }
    cache = Path("output/_chart_test/sources_cache")
    # Clear any stale cache
    blog_cache_dir = cache / blog_record["blog_id"]
    if blog_cache_dir.exists():
        for p in blog_cache_dir.iterdir():
            p.unlink()
        blog_cache_dir.rmdir()
    sources = extract_blog_sources(blog_record, cache)
    print(f"  Found {len(sources)} source assets")
    pdf_pages = [s for s in sources if s["source_type"] == "pdf_page"]
    inline = [s for s in sources if s["source_type"] == "inline_image"]
    print(f"    inline images: {len(inline)}")
    print(f"    pdf pages   : {len(pdf_pages)}")
    for s in sources:
        print(f"      • {s['source_type']:13s} {s['source_url']}")
        print(f"          → {s['path']} (authority={s['is_authority']})")
    if pdf_pages:
        print("✓ EPA PDF (or another authority PDF) was fetched and rendered")
    else:
        print("✗ NO PDF page rendered — pipeline cannot show a research-paper "
              "image for this blog")


if __name__ == "__main__":
    verify_bar_logic()
    verify_source_extraction()
