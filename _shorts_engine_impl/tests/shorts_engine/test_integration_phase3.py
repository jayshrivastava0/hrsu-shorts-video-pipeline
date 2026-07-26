# tests/shorts_engine/test_integration_phase3.py
"""Golden pipeline: fixture HTML -> verified video with acquisition, verify
and package running against mocked model boundaries only. Also settles the
Plan-2 Task-14 debt (that golden test was never created)."""
from __future__ import annotations
import json
import re
from pathlib import Path
import pytest
from PIL import Image
from pydub import AudioSegment
from pydub.generators import Sine

FIXTURE = Path(__file__).parent / "fixtures" / "nitrate_post.html"
URL = "https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html"

FACTS_RESPONSE = {"facts": [
    {"id": "f1", "verbatim_quote": "dosage range of 1.5 to 3 kg per cubic meter",
     "value": "1.5 to 3", "unit": "kg/m3", "claim_summary": "dosing window",
     "tags": ["spec"], "procurement_significance": 5, "citation_marker": None},
]}
# 70 words total at 1.7 w/s = 41.2s -- inside [60, 85] words / [35, 50] s.
# Fixture tuning vs. the task brief (brief Step 3 explicitly authorizes
# retuning narration word counts, never the gates):
#   - hook: brief's 10-word narration busted gate_word_budget's hook ceiling
#     of floor(4s * 1.7 w/s * 1.2) = 8 words. Retuned to 8 words while
#     keeping a comma so split_phrases still yields >=2 spans (BROLL fires).
#   - stakes: brief's 13-word narration busted the stakes ceiling of
#     floor(6s * 1.7 * 1.2) = 12 words. Retuned to 11 words.
BEATS = [
    {"beat": "hook", "narration": "Effluent nitrate creeping up, "
     "discharge limit looming again.", "fact_ids": [],
     "card_text": "Nitrate limits are tightening",
     "broll_wish": "wastewater aeration basin"},
    {"beat": "stakes", "narration": "European plants dose at 1.5 to "
     "3 kg per cubic meter.", "fact_ids": ["f1"],
     "card_text": "The dosing window that works", "broll_wish": ""},
    {"beat": "mechanism", "narration": "Calcium nitrate feeds denitrifying "
     "bacteria, converting nitrate into harmless nitrogen gas inside the "
     "treatment train without a retrofit.", "fact_ids": ["f1"],
     "card_text": "Bacteria do the removal", "broll_wish": "",
     "diagram_labels": ["Effluent in", "Dosing", "Denitrifying bacteria",
                        "Nitrogen out"]},
    {"beat": "proof", "narration": "The published dosing window of 1.5 to 3 "
     "kilograms per cubic meter comes from the cited guide.",
     "fact_ids": ["f1"], "card_text": "A proven dosing window",
     "broll_wish": ""},
    {"beat": "cta", "narration": "HRSU supplies high purity powder with batch "
     "level QC. Read the guide at hrsuindore dot com.",
     "fact_ids": ["b_purity"], "card_text": "Get the dosing guide",
     "broll_wish": ""},
]
CRITIQUE = {"actionable_score": 9, "coherence_score": 9, "hrsu_reason_score": 9,
            "revise_notes": ""}
GOOD_DESC = {"description": "A branded navy slide with clearly legible serif "
             "text describing calcium nitrate dosing for wastewater treatment, "
             "sharp typography with a gold accent underline.",
             "visible_text": "", "quality_notes": "sharp"}


def _llm_router(prompt, system, schema, **kw):
    props = schema.get("properties", {})
    if "facts" in props:
        return FACTS_RESPONSE
    if "beats" in props:
        return {"beats": BEATS}
    if "match_score" in props:   # verify shot verdict
        return {"match_score": 9, "legible": True, "issues": []}
    if "score" in props:         # sourcing judge match
        return {"score": 8, "reason": "matches", "focal_hint": "center"}
    return CRITIQUE


