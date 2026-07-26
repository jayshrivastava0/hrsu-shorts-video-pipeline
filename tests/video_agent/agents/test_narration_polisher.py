from unittest.mock import MagicMock
from video_agent.agents.narration_polisher import NarrationPolisher
from video_agent.ollama_client import OllamaError
from video_agent.storyboard import Storyboard, Scene, VisualConcept


def _scene(idx, narration, duration=5.0):
    return Scene(
        index=idx, beat="problem",
        duration_target_s=duration,
        narration=narration,
        on_screen_text="x",
        visual_concept=VisualConcept(subject="a", modifier="b",
                                     type="photo", mood="problem", style_hint=""),
        asset_candidates=[],
    )


def test_polisher_replaces_narrations():
    sb = Storyboard(version="2.0", blog={"region": "gulf", "category": "mining"},
                    scenes=[_scene(0, "Original 1"), _scene(1, "Original 2")])
    ollama = MagicMock()
    ollama.generate_json.return_value = [
        {"index": 0, "narration": "Polished 1"},
        {"index": 1, "narration": "Polished 2"},
    ]
    NarrationPolisher(ollama=ollama).run(sb)
    assert sb.scenes[0].narration == "Polished 1"
    assert sb.scenes[1].narration == "Polished 2"


def test_polisher_keeps_original_on_ollama_failure():
    sb = Storyboard(version="2.0", blog={"region": "gulf", "category": "mining"},
                    scenes=[_scene(0, "Original 1")])
    ollama = MagicMock()
    ollama.generate_json.side_effect = OllamaError("ollama down")
    NarrationPolisher(ollama=ollama).run(sb)
    assert sb.scenes[0].narration == "Original 1"


def test_polisher_keeps_original_when_wrong_shape():
    sb = Storyboard(version="2.0", blog={}, scenes=[_scene(0, "Original")])
    ollama = MagicMock()
    ollama.generate_json.return_value = "not a list"
    NarrationPolisher(ollama=ollama).run(sb)
    assert sb.scenes[0].narration == "Original"


def test_polisher_handles_empty_storyboard():
    sb = Storyboard(version="2.0", blog={}, scenes=[])
    ollama = MagicMock()
    NarrationPolisher(ollama=ollama).run(sb)
    # Must not call Ollama with empty payload
    ollama.generate_json.assert_not_called()
