"""Deterministic narration provenance check + critic edit instructions. No LLM, no I/O.

The script critic is a paid, fuzzy judge. Before spending a call on it, every factual
sentence of the narration must be traceable to the plan: it may use the words of the
verified claims of its own step, of the claims that step BUILDS ON, of the step's defined
terms, question and opening transition. A sentence that brings in a capitalised named
entity / acronym, or mostly new content words, that none of those contain is flagged with
the verified claim it most resembles, so the writer gets "restate using claim X" instead
of a vague score. The same module turns the critic's structured `edits` into explicit
EDIT INSTRUCTIONS (quote the offending sentence, give the verified replacement wording,
forbid re-adding it) and detects a flagged sentence coming back in a later draft.
"""
from __future__ import annotations

import re

from shorts_engine import explanation
from shorts_engine.stages.facts import normalize_for_match

NOVEL_RATIO = 0.6        # share of a sentence's content words that are new ...
NOVEL_MIN_WORDS = 4      # ... and at least this many new words
MIN_SENTENCE_WORDS = 6   # shorter fragments are connectors, not assertions

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]*")
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_SENTENCE_START_PREFIX_RE = re.compile(r"^[\W_]*")
_STOP = frozenset({
    "about", "above", "after", "again", "against", "along", "also", "among", "because",
    "before", "being", "below", "between", "cannot", "could", "doing", "during", "every",
    "first", "further", "having", "their", "theirs", "there", "these", "thing", "things",
    "those", "through", "under", "until", "where", "which", "while", "whose", "within",
    "without", "would", "should", "other", "others", "rather", "since", "still",
    "therefore", "thus", "though", "together", "toward", "towards", "whether", "becomes",
    "become", "makes", "making", "means", "often", "really", "several", "something",
    "always", "makes", "which", "happens", "happen", "means", "start", "starts", "next",
    "explain", "explains", "matter", "matters", "decision", "question", "answer",
    "different", "simple", "simply", "ready", "clear", "clearly", "learn", "video",
})


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT_RE.split(text or "") if s.strip()]


