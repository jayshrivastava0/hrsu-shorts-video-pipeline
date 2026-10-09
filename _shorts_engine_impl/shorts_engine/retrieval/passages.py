"""Deterministic passage selection: sentences of a page that overlap a claim."""
from __future__ import annotations

import re

from shorts_engine.stages.facts import normalize_for_match, split_sentences

_STOP = frozenset("a an the of in on at to for and or is are was were be been it its this that "
                  "with by as from can may will than then so such not no".split())
_WORD_RE = re.compile(r"[a-z0-9]+")


def _content_words(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(normalize_for_match(text)) if w not in _STOP and len(w) > 2}


def best_passages(text: str, claim: str, k: int) -> list[str]:
    """Top-k sentences by content-word overlap with `claim` (needs >=2 shared words,
    or >=1 when the claim has fewer than 3 content words). Ties keep page order."""
    want = _content_words(claim)
    if not want:
        return []
    need = 1 if len(want) < 3 else 2
    scored = []
    for i, sentence in enumerate(split_sentences(text)):
        overlap = len(want & _content_words(sentence))
        if overlap >= need:
            scored.append((-overlap, i, sentence))
    scored.sort()
    return [s for _o, _i, s in scored[:k]]
