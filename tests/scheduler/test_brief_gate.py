"""Type gate for brief-driven videos: banned claims, number tracing, in-progress wording."""
import json

from scheduler.briefs.facts import Facts
from scheduler.briefs.gate import numbers_in, on_screen_text, run_type_gate
from scheduler.briefs.model import Brief
from scheduler.briefs.types import get_recipe


def _make_project(tmp_path, pages: dict[str, str], voice=(10.0, 25.0)):
    run = tmp_path / "run"
    proj = run / "videos" / "explainer"
    (proj / "renders").mkdir(parents=True)
    (proj / "compositions" / "frames").mkdir(parents=True)
    (run / "run_manifest.json").write_text(json.dumps({"status": "audio"}))
    (run / "script.json").write_text("{}")
    (run / "references.json").write_text("{}")
    (proj / "audio_meta.json").write_text(json.dumps({"voices": [{"duration_s": d} for d in voice]}))
    (proj / "renders" / "video.mp4").write_bytes(b"x")
    (proj / "DIRECTOR_DONE.json").write_text(json.dumps({"ok": True, "video": "renders/video.mp4"}))
    for name, html in pages.items():
        (proj / "compositions" / "frames" / name).write_text(html, encoding="utf-8")
    return run, proj


def _brief(type_name="explainer"):
    return Brief(id="b-1", text="Brief text", type=type_name)


def _facts(banned=None, in_progress=None):
    return Facts(banned_claims=list(banned or []), in_progress=list(in_progress or []))


def _gate(run, proj, type_name, facts, allowed_text="", lint=(0, ""), dur=35.0):
    return run_type_gate(_brief(type_name), get_recipe(type_name), facts, run, proj,
                         allowed_text=allowed_text,
                         ffprobe=lambda p: dur, lint=lambda p: lint)


def _page(body: str) -> str:
    return f"<html><body><div class=\"clip\">{body}</div></body></html>"


def test_banned_phrase_on_screen_fails(tmp_path):
    run, proj = _make_project(tmp_path, {"01-x.html": _page("<h1>Certified calcium nitrate</h1>")})
    r = _gate(run, proj, "explainer", _facts(banned=["certified"]))
    assert not r.ok
    assert any("certified" in x for x in r.reasons)


def test_number_not_in_allowed_text_fails(tmp_path):
    run, proj = _make_project(tmp_path, {"01-x.html": _page("<p>Purity 99.9%</p>")})
    r = _gate(run, proj, "explainer", _facts(), allowed_text="Purity 99.8% per the CoA.")
    assert not r.ok
    assert any("99.9" in x for x in r.reasons)


def test_number_in_coa_text_passes(tmp_path):
    run, proj = _make_project(tmp_path, {"01-x.html": _page("<p>Purity 99.8%</p>")})
    r = _gate(run, proj, "explainer", _facts(), allowed_text="Purity 99.8% per the CoA.")
    assert r.ok, r.reasons


def test_marketing_checks_only_banned_and_in_progress(tmp_path):
    # facts_only recipe: no number tracing, so an untraced number passes.
    run, proj = _make_project(tmp_path, {"01-x.html": _page("<p>Save 99.9% on freight</p>")})
    r = _gate(run, proj, "marketing", _facts(), allowed_text="")
    assert r.ok, r.reasons


def test_in_progress_claim_without_planned_fails(tmp_path):
    run, proj = _make_project(tmp_path, {"01-x.html": _page("<p>Steam reuse in all boilers</p>")})
    r = _gate(run, proj, "explainer", _facts(), allowed_text="")
    assert not r.ok
    assert any("all boilers" in x for x in r.reasons)


def test_in_progress_line_with_planned_passes(tmp_path):
    run, proj = _make_project(tmp_path, {"01-x.html": _page("<p>Steam reuse in all boilers is planned</p>")})
    r = _gate(run, proj, "explainer", _facts(), allowed_text="")
    assert r.ok, r.reasons


def test_base_gate_failure_propagates(tmp_path):
    run, proj = _make_project(tmp_path, {"01-x.html": _page("<p>Plain text 1.5</p>")})
    r = _gate(run, proj, "explainer", _facts(), allowed_text="1.5", lint=(2, "boom"))
    assert not r.ok
    assert any("lint" in x for x in r.reasons)
    # type checks still run alongside the base failure
    run2, proj2 = _make_project(tmp_path / "second", {"01-x.html": _page("<p>Certified</p>")})
    r2 = _gate(run2, proj2, "explainer", _facts(banned=["certified"]), lint=(2, "boom"))
    assert any("lint" in x for x in r2.reasons)
    assert any("certified" in x for x in r2.reasons)
    assert r2.video_path == proj2 / "renders" / "video.mp4"
    assert r2.duration_s == 35.0


def test_numbering_integers_ignored():
    assert numbers_in("Step 1 then 2, 3 and 12 in list") == set()
    assert numbers_in("Reach 13 bars") == {"13"}
    assert numbers_in("H2S and CO2") == set()


def test_thousands_commas_normalised():
    assert numbers_in("Output 1,000 tonnes and 12,345.5 kg") == {"1000", "12345.5"}
    assert numbers_in("99.8%") == {"99.8"}


def test_csr_on_screen_fails(tmp_path):
    run, proj = _make_project(tmp_path, {"01-x.html": _page("<p>Our CSR programme</p>")})
    r = _gate(run, proj, "explainer", _facts(), allowed_text="")
    assert not r.ok
    assert any("csr" in x.lower() for x in r.reasons)


def test_numbers_in_script_tags_ignored(tmp_path):
    html = ("<html><head><style>.a { width: 77.7%; }</style></head><body>"
            "<script>var n = 555.5; var q = 1,234;</script>"
            "<p>Plain words</p></body></html>")
    run, proj = _make_project(tmp_path, {"01-x.html": html})
    text = on_screen_text(proj)
    assert "555" not in text and "77.7" not in text and "1,234" not in text
    assert numbers_in(text) == set()
    r = _gate(run, proj, "explainer", _facts(), allowed_text="")
    assert r.ok, r.reasons


def _narration_script(run, narration: list[str]):
    beats = [{"beat": f"b{i}", "narration": n, "kind": "fact", "card_text": "", "broll_wish": ""}
             for i, n in enumerate(narration, 1)]
    (run / "script.json").write_text(json.dumps({"beats": beats}), encoding="utf-8")


def test_proof_narration_number_not_in_documents_fails(tmp_path):
    run, proj = _make_project(tmp_path, {"01-x.html": _page("<p>Purity 99.8%</p>")})
    _narration_script(run, ["The product reaches 99.9% purity."])
    r = _gate(run, proj, "proof", _facts(), allowed_text="Purity 99.8% per the CoA.")
    assert not r.ok
    assert any("99.9" in x and "narration" in x for x in r.reasons)


def test_explainer_narration_numbers_still_allowed(tmp_path):
    run, proj = _make_project(tmp_path, {"01-x.html": _page("<p>Purity 99.9%</p>")})
    _narration_script(run, ["The product reaches 99.9% purity."])
    r = _gate(run, proj, "explainer", _facts(), allowed_text="The product reaches 99.9% purity.")
    assert r.ok, r.reasons
