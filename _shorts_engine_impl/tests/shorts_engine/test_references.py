"""Claim -> authoritative-source attribution (references.py). Pure logic, no network."""
import json

from shorts_engine import references as R

CANON = (
    "Intro text about sewers. Hydrogen sulfide corrodes concrete pipes in sewers1. "
    "Nitrate-reducing bacteria outcompete sulfate reducers when calcium nitrate2 is dosed. "
    "The dose is 20 mg/L to 50 mg/L in the stream3. "
    "A widely repeated aside has no citation attached. "
    "Dosing follows WHO guidance (see annex)4 for safety. "
    "Total sulfide is measured as H2S in the stream."
)

POST = {
    "url": "https://hrsuindore.com/blog/x/",
    "citations": [
        {"marker": 1, "url": "https://www.epa.gov/wastewater/h2s", "kind": "standard", "title": "EPA H2S Control"},
        {"marker": 2, "url": "https://echa.europa.eu/substance-information/-/substanceinfo/1", "kind": "standard", "title": ""},
        {"marker": 3, "url": "https://en.wikipedia.org/wiki/Nitrate", "kind": "web", "title": "Nitrate"},
        {"marker": 4, "url": "https://www.who.int/publications/x", "kind": "web", "title": "WHO guidance"},
    ],
}


def claim(cid, kind="blog_stated", role="mechanism", quote="", url=None, text=None, verdict="supported"):
    stype = "blog_quote" if kind == "blog_stated" else ("source" if kind == "external_fact" else None)
    return {"id": cid, "text": text or quote or cid, "kind": kind, "role": role,
            "support": {"type": stype, "quote": quote, "url": url}, "verdict": verdict,
            "needs_number": any(ch.isdigit() for ch in (text or quote))}


def plan_of(*steps):
    return {"question": "How does it work?", "steps": [
        {"step_id": f"s{i+1}", "claims": cl, "depends_on": dep} for i, (cl, dep) in enumerate(steps)]}


Q1 = "Hydrogen sulfide corrodes concrete pipes in sewers."
Q2 = "Nitrate-reducing bacteria outcompete sulfate reducers when calcium nitrate is dosed."
Q3 = "The dose is 20 mg/L to 50 mg/L in the stream."
QU = "A widely repeated aside has no citation attached."


class TestMarkerMapping:
    def test_marker_before_period_maps_to_citation(self):
        refs = R.build_references(plan_of(([claim("m1", quote=Q1)], [])), POST, CANON)
        c = refs["claims"]["m1"]
        assert c["tier"] == "authoritative" and c["chip"] == "US EPA"
        src = refs["sources"][0]
        assert src["url"].startswith("https://www.epa.gov") and src["claim_ids"] == ["m1"]
        assert src["title"] == "EPA H2S Control" and src["kind"] == "standard"
        assert src["id"] == "s1" and c["sources"] == ["s1"]

    def test_mid_sentence_marker_inside_quote(self):
        refs = R.build_references(plan_of(([claim("m2", quote=Q2)], [])), POST, CANON)
        assert refs["claims"]["m2"]["chip"] == "ECHA"

    def test_numeric_text_is_not_mistaken_for_marker(self):
        # "H2S" and "20 mg/L" must not read as markers; this sentence's only marker is 3
        refs = R.build_references(plan_of(([claim("m3", quote=Q3)], [])), POST, CANON)
        assert [s["url"] for s in refs["sources"]] == ["https://en.wikipedia.org/wiki/Nitrate"]

    def test_uncited_quote_is_blog_tier(self):
        refs = R.build_references(plan_of(([claim("m4", quote=QU)], [])), POST, CANON)
        assert refs["claims"]["m4"] == {"sources": [], "tier": "blog", "chip": None}

    def test_marker_after_paren(self):
        q = "Dosing follows WHO guidance (see annex) for safety."
        refs = R.build_references(plan_of(([claim("m5", quote=q)], [])), POST, CANON)
        assert refs["claims"]["m5"]["chip"] == "WHO"

    def test_quote_not_found_is_none(self):
        refs = R.build_references(
            plan_of(([claim("m6", quote="totally absent sentence here")], [])), POST, CANON)
        assert refs["claims"]["m6"]["tier"] == "none"

    def test_marker_without_citation_entry_ignored(self):
        post = {"url": "u", "citations": []}
        refs = R.build_references(plan_of(([claim("m1", quote=Q1)], [])), post, CANON)
        assert refs["claims"]["m1"]["tier"] == "blog"


