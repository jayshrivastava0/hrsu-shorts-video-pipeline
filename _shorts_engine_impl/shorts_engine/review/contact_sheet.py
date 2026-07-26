"""Human-review contact sheet: everything needed to approve a video on one
offline HTML page (spec Stage 8 hold_for_review)."""
from __future__ import annotations

import html
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def build(ctx, verify_report: dict) -> Path:
    ws = Path(ctx.workspace)
    script = json.loads((ws / "script.json").read_text(encoding="utf-8"))
    facts = json.loads((ws / "factsheet.json").read_text(encoding="utf-8"))
    vis = json.loads((ws / "visuals_report.json").read_text(encoding="utf-8"))
    assemble = json.loads((ws / "assemble_report.json").read_text(encoding="utf-8"))
    verdicts = {s["id"]: s for s in verify_report["final"]["shots"]}

    rows = []
    for s in vis["shots"]:
        sid = s["id"]
        v = verdicts.get(sid, {})
        frame_rel = f"verify/frame_{sid}.png"
        prov = s.get("provenance", {})
        rows.append(f"""
<tr><td><img src="{frame_rel}" width="180"></td>
<td><b>{sid}</b> · {html.escape(s['rendered_type'])}<br>
resolved: {html.escape(str(prov.get('resolved')))}
{('· ' + html.escape(str(prov.get('reason')))) if prov.get('reason') else ''}<br>
match {v.get('match_score', '—')}/10 · legible: {v.get('legible', '—')}<br>
{html.escape('; '.join(v.get('issues', [])))}</td></tr>""")

    beats_html = "".join(
        f"<li><b>{html.escape(b['beat'])}</b>: {html.escape(b['narration'])}"
        f"<br><i>card: {html.escape(b.get('card_text', ''))}</i></li>"
        for b in script["beats"])
    facts_html = "".join(
        f"<li>[{html.escape(str(f['id']))}] “{html.escape(f['verbatim_quote'])}”"
        f" (= {html.escape(str(f['value']))} {html.escape(str(f.get('unit', '')))})</li>"
        for f in facts.get("facts", []))

    doc = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Contact sheet — {html.escape(ctx.manifest.slug if hasattr(ctx, 'manifest') else str(ws.name))}</title>
<style>body{{font-family:Segoe UI,Arial,sans-serif;background:#0a192f;color:#ccd6f6;
margin:24px}} a{{color:#d4af37}} td{{padding:8px;border-bottom:1px solid #233}}
img{{border:1px solid #345}}</style></head><body>
<h1>Review: <a href="video_short.mp4">video_short.mp4</a></h1>
<p>voice {assemble['voice_total_s']}s · video {assemble['video_duration_s']}s ·
music: {assemble['music_used']} · verify cycles: {verify_report['cycles']} ·
fixes: {html.escape('; '.join(verify_report['fixes_applied']) or 'none')}</p>
<p><b>Hook check (human):</b> would a procurement manager stop scrolling for
beat 1? If not, note it — hook strength is a tracked tuning item.</p>
<h2>Shots</h2><table>{''.join(rows)}</table>
<h2>Script</h2><ol>{beats_html}</ol>
<h2>Facts (verbatim)</h2><ul>{facts_html}</ul>
</body></html>"""
    out = ws / "contact_sheet.html"
    out.write_text(doc, encoding="utf-8")
    return out
