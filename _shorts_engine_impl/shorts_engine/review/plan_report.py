"""Human-readable dry-run report of explanation_plan.json (question, steps, verdicts)."""
from __future__ import annotations


def format_plan(plan: dict) -> str:
    lines = [f"Question: {plan.get('question') or ''}",
             f"Status: {(plan.get('status') or '').upper()}"]
    for reason in plan.get("hold_reasons") or []:
        lines.append(f"  HELD: {reason}")
    for step in plan.get("steps") or []:
        lines.append(f"\nStep {step.get('step_id', '?')}: {step.get('claim_text', '')}")
        for c in step.get("claims") or []:
            url = (c.get("support") or {}).get("url")
            lines.append(f"  [{c.get('verdict')}] {c.get('text', '')} ({c.get('kind', '')})"
                         + (f" <- {url}" if url else ""))
    dropped = plan.get("dropped_claims") or []
    if dropped:
        lines.append("\nDROPPED claims:")
        lines += [f"  [{c.get('verdict')}] {c.get('text', '')} -- {c.get('reason', '')}"
                  for c in dropped]
    for s in plan.get("dropped_steps") or []:
        lines.append(f"DROPPED step {s.get('step_id', '?')}")
    return "\n".join(lines)