class TestSourceClaimsAndWeak:
    def test_source_claim_uses_support_url(self):
        c = claim("f1", kind="external_fact", role="fact", quote="x", url="https://www.who.int/foo")
        refs = R.build_references(plan_of(([c], [])), POST, CANON)
        assert refs["claims"]["f1"]["chip"] == "WHO"

    def test_wikipedia_never_gets_chip(self):
        c = claim("f1", kind="external_fact", role="fact", quote="x",
                  url="https://en.wikipedia.org/wiki/H2S")
        refs = R.build_references(plan_of(([c], [])), POST, CANON)
        assert refs["claims"]["f1"]["tier"] == "weak" and refs["claims"]["f1"]["chip"] is None
        assert refs["sources"][0]["tier"] == "weak"

    def test_weak_citation_from_post_gets_no_chip(self):
        refs = R.build_references(plan_of(([claim("m3", quote=Q3)], [])), POST, CANON)
        assert refs["claims"]["m3"]["tier"] == "weak" and refs["claims"]["m3"]["chip"] is None

    def test_unknown_web_is_weak(self):
        c = claim("f1", kind="external_fact", role="fact", quote="x", url="https://someblog.example.com/p")
        refs = R.build_references(plan_of(([c], [])), POST, CANON)
        assert refs["claims"]["f1"]["tier"] == "weak"

    def test_best_source_wins_when_mixed(self):
        canon = "A fact stated here1 and confirmed3."
        c = claim("m1", quote="A fact stated here and confirmed.")
        refs = R.build_references(plan_of(([c], [])), POST, canon)
        assert refs["claims"]["m1"]["tier"] == "authoritative"


class TestChipLabels:
    def test_labels(self):
        label = R.publisher_label
        assert label("https://www.epa.gov/x") == "US EPA"
        assert label("https://echa.europa.eu/x") == "ECHA"
        assert label("https://eur-lex.europa.eu/legal-content") == "EUR-Lex"
        assert label("https://www.who.int/x") == "WHO"
        assert label("https://pubs.usgs.gov/x") == "USGS"
        assert label("https://www.osti.gov/x") == "osti.gov"
        assert label("https://www.nature.com/articles/1") == "nature.com"


class TestReasoning:
    def test_inherits_when_all_premises_sourced(self):
        refs = R.build_references(
            plan_of(([claim("m1", quote=Q1), claim("r1", kind="reasoning")], [])), POST, CANON)
        r = refs["claims"]["r1"]
        assert r["tier"] == "authoritative" and r["sources"] == refs["claims"]["m1"]["sources"]

    def test_none_when_a_premise_unsourced(self):
        steps = [([claim("m1", quote=Q1), claim("m4", quote=QU), claim("r1", kind="reasoning")], [])]
        refs = R.build_references(plan_of(*steps), POST, CANON)
        assert refs["claims"]["r1"] == {"sources": [], "tier": "none", "chip": None}

    def test_depends_on_previous_step_claims_are_premises(self):
        steps = [([claim("m1", quote=Q1)], []), ([claim("r1", kind="reasoning")], ["m1"])]
        refs = R.build_references(plan_of(*steps), POST, CANON)
        assert refs["claims"]["r1"]["tier"] == "authoritative"

    def test_reasoning_without_premises_is_none(self):
        refs = R.build_references(plan_of(([claim("r1", kind="reasoning")], [])), POST, CANON)
        assert refs["claims"]["r1"]["tier"] == "none"

    def test_chain_of_reasoning(self):
        steps = [([claim("m1", quote=Q1), claim("r1", kind="reasoning")], []),
                 ([claim("r2", kind="reasoning")], ["r1"])]
        refs = R.build_references(plan_of(*steps), POST, CANON)
        assert refs["claims"]["r2"]["tier"] == "authoritative"


class TestGrade:
    def _grade(self, n_auth, n_other):
        cl, parts = [], []
        for i in range(n_auth):
            parts.append(f"Auth sentence number {i} here1.")
            cl.append(claim(f"a{i}", quote=f"Auth sentence number {i} here."))
        for i in range(n_other):
            parts.append(f"Plain sentence number {i} here.")
            cl.append(claim(f"b{i}", quote=f"Plain sentence number {i} here."))
        return R.build_references(plan_of((cl, [])), POST, " ".join(parts))["grade"]

    def test_strong(self):
        g = self._grade(7, 3)
        assert g["verdict"] == "strong" and g["key_claims"] == 10 and g["authoritative"] == 7
        assert g["blog_only"] == 3

    def test_adequate(self):
        assert self._grade(4, 6)["verdict"] == "adequate"

    def test_thin(self):
        assert self._grade(3, 7)["verdict"] == "thin"

    def test_illustrative_excluded_and_empty_is_thin(self):
        ill = claim("i1", kind="illustrative", role="fact")
        refs = R.build_references(plan_of(([ill], [])), POST, CANON)
        assert refs["grade"]["key_claims"] == 0 and refs["grade"]["verdict"] == "thin"

    def test_unsupported_claims_excluded(self):
        c = claim("m1", quote=Q1, verdict="unsupported")
        refs = R.build_references(plan_of(([c], [])), POST, CANON)
        assert refs["grade"]["key_claims"] == 0 and "m1" not in refs["claims"]

    def test_weak_counted(self):
        refs = R.build_references(plan_of(([claim("m3", quote=Q3)], [])), POST, CANON)
        assert refs["grade"]["weak"] == 1


