"""Human-readable dry-run report of explanation_plan.json (question, steps with the
earlier claims each builds on and its transition, verdicts, drops)."""
from __future__ import annotations


def _role(c: dict) -> str:
    """"[mechanism] " etc., or "" for a claim with no role (pre-role plans)."""
    return f"[{c['role']}] " if c.get("role") else ""


def format_plan(plan: dict) -> str:
    lines = [f"Question: {plan.get('question') or ''}",
             f"Status: {(plan.get('status') or '').upper()}"]
    for reason in plan.get("hold_reasons") or []:
        lines.append(f"  HELD: {reason}")
    for step in plan.get("steps") or []:
        lines.append(f"\nStep {step.get('step_id', '?')}: {step.get('claim_text', '')}")
        deps = step.get("depends_on")
        if isinstance(deps, list):       # absent in plans stored before depends_on existed
            lines.append("  builds on: " + (", ".join(str(d) for d in deps) or "(the question)"))
        if (step.get("transition") or "").strip():
            lines.append(f"  transition: {step['transition'].strip()}")
        for c in step.get("claims") or []:
            url = (c.get("support") or {}).get("url")
            lines.append(f"  {_role(c)}[{c.get('verdict')}] {c.get('text', '')} "
                         f"({c.get('kind', '')})" + (f" <- {url}" if url else ""))
    dropped = plan.get("dropped_claims") or []
    if dropped:
        lines.append("\nDROPPED claims:")
        lines += [f"  {_role(c)}[{c.get('verdict')}] {c.get('text', '')} -- "
                  f"{c.get('reason', '')}" for c in dropped]
    for s in plan.get("dropped_steps") or []:
        reason = s.get("reason")
        lines.append(f"DROPPED step {s.get('step_id', '?')}"
                     + (f" -- {reason}" if reason else ""))
    return "\n".join(lines)
