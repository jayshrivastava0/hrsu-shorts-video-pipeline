from video_agent.sources.scoring import score_candidate
from video_agent.sources.base import RawCandidate


def test_high_quality_photo_scores_above_threshold():
    cand = RawCandidate(
        source="unsplash", url="https://x/img.jpg",
        caption="acid mine drainage runoff in stream",
        width=1920, height=1080, file_size=120_000,
    )
    score = score_candidate(cand, query="acid mine drainage")
    assert score >= 60          # res(30) + aspect(10) + tokens(25) + auth(10)


def test_low_resolution_rejected():
    cand = RawCandidate(source="g", url="u", caption="x",
                        width=400, height=300, file_size=20_000)
    score = score_candidate(cand, query="x")
    assert score < 0            # hard rejected


def test_context_match_score_uses_thread_keywords():
    """Caption that overlaps the cumulative thread (not just narration) gets
    boosted; off-thread caption stays low."""
    from video_agent.sources.scoring import context_match_score
    narration = "calcium ions raise the water pH"
    thread = ["pipe scaling", "equipment", "calcium ions", "industrial water"]
    hero = "calcium nitrate cuts H2S in wastewater by 40%"
    on_thread = context_match_score(
        "industrial pipe scaling buildup", narration,
        thread_keywords=thread, hero_claim=hero,
    )
    off_thread = context_match_score(
        "algae bloom on river surface", narration,
        thread_keywords=thread, hero_claim=hero,
    )
    assert on_thread > off_thread
    assert on_thread >= 30, f"expected >= 30, got {on_thread}"


def test_context_match_score_backcompat_two_args():
    """Existing two-arg signature still works for callers not yet migrated."""
    from video_agent.sources.scoring import context_match_score
    score = context_match_score("wastewater treatment plant",
                                "wastewater treatment for industrial use")
    assert score > 0


def test_token_overlap_zero_when_unrelated():
    cand = RawCandidate(source="g", url="u",
                        caption="cat playing piano",
                        width=1920, height=1080, file_size=80_000)
    score = score_candidate(cand, query="industrial wastewater")
    assert score < 55           # no token overlap → no +25 (base+dimension ≤ 50)


def test_below_min_long_edge_hard_rejects():
    cand = RawCandidate(source="unsplash", url="u", caption="x",
                        width=800, height=600, file_size=80_000)
    score = score_candidate(cand, query="x")
    assert score < 0


def test_square_aspect_penalised():
    sq = RawCandidate(source="unsplash", url="u", caption="industrial water",
                      width=1500, height=1500, file_size=120_000)
    wide = RawCandidate(source="unsplash", url="u", caption="industrial water",
                        width=1920, height=1080, file_size=120_000)
    assert score_candidate(sq, "industrial water") < score_candidate(wide, "industrial water")


def test_landscape_widescreen_gets_bonus():
    wide = RawCandidate(source="unsplash", url="u", caption="industrial",
                        width=2560, height=1440, file_size=120_000)
    score = score_candidate(wide, "industrial")
    # 1.78 aspect ≥ 1.6 → +10; long edge ≥ 1920 → +10
    assert score >= 60


def test_portrait_orientation_gets_bonus():
    tall = RawCandidate(source="unsplash", url="u", caption="oil rig tower",
                        width=1280, height=2280, file_size=120_000)
    score = score_candidate(tall, "oil rig")
    assert score >= 50    # gets the portrait bonus
