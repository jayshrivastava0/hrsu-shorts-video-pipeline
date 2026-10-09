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
# Planner output for the new explain -> verify_claims -> script flow. Every
# blog_stated quote is a real passage of the fixture's canonical.txt (numbers
# only where the quote carries the same number+unit), so VERIFY supports them
# with no web access; the reasoning claim follows from them.
PLAN_RAW = {
    "question": "Why do EU plants dose calcium nitrate to cut nitrate discharge?",
    "steps": [
        {"step_id": "s1", "claim_text": "There is an established dosing window.",
         "claims": [{"id": "c1", "kind": "blog_stated",
                     "quote": "dosage range of 1.5 to 3 kg per cubic meter of wastewater volume",
                     "text": "Best practice uses a dosage range of 1.5 to 3 kg per cubic "
                             "meter of wastewater."}],
         "terms": [],
         "visual_intent": {"entities": ["Effluent in", "Dosing"],
                           "relationship": "dosed into", "quantity": "dose"}},
        {"step_id": "s2", "claim_text": "Bacteria turn nitrate into nitrogen gas.",
         "claims": [{"id": "c2", "kind": "blog_stated",
                     "quote": "This bacteria utilizes nitrate as an electron acceptor, "
                              "converting it into harmless nitrogen gas",
                     "text": "Denitrifying bacteria use nitrate as an electron acceptor "
                             "and convert it into harmless nitrogen gas."}],
         "terms": [],
         "visual_intent": {"entities": ["Nitrate", "Denitrifying bacteria", "Nitrogen out"],
                           "relationship": "converted by", "quantity": "nitrate level"}},
        {"step_id": "s3", "claim_text": "The dose must be tuned per site.",
         "claims": [{"id": "c3", "kind": "blog_stated",
                     "quote": "The amount of chemicals required for treatment depends on "
                              "the pH and alkalinity of the wastewater",
                     "text": "The amount needed depends on the pH and alkalinity of the "
                             "wastewater."},
                    {"id": "c4", "kind": "reasoning", "quote": "",
                     "text": "So the dose has to be tuned to each site."}],
         "terms": [],
         "visual_intent": {"entities": ["pH", "Alkalinity", "Dose"],
                           "relationship": "sets", "quantity": "dose"}},
    ],
    "payoff": {"takeaway": "Tune a consistent calcium nitrate dose to your wastewater.",
               "differentiator_id": "b_purity"},
}
# Writer output (script.SCRIPT_SCHEMA). The hook keeps a comma so split_phrases
# yields >=2 spans and its broll_wish drives real BROLL acquisition.
SCRIPT_DOC = {
    "hook": {"narration": "Effluent nitrate creeping up, discharge limit looming again.",
             "card_text": "Nitrate limits are tightening",
             "broll_wish": "wastewater aeration basin"},
    "steps": [
        {"step_id": "s1", "narration": "European plants dose calcium nitrate at 1.5 to 3 kg "
                                       "per cubic meter of wastewater.",
         "card_text": "The dosing window that works", "broll_wish": ""},
        {"step_id": "s2", "narration": "That dose feeds denitrifying bacteria, which use "
                                       "nitrate as an electron acceptor and convert it "
                                       "into harmless nitrogen gas.",
         "card_text": "Bacteria do the removal", "broll_wish": ""},
        {"step_id": "s3", "narration": "How much you need depends on the pH and alkalinity "
                                       "of the wastewater, so the dose is tuned to each "
                                       "site.",
         "card_text": "Tune the dose per site", "broll_wish": ""},
    ],
    "cta": {"narration": "HRSU supplies consistent high purity calcium nitrate powder with "
                         "batch level QC. Read the guide at hrsuindore dot com.",
            "card_text": "Get the dosing guide", "broll_wish": ""},
}
CRITIQUE = {"actionable_score": 9, "coherence_score": 9, "hrsu_reason_score": 9,
            "faithfulness_score": 9, "revise_notes": ""}
GOOD_DESC = {"description": "A branded navy slide with clearly legible serif "
             "text describing calcium nitrate dosing for wastewater treatment, "
             "sharp typography with a gold accent underline.",
             "visible_text": "", "quality_notes": "sharp"}


def _llm_router(prompt, system, schema, **kw):
    from shorts_engine import explanation
    from shorts_engine.stages import script as script_stage
    from shorts_engine.stages import verify_claims as vc_stage
    # explain / verify_claims / script calls: answered by schema identity
    if schema is explanation.PLAN_SCHEMA:
        return json.loads(json.dumps(PLAN_RAW))
    if schema is vc_stage.VERDICT_SCHEMA:
        return {"verdict": "supported", "passage_index": 0}
    if schema is vc_stage.REPAIR_SCHEMA:
        return {"claims": []}
    if schema is script_stage.SCRIPT_SCHEMA:
        return json.loads(json.dumps(SCRIPT_DOC))
    if schema is script_stage.CRITIQUE_SCHEMA:
        return CRITIQUE
    props = schema.get("properties", {})
    if "facts" in props:
        return FACTS_RESPONSE
    if "match_score" in props:   # verify shot verdict
        return {"match_score": 9, "legible": True, "issues": []}
    if "score" in props:         # sourcing judge match
        return {"score": 8, "reason": "matches", "focal_hint": "center"}
    raise AssertionError(f"unexpected schema {schema}")


class _EmptyRetriever:
    def retrieve(self, claim_text):
        return []


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
        from shorts_engine.stages import explain as explain_stage
        from shorts_engine.stages import verify_claims as vc_stage
        from shorts_engine.stages import audio as audio_stage
        from shorts_engine.stages import visuals as visuals_stage
        from shorts_engine.stages import verify as verify_stage
        from shorts_engine.llm import vision_judge

        monkeypatch.setattr(facts_stage.text_llm, "generate_schema_json", _llm_router)
        monkeypatch.setattr(script_stage.text_llm, "generate_schema_json", _llm_router)
        monkeypatch.setattr(verify_stage.text_llm, "generate_schema_json", _llm_router)
        monkeypatch.setattr(vc_stage.text_llm, "generate_schema_json", _llm_router)
        monkeypatch.setattr(vc_stage, "Retriever", lambda urls: _EmptyRetriever())
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

        from shorts_engine.stages import ingest as ingest_stage
        # facts -> explain -> verify_claims -> script driven by the stage modules
        # themselves; every later stage comes from build_stages() unchanged.
        head = [("ingest", "ingested", ingest_stage.run),
                ("facts", "facts", facts_stage.run),
                ("explain", "explained", explain_stage.run),
                ("verify_claims", "claims_verified", vc_stage.run),
                ("script", "scripted", script_stage.run)]
        head_names = {name for name, _, _ in head}
        stages = head + [s for s in build_stages() if s[0] not in head_names]

        html = FIXTURE.read_text(encoding="utf-8")
        manifest = runner.run(URL, stages, workspace_root=tmp_path,
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
