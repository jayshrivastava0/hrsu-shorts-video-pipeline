import numpy as np

from videobench import metrics as M

H, W = 480, 270


def blank():
    return np.full((H, W), 20.0, dtype=np.float32)


def with_boxes(rows, value=200.0):
    """A frame with a filled box spanning each (top, bottom) fraction of the content area."""
    f = blank()
    top, bottom = int(H * M.CONTENT_TOP), int(H * M.CONTENT_BOTTOM)
    span = bottom - top
    for a, b in rows:
        y0, y1 = top + int(span * a), top + int(span * b)
        for y in range(y0, y1, 6):  # striped so the box has edges throughout
            f[y:y + 3, 20:W - 20] = value
    return f


def test_blank_frame_has_no_coverage_and_an_empty_third():
    f = blank()
    assert M.coverage(f) == 0.0
    assert M.empty_third(f)


def test_full_frame_uses_every_third():
    f = with_boxes([(0.0, 1.0)])
    assert M.coverage(f) > 0.6
    assert not M.empty_third(f)


def test_bottom_third_empty_is_flagged():
    f = with_boxes([(0.0, 0.6)])
    t = M.thirds(f)
    assert t[0] > 0.5 and t[2] == 0.0
    assert M.empty_third(f)


def test_captions_below_content_area_are_ignored():
    f = blank()
    f[int(H * 0.9):, :] = 255.0
    f[int(H * 0.9)::4, :] = 0.0
    assert M.coverage(f) == 0.0


def test_hard_cuts_split_scenes_and_set_pacing():
    a, b = with_boxes([(0.0, 0.3)]), with_boxes([(0.6, 1.0)])
    frames = [a] * 4 + [b] * 4 + [a] * 4
    times = [i * 0.5 for i in range(len(frames))]
    assert M.scenes(frames) == [(0, 4), (4, 8), (8, 12)]
    assert M.cuts_per_minute(frames, times) > 0


def test_card_shown_again_after_a_cut_is_a_repeat():
    a, b = with_boxes([(0.0, 0.5)]), with_boxes([(0.5, 1.0)])
    assert M.repeat_rate([a] * 3 + [b] * 3 + [a] * 3) > 0


def test_diagram_that_keeps_building_is_not_a_repeat():
    steps = [with_boxes([(0.0, 0.1 * k)]) for k in range(1, 10)]
    assert M.repeat_rate(steps) == 0.0


def test_long_still_hold_counts_as_frozen():
    a = with_boxes([(0.0, 1.0)])
    frames = [a] * 21  # 10 s still at 2 fps
    times = [i * 0.5 for i in range(len(frames))]
    assert M.frozen_rate(frames, times) > 0.5


def test_summarize_composite_prefers_full_steady_frames():
    good = [with_boxes([(0.0, 0.1 * k + 0.4)]) for k in range(7)]
    bad = [blank()] * 7
    times = [i * 0.5 for i in range(7)]
    assert M.summarize(good, times)["composite"] > M.summarize(bad, times)["composite"]
