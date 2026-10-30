"""Apply claim verdicts to a script: drop unsupported fact beats, enforce the thesis."""
from __future__ import annotations

import copy

from scheduler.briefs.research import ClaimVerdict


class ThesisUnsourced(Exception):
    """The thesis claim has no supporting source; the script must not proceed."""

    def __init__(self, thesis: str):
        self.thesis = thesis
        super().__init__(f"Thesis is not supported by any source: {thesis}")


def apply_verdicts(script: dict, verdicts: list[ClaimVerdict], thesis_claim: str) -> dict:
    """Return a copy of `script` with only supported fact beats kept.

    Fact beats are matched to verdicts by exact narration == claim. Opinion and story
    beats are never verified or dropped. Raises ThesisUnsourced if the thesis is not
    supported (a missing verdict for the thesis counts as unsupported).
    """
    by_claim = {v.claim: v for v in verdicts}

    thesis = by_claim.get(thesis_claim)
    if thesis is None or not thesis.supported:
        raise ThesisUnsourced(thesis_claim)

    out = copy.deepcopy(script)
    kept: list[dict] = []
    for beat in out.get("beats", []):
        if beat.get("kind") != "fact":
            kept.append(beat)
            continue
        verdict = by_claim.get(beat.get("narration"))
        if verdict is None or not verdict.supported:
            continue
        beat["source_ids"] = list(verdict.source_ids)
        kept.append(beat)
    out["beats"] = kept
    return out
