import pytest

from scheduler.briefs.claims import ThesisUnsourced, apply_verdicts
from scheduler.briefs.research import ClaimVerdict, Source, gather_sources, publisher_of, verify_claims


def _hit(url, title="T", snippet="S"):
    return {"url": url, "title": title, "snippet": snippet}


def test_gather_sources_dedupes_by_url():
    hits = [
        _hit("https://www.epa.gov/a", "A"),
        _hit("https://www.epa.gov/a", "A again"),
        _hit("https://example.org/b", "B"),
    ]
    sources = gather_sources("brief", search=lambda q: hits, limit=12)
    assert [s.url for s in sources] == ["https://www.epa.gov/a", "https://example.org/b"]
    assert sources[0].publisher == "epa.gov"
    assert sources[1].publisher == "example.org"


def test_publisher_is_registrable_domain():
    assert publisher_of("https://www.gov.uk/x") == "gov.uk"
    assert publisher_of("https://news.bbc.co.uk/x") == "bbc.co.uk"


def test_unsupported_claim_beat_dropped():
    script = {"beats": [
        {"kind": "fact", "narration": "Claim one."},
        {"kind": "fact", "narration": "Claim two."},
    ]}
    verdicts = [
        ClaimVerdict("Claim one.", True, ["S1"]),
        ClaimVerdict("Claim two.", False, []),
    ]
    out = apply_verdicts(script, verdicts, thesis_claim="Claim one.")
    assert [b["narration"] for b in out["beats"]] == ["Claim one."]


def test_supported_claims_keep_source_ids():
    script = {"beats": [{"kind": "fact", "narration": "Claim one."}]}
    verdicts = [ClaimVerdict("Claim one.", True, ["S1", "S3"])]
    out = apply_verdicts(script, verdicts, thesis_claim="Claim one.")
    beat = out["beats"][0]
    assert beat["narration"] == "Claim one."
    assert beat["source_ids"] == ["S1", "S3"]


def test_unsupported_thesis_raises_before_any_director_call():
    script = {"beats": [{"kind": "fact", "narration": "Thesis."}]}
    verdicts = [ClaimVerdict("Thesis.", False, [])]
    with pytest.raises(ThesisUnsourced) as exc:
        apply_verdicts(script, verdicts, thesis_claim="Thesis.")
    assert "Thesis." in str(exc.value)


def test_verify_claims_calls_llm_once_per_claim_and_maps_verdicts():
    sources = [Source("S1", "T", "https://epa.gov/x", "snip", "epa.gov")]
    calls = []

    def fake_llm(prompt):
        calls.append(prompt)
        return {"supported": "rain" in prompt, "source_ids": ["S1"] if "rain" in prompt else []}

    verdicts = verify_claims(["rain falls", "pigs fly"], sources, fake_llm)
    assert len(calls) == 2
    assert verdicts[0] == ClaimVerdict("rain falls", True, ["S1"])
    assert verdicts[1] == ClaimVerdict("pigs fly", False, [])


def test_opinion_beats_marked_hrsu_view_are_not_verified_or_dropped():
    script = {"beats": [
        {"kind": "opinion", "narration": "We think this is the best option."},
        {"kind": "story", "narration": "A plant manager told us about it."},
        {"kind": "fact", "narration": "Claim one."},
    ]}
    verdicts = [ClaimVerdict("Claim one.", True, ["S1"])]
    out = apply_verdicts(script, verdicts, thesis_claim="Claim one.")
    opinion = out["beats"][0]
    assert opinion["narration"] == "We think this is the best option."
    assert "source_ids" not in opinion
    assert out["beats"][1]["narration"] == "A plant manager told us about it."
    assert len(out["beats"]) == 3