def _stem(word: str) -> str:
    w = word.lower().strip("'-")
    for suf in ("ing", "es", "ed", "ly", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            w = w[:-len(suf)]
            break
    return w[:-1] if w.endswith("e") and len(w) > 3 else w


def _content_stems(text: str) -> set[str]:
    return {_stem(w) for w in _WORD_RE.findall(text or "")
            if len(w) >= 5 and w.lower() not in _STOP}


def _entities(sentence: str) -> list[str]:
    """Capitalised words that are not the first word of the sentence, and acronyms
    (2-5 capitals), as lowercase strings. Multi-word names come out word by word."""
    words = _WORD_RE.findall(_SENTENCE_START_PREFIX_RE.sub("", sentence))
    out = []
    for i, w in enumerate(words):
        if len(w) < 2:
            continue
        if re.fullmatch(r"[A-Z]{2,5}s?", w):
            out.append(w.lower())
        elif i > 0 and w[0].isupper() and not w.isupper():
            out.append(w.lower())
    return out


def _step_pool(plan: dict, pos: int, brand) -> str:
    from shorts_engine.stages import script as script_stage   # lazy: script imports us
    step = plan["steps"][pos]
    claims = explanation.supported_claims(step) + script_stage._builds_on_claims(plan, pos)
    parts = [c.get("text", "") for c in claims]
    parts += [f"{t.get('term', '')} {t.get('definition', '')}" for t in step.get("terms", [])]
    parts += [plan.get("question", ""), step.get("transition") or ""]
    parts += [brand.company, brand.domain]      # attribution cues ("HRSU's analysis ...")
    return " ".join(parts)


def _plan_pool(plan: dict, brand) -> str:
    parts = [c.get("text", "") for s in plan["steps"] for c in explanation.supported_claims(s)]
    parts += [t.get("term", "") + " " + t.get("definition", "")
              for s in plan["steps"] for t in s.get("terms", [])]
    parts += [plan.get("question", ""), brand.company, brand.domain, brand.tagline]
    parts += [d["text"] for d in brand.differentiators] + list(brand.cta_lines)
    return " ".join(parts)


def best_claim(sentence: str, plan: dict) -> dict | None:
    """The supported claim sharing the most content stems with `sentence` (None if none)."""
    sw = _content_stems(sentence)
    best, best_n = None, 0
    for s in plan["steps"]:
        for c in explanation.supported_claims(s):
            n = len(sw & _content_stems(c.get("text", "")))
            if n > best_n:
                best, best_n = c, n
    return best


def check_provenance(beats: list[dict], plan: dict, brand) -> list[dict]:
    """Findings for narration sentences with no support in the plan.

    Each finding: {"beat", "sentence", "why", "claim_id", "claim_text"} where the claim is
    the closest verified one (the wording to restate with)."""
    step_beats = {f"step_{i}": i - 1 for i in range(1, len(plan["steps"]) + 1)}
    plan_pool = _plan_pool(plan, brand)
    findings: list[dict] = []
    for b in beats:
        name = b.get("beat")
        pool = _step_pool(plan, step_beats[name], brand) if name in step_beats else plan_pool
        pool_stems = _content_stems(pool)
        pool_low = normalize_for_match(pool)
        for sent in split_sentences(b.get("narration", "")):
            why = None
            unknown = [e for e in dict.fromkeys(_entities(sent))
                       if not re.search(rf"(?<![a-z0-9]){re.escape(e)}(?![a-z0-9])", pool_low)]
            if unknown:
                why = (f"names {', '.join(repr(e) for e in unknown)} which no claim of this "
                       f"step (or the claims it builds on) mentions")
            elif len(sent.split()) >= MIN_SENTENCE_WORDS:
                stems = _content_stems(sent)
                novel = stems - pool_stems
                if (len(novel) >= NOVEL_MIN_WORDS and stems
                        and len(novel) / len(stems) >= NOVEL_RATIO):
                    why = (f"is mostly new material ({', '.join(sorted(novel)[:5])}) that no "
                           f"claim of this step states")
            if why:
                c = best_claim(sent, plan)
                findings.append({"beat": name, "sentence": sent, "why": why,
                                 "claim_id": c["id"] if c else None,
                                 "claim_text": c["text"] if c else None})
    return findings


def _fmt_edit(beat: str, sentence: str, issue: str, claim_id, claim_text) -> str:
    fix = (f"REPLACE it with wording taken from verified claim [{claim_id}]: "
           f"\"{claim_text}\"" if claim_id and claim_text
           else "REMOVE it (no verified claim supports it)")
    return (f"In {beat}, the sentence \"{sentence}\" {issue}. {fix}. "
            f"Do NOT re-add that sentence or its wording.")


def provenance_instructions(findings: list[dict]) -> list[str]:
    return [_fmt_edit(f["beat"], f["sentence"], f["why"], f["claim_id"], f["claim_text"])
            for f in findings]


def _claims_by_id(plan: dict) -> dict[str, dict]:
    return {c["id"]: c for s in plan["steps"] for c in explanation.supported_claims(s)}


def resolve_critic_edits(edits, beats: list[dict], plan: dict) -> list[dict]:
    """Critic `edits` -> verified edit records. An edit survives only if its quoted
    sentence is really in the named beat (so the writer is never told to fix a phantom),
    and its replacement text is always the PLAN's text of the cited claim, never the
    critic's own wording (an unknown claim id means 'remove')."""
    by_id = _claims_by_id(plan)
    narr = {b["beat"]: normalize_for_match(b.get("narration", "")) for b in beats}
    out = []
    for e in edits or []:
        beat, sent = e.get("beat", ""), (e.get("offending_sentence") or "").strip()
        if not sent or beat not in narr:
            continue
        key = normalize_for_match(sent).rstrip(".!? ")
        if key and key not in narr[beat]:
            continue
        c = by_id.get(e.get("replacement_claim_id"))
        out.append({"beat": beat, "sentence": sent, "issue": e.get("issue") or "is not supported",
                    "claim_id": c["id"] if c else None, "claim_text": c["text"] if c else None})
    return out


def critic_instructions(edits: list[dict]) -> list[str]:
    return [_fmt_edit(e["beat"], e["sentence"], e["issue"], e["claim_id"], e["claim_text"])
            for e in edits]


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def _words(text: str) -> list[str]:
    return [w.lower() for w in _WORD_RE.findall(text or "")]


def reappeared(forbidden: list[str], beats: list[dict]) -> list[str]:
    """Previously flagged sentences that are back (near-verbatim) in the new draft."""
    sents = [set(_words(s)) for b in beats for s in split_sentences(b.get("narration", ""))]
    back = []
    for f in forbidden:
        fw = set(_words(f))
        if fw and any(_jaccard(fw, sw) >= 0.8 for sw in sents):
            back.append(f)
    return back


TRANSITION_MAX_NOVEL = 1


def sanitize_transitions(plan: dict) -> list[dict]:
    """Planner transitions are narration guidance, but the planner sometimes smuggles a new
    claim into one ("This shift transforms procurement into a strategic advantage"), and the
    writer then narrates it. Clear every transition that brings in more than
    TRANSITION_MAX_NOVEL content words not found in its step's verified claims, the claims it
    builds on, its terms or the question (mutates `plan`; returns what was cleared)."""
    from shorts_engine.stages import script as script_stage
    cleared = []
    for pos, step in enumerate(plan.get("steps", [])):
        t = step.get("transition") or ""
        if not t.strip():
            continue
        claims = explanation.supported_claims(step) + script_stage._builds_on_claims(plan, pos)
        pool = " ".join([c.get("text", "") for c in claims]
                        + [f"{x.get('term', '')} {x.get('definition', '')}"
                           for x in step.get("terms", [])]
                        + [plan.get("question", "")])
        novel = _content_stems(t) - _content_stems(pool)
        if len(novel) > TRANSITION_MAX_NOVEL:
            cleared.append({"step_id": step.get("step_id"), "transition": t,
                            "novel": sorted(novel)})
            step["transition"] = ""
    return cleared
