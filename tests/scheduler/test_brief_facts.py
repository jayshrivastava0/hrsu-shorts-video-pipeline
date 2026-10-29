from scheduler.briefs.facts import load_facts


def test_in_place_includes_door_delivery_and_garden():
    facts = load_facts()
    assert any("deliver" in s.lower() for s in facts.in_place)
    assert any("garden" in s.lower() for s in facts.in_place)


def test_in_progress_never_in_place():
    facts = load_facts()
    assert not set(facts.in_place) & set(facts.in_progress)
    assert any("electric" in s.lower() for s in facts.in_progress)
    assert not any("electric" in s.lower() for s in facts.in_place)


def test_banned_claims_kept():
    facts = load_facts()
    assert "certified" in facts.banned_claims


def test_never_claimed_lists_csr():
    facts = load_facts()
    assert any("CSR" in s for s in facts.never_claimed)


def test_no_solar_or_rainwater_is_dropped():
    facts = load_facts()
    assert any("solar" in s.lower() for s in facts.in_place)
    assert any("rainwater" in s.lower() for s in facts.in_place)


def test_chimney_fact_forbids_fully_compliant():
    facts = load_facts()
    chimney = [s for s in facts.in_place if "Chimneys" in s]
    assert len(chimney) == 1
    assert "fully compliant" in chimney[0]
    assert "no more" in chimney[0]