class TestPickChips:
    def _setup(self):
        canon = " ".join(f"Claim number {i} stated here{1 if i % 2 else 2}." for i in range(8))
        steps = []
        for s in range(4):
            steps.append(([claim(f"a{s}", quote=f"Claim number {s*2} stated here."),
                           claim(f"b{s}", role="fact", quote=f"Claim number {s*2+1} stated here.")], []))
        plan = plan_of(*steps)
        return R.build_references(plan, POST, canon), plan

    def test_limit_and_one_per_step(self):
        refs, plan = self._setup()
        picks = R.pick_chip_claims(refs, plan, limit=4)
        assert len(picks) == 4 and len({p["step_id"] for p in picks}) == 4

    def test_limit_respected(self):
        refs, plan = self._setup()
        assert len(R.pick_chip_claims(refs, plan, limit=2)) == 2

    def test_chipless_claims_never_picked(self):
        canon = "Plain mechanism stated here1. Weak one here3."
        plan = plan_of(([claim("m1", quote="Plain mechanism stated here.")], []),
                       ([claim("w1", quote="Weak one here.")], []))
        refs = R.build_references(plan, POST, canon)
        picks = R.pick_chip_claims(refs, plan)
        assert [p["claim_id"] for p in picks] == ["m1"]
        assert picks[0]["label"] == "US EPA" and picks[0]["text"] and picks[0]["step_id"] == "s1"

    def test_numeric_preferred_within_step(self):
        canon = "Plain mechanism stated here1. The dose is 20 mg/L in the stream2."
        plan = plan_of(([claim("m1", quote="Plain mechanism stated here."),
                         claim("f1", role="fact", quote="The dose is 20 mg/L in the stream.")], []))
        refs = R.build_references(plan, POST, canon)
        assert [p["claim_id"] for p in R.pick_chip_claims(refs, plan)] == ["f1"]

    def test_reasoning_never_picked(self):
        plan = plan_of(([claim("m1", quote=Q1), claim("r1", kind="reasoning")], []))
        refs = R.build_references(plan, POST, CANON)
        assert [p["claim_id"] for p in R.pick_chip_claims(refs, plan)] == ["m1"]


class TestDescription:
    def _refs(self, quote=Q1):
        plan = plan_of(([claim("m1", quote=quote)], []))
        return R.build_references(plan, POST, CANON), plan

    def test_strong_description(self):
        refs, plan = self._refs()
        assert refs["grade"]["verdict"] == "strong"
        d = R.render_description(refs, POST, plan)
        assert d.splitlines()[0] == "How does it work?"
        assert "Sources" in d
        assert "1. US EPA - EPA H2S Control - https://www.epa.gov/wastewater/h2s" in d
        assert "Full technical guide: https://hrsuindore.com/blog/x/" in d
        assert "Every claim in this video was checked against its source; references above." in d

    def test_not_strong_omits_grade_sentence(self):
        refs, plan = self._refs(quote=QU)
        d = R.render_description(refs, POST, plan)
        assert "Every claim in this video" not in d
        assert "Full technical guide: https://hrsuindore.com/blog/x/" in d

    def test_weak_sources_not_listed_and_no_emoji(self):
        plan = plan_of(([claim("m1", quote=Q1), claim("m3", quote=Q3)], []))
        refs = R.build_references(plan, POST, CANON)
        d = R.render_description(refs, POST, plan)
        assert "wikipedia" not in d
        assert all(ord(ch) < 0x2000 for ch in d)

    def test_empty_title_falls_back_to_publisher_and_url(self):
        plan = plan_of(([claim("m2", quote=Q2)], []))
        refs = R.build_references(plan, POST, CANON)
        assert "1. ECHA - https://echa.europa.eu/" in R.render_description(refs, POST, plan)

    def test_no_post_url_no_guide_line(self):
        refs, plan = self._refs()
        d = R.render_description(refs, {"citations": POST["citations"]}, plan)
        assert "Full technical guide" not in d


class TestOutputs:
    def test_write_outputs_and_cli(self, tmp_path):
        plan = plan_of(([claim("m1", quote=Q1)], []))
        (tmp_path / "explanation_plan.json").write_text(json.dumps(plan), encoding="utf-8")
        (tmp_path / "post.json").write_text(json.dumps(POST), encoding="utf-8")
        (tmp_path / "canonical.txt").write_text(CANON, encoding="utf-8")
        R.write_outputs(tmp_path)
        out = json.loads((tmp_path / "references.json").read_text(encoding="utf-8"))
        assert out["chips"][0]["claim_id"] == "m1" and out["chips"][0]["label"] == "US EPA"
        assert out["chips"][0]["step_id"] == "s1" and out["chips"][0]["text"]
        desc = (tmp_path / "video_description.txt").read_text(encoding="utf-8")
        assert desc.startswith("How does it work?")
        assert R.main([str(tmp_path)]) == 0

    def test_missing_citations_key_is_thin_not_error(self, tmp_path):
        plan = plan_of(([claim("m1", quote=Q1)], []))
        for name, data in (("explanation_plan.json", plan), ("post.json", {"url": "u"})):
            (tmp_path / name).write_text(json.dumps(data), encoding="utf-8")
        (tmp_path / "canonical.txt").write_text(CANON, encoding="utf-8")
        R.write_outputs(tmp_path)
        out = json.loads((tmp_path / "references.json").read_text(encoding="utf-8"))
        assert out["grade"]["verdict"] == "thin"
