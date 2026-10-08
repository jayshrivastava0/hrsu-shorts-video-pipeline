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
