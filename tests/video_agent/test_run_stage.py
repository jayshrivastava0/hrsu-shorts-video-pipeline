from unittest.mock import patch
from pathlib import Path
from video_agent.storyboard import (
    save_storyboard, load_storyboard, Storyboard, HeroClaim, Beat,
)
from video_agent.run_stage import replay_stage


def test_replay_storyboarder_only(tmp_path):
    sb = Storyboard(version="2.0",
                    blog={"id": "b", "url": "u", "title": "t",
                          "region": "australia", "category": "mining",
                          "persona": "procurement"},
                    hero_claim=HeroClaim(stat="90%", claim_text="x"),
                    arc=[Beat(index=0, beat="hook", purpose="",
                              duration_target_s=4.0)])
    path = tmp_path / "storyboard.json"
    save_storyboard(sb, path)
    with patch("video_agent.run_stage.Storyboarder") as M:
        M.return_value.run.side_effect = lambda s: s
        replay_stage("storyboarder", path)
    M.return_value.run.assert_called_once()