def _fake_synth(segments, output_path, region, voice_override=None):
    # A quiet sine tone, NOT silence: VERIFY's heuristic gate runs for REAL
    # against the final mux and enforces VERIFY_AUDIO_RMS_FLOOR (250 linear
    # PCM RMS) -- the brief's AudioSegment.silent() fixture reads as
    # "effectively silent" and would fail the run at the verify stage.
    # -14 dBFS sine: RMS ~4600 (>250), peak ~6500 (<32500 ceiling).
    ms = int(len(segments[0].text.split()) / 1.7 * 1000)
    Sine(440).to_audio_segment(duration=max(ms, 300), volume=-14.0).export(
        str(output_path), format="mp3", bitrate="128k")
    return {"audio_path": Path(output_path), "duration_s": ms / 1000,
            "voice_used": "test", "engine_used": "fake", "fell_back": False}


def _fake_transcribe(audio_path, narration_hint=None, multilingual=False):
    words = (narration_hint or "x").split()
    step = 1 / 1.7
    return [{"word": w, "start": round(i * step, 3),
             "end": round(i * step + step * 0.85, 3)}
            for i, w in enumerate(words)]


@pytest.mark.slow
class TestGoldenPipelinePhase3:
    def test_fixture_to_verified_video(self, tmp_path, monkeypatch):
        import shorts_engine.stages.facts as facts_stage
        import shorts_engine.stages.script as script_stage
        from shorts_engine.stages import audio as audio_stage
        from shorts_engine.stages import visuals as visuals_stage
        from shorts_engine.stages import verify as verify_stage
        from shorts_engine.llm import vision_judge

        monkeypatch.setattr(facts_stage.text_llm, "generate_schema_json", _llm_router)
        monkeypatch.setattr(script_stage.text_llm, "generate_schema_json", _llm_router)
        monkeypatch.setattr(verify_stage.text_llm, "generate_schema_json", _llm_router)
        monkeypatch.setattr(audio_stage, "_synthesize", _fake_synth)
        monkeypatch.setattr(audio_stage, "_transcribe", _fake_transcribe)
        monkeypatch.setattr(vision_judge, "_describe_call", lambda *a, **k: GOOD_DESC)
        monkeypatch.setattr(verify_stage, "_describe", lambda p: GOOD_DESC)

        broll_img = tmp_path / "acq.png"
        Image.new("RGB", (1600, 900), (70, 70, 70)).save(broll_img)
        monkeypatch.setattr(visuals_stage, "_acquire", lambda **kw: {
            "image_path": str(broll_img), "focal_hint": "center",
            "provenance": {"tiers": [{"tier": "own"}], "reason": None}})

        from shorts_engine import runner, config
        from shorts_engine.cli import build_stages

        html = FIXTURE.read_text(encoding="utf-8")
        manifest = runner.run(URL, build_stages(), workspace_root=tmp_path,
                              until="verified",
                              flags={"html_override": html})
        assert manifest.status == "verified"
        ws = Path(manifest.workspace)

        # never-unverified survived
        script_doc = json.loads((ws / "script.json").read_text(encoding="utf-8"))
        for b in script_doc["beats"]:
            for tok in re.findall(r"\d[\d,]*(?:\.\d+)?", b["narration"]):
                assert tok in {"1.5", "3"}, f"untraced numeric {tok}"

        # duration law on the FINAL (post-verify) video
        from shorts_engine.cards import encoder
        voice = encoder.probe_duration(ws / "voiceover.mp3")
        video = encoder.probe_duration(ws / "video_short.mp4")
        assert video >= voice + config.AUDIO_COMPLETENESS_MARGIN_S
        assert abs(video - (voice + config.END_CARD_HOLD_S)) <= 0.35

        # acquisition actually happened: hook shot 1 rendered as real BROLL
        vis = json.loads((ws / "visuals_report.json").read_text(encoding="utf-8"))
        rendered = {s["id"]: s for s in vis["shots"]}
        assert any(s["rendered_type"] == "BROLL"
                   and s["provenance"]["resolved"] == "acquired"
                   for s in vis["shots"])
        assert all(s["content_pixels"] >= config.MIN_CONTENT_PIXELS
                   for s in vis["shots"])

        # verify artifacts
        vrep = json.loads((ws / "verify_report.json").read_text(encoding="utf-8"))
        assert vrep["final"]["failures"] == []
        assert (ws / "contact_sheet.html").exists()
