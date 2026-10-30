"""Pixel-level quality metrics for rendered explainer videos (HRSU-VideoBench, tier 1).

Every metric works on grayscale frames (2-D float arrays, 0..255) sampled from the final mp4, so
any candidate can be graded -- including renders with no HTML to inspect. Only the content area
is measured (below the top wordmark strip, above the caption band), so changing captions never
count as visual motion. Calibrated on two Claude-vs-Gemma pairs of the same voiceover
(run-b2a193ef mining, run-241fb598 H2S): the failures they catch are sparse frames with tiny
elements, card-per-phrase cutting, long frozen holds, and the same card shown again.
"""
from __future__ import annotations

import numpy as np

CONTENT_TOP = 0.05
CONTENT_BOTTOM = 0.83
EDGE_THRESHOLD = 12.0     # gray-level step that counts as an edge pixel
GRID = (18, 12)           # content area cells (rows, cols) for coverage
CELL_OCCUPIED = 0.01      # edge-pixel share that makes a cell "used"
CUT_DIFF = 8.0            # mean gray change between samples that counts as a hard cut
STILL_DIFF = 0.25         # below this the content area is not moving
TARGET_CUTS_PER_MIN = 6.0  # about one hard change per beat plus transitions


def content(frame: np.ndarray) -> np.ndarray:
    h = frame.shape[0]
    return frame[int(h * CONTENT_TOP):int(h * CONTENT_BOTTOM)]


def edge_map(frame: np.ndarray) -> np.ndarray:
    """Boolean map of pixels whose horizontal or vertical gray step exceeds EDGE_THRESHOLD."""
    gx = np.abs(np.diff(frame, axis=1, prepend=frame[:, :1]))
    gy = np.abs(np.diff(frame, axis=0, prepend=frame[:1, :]))
    return np.maximum(gx, gy) > EDGE_THRESHOLD


def cell_map(frame: np.ndarray) -> np.ndarray:
    """GRID boolean map of content cells that carry visible content."""
    e = edge_map(content(frame))
    gy, gx = GRID
    h, w = e.shape
    e = e[: h // gy * gy, : w // gx * gx].reshape(gy, h // gy, gx, w // gx).mean(axis=(1, 3))
    return e > CELL_OCCUPIED


def coverage(frame: np.ndarray) -> float:
    """Share of the content area that carries visible content (0..1)."""
    return float(cell_map(frame).mean())


def thirds(frame: np.ndarray) -> list[float]:
    """Coverage of the top, middle and bottom thirds of the content area."""
    return [float(b.mean()) for b in np.array_split(cell_map(frame), 3, axis=0)]


def empty_third(frame: np.ndarray, floor: float = 0.02) -> bool:
    return min(thirds(frame)) < floor


def _diffs(frames: list[np.ndarray]) -> list[float]:
    return [float(np.abs(content(frames[k]) - content(frames[k - 1])).mean())
            for k in range(1, len(frames))]


def scenes(frames: list[np.ndarray]) -> list[tuple[int, int]]:
    """[start, end) sample ranges split at hard cuts."""
    out, start = [], 0
    for k, d in enumerate(_diffs(frames), start=1):
        if d > CUT_DIFF:
            out.append((start, k))
            start = k
    out.append((start, len(frames)))
    return out


def cuts_per_minute(frames: list[np.ndarray], times: list[float]) -> float:
    span = (times[-1] - times[0]) if len(times) > 1 else 0.0
    return (len(scenes(frames)) - 1) / (span / 60) if span else 0.0


def repeat_rate(frames: list[np.ndarray], max_diff: float = 3.0,
                min_coverage: float = 0.08) -> float:
    """Share of scenes whose settled picture matches an earlier scene's (a card shown again).

    A diagram that holds or keeps building inside one scene never counts; only a picture that
    comes back after a cut to something else does.
    """
    reps = []
    for a, b in scenes(frames):
        f = frames[max(a, b - 2)]  # settled end of the scene
        if coverage(f) >= min_coverage:
            reps.append(content(f))
    hits = sum(
        any(float(np.abs(reps[i] - reps[j]).mean()) < max_diff for j in range(i))
        for i in range(1, len(reps)))
    return hits / len(reps) if reps else 0.0


def frozen_rate(frames: list[np.ndarray], times: list[float], max_hold: float = 4.0) -> float:
    """Share of runtime spent in content-area holds longer than `max_hold` seconds."""
    if len(frames) < 2:
        return 0.0
    frozen, run_start = 0.0, times[0]
    diffs = _diffs(frames)
    for k in range(1, len(frames)):
        if diffs[k - 1] > STILL_DIFF or k == len(frames) - 1:
            run = times[k] - run_start
            if run > max_hold:
                frozen += run - max_hold
            run_start = times[k]
    span = times[-1] - times[0]
    return frozen / span if span else 0.0


def summarize(frames: list[np.ndarray], times: list[float]) -> dict:
    """All tier-1 metrics plus a 0..1 composite (higher is better)."""
    covs = [coverage(f) for f in frames]
    cpm = cuts_per_minute(frames, times)
    m = {
        "coverage": round(float(np.mean(covs)), 3),
        "empty_third_rate": round(float(np.mean([empty_third(f) for f in frames])), 3),
        "cuts_per_min": round(cpm, 2),
        "repeat_rate": round(repeat_rate(frames), 3),
        "frozen_rate": round(frozen_rate(frames, times), 3),
    }
    parts = {
        "coverage": min(1.0, m["coverage"] / 0.5),
        "thirds": 1 - m["empty_third_rate"],
        "pacing": min(1.0, TARGET_CUTS_PER_MIN / cpm) if cpm else 1.0,
        "no_repeat": 1 - m["repeat_rate"],
        "motion": 1 - m["frozen_rate"],
    }
    m["parts"] = {k: round(v, 3) for k, v in parts.items()}
    m["composite"] = round(float(np.mean(list(parts.values()))), 3)
    return m
