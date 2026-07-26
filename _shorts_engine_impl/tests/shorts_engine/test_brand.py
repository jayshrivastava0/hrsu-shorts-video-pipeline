"""Tests for the brand facts loader — BrandFacts dataclass and load_brand_facts()."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import tempfile
import pytest
import yaml

from shorts_engine.brand import BrandFacts, load_brand_facts
from shorts_engine.errors import EngineConfigError


class TestBrandFactsDataclass:
    """Test BrandFacts dataclass."""

    def test_brand_facts_creation_with_differentiators(self) -> None:
        """BrandFacts stores company, domain, tagline, differentiators, cta_lines, banned_claims."""
        brand = BrandFacts(
            company="HRSU Indore Pvt. Ltd.",
            domain="hrsuindore.com",
            tagline="Beyond Granules. The Purity of Powder.",
            differentiators=[
                {"id": "b_purity", "text": "Consistent high-purity calcium nitrate powder"}
            ],
            cta_lines=["Visit hrsuindore.com"],
            banned_claims=["REACH registered", "FDA approved"]
        )
        assert brand.company == "HRSU Indore Pvt. Ltd."
        assert brand.domain == "hrsuindore.com"
        assert brand.tagline == "Beyond Granules. The Purity of Powder."
        assert len(brand.differentiators) == 1
        assert brand.differentiators[0]["id"] == "b_purity"
        assert len(brand.cta_lines) == 1
        assert len(brand.banned_claims) == 2

    def test_brand_facts_differentiators_have_id_and_text(self) -> None:
        """Each differentiator dict must have 'id' and 'text' keys."""
        brand = BrandFacts(
            company="Test Co",
            domain="test.com",
            tagline="Test tagline",
            differentiators=[
                {"id": "b_supply", "text": "Responsive supply chain"}
            ],
            cta_lines=["Visit us"],
            banned_claims=[]
        )
        diff = brand.differentiators[0]
        assert "id" in diff
        assert "text" in diff
        assert diff["id"] == "b_supply"

    def test_brand_facts_with_all_three_approved_differentiators(self) -> None:
        """BrandFacts can contain all three approved differentiator IDs."""
        brand = BrandFacts(
            company="Test Co",
            domain="test.com",
            tagline="Test tagline",
            differentiators=[
                {"id": "b_purity", "text": "High purity"},
                {"id": "b_supply", "text": "Flexible supply"},
                {"id": "b_esg", "text": "Sustainable practices"}
            ],
            cta_lines=["Visit us"],
            banned_claims=[]
        )
        ids = [d["id"] for d in brand.differentiators]
        assert "b_purity" in ids
        assert "b_supply" in ids
        assert "b_esg" in ids


class TestLoadBrandFacts:
    """Test load_brand_facts() loader function."""

    def test_loads_valid_yaml_file(self) -> None:
        """load_brand_facts() reads and parses a valid YAML file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_path = Path(tmpdir) / "brand_facts.yaml"
            yaml_content = {
                "company": "HRSU Indore Pvt. Ltd.",
                "domain": "hrsuindore.com",
                "tagline": "Beyond Granules. The Purity of Powder.",
                "differentiators": [
                    {"id": "b_purity", "text": "Consistent high-purity calcium nitrate powder"}
                ],
                "cta_lines": ["Full technical guide on the HRSU blog — link in description."],
                "banned_claims": ["REACH registered", "FDA approved"]
            }
            with open(yaml_path, 'w') as f:
                yaml.dump(yaml_content, f)

            brand = load_brand_facts(yaml_path)
            assert brand.company == "HRSU Indore Pvt. Ltd."
            assert brand.domain == "hrsuindore.com"

    def test_utf8_punctuation_survives_loading(self) -> None:
        """Regression: the real brand_facts.yaml contains a UTF-8 em dash in
        a CTA line; loading without encoding='utf-8' decoded it via cp1252
        on Windows, and the mojibake rendered verbatim onto the LOGO_CTA end
        card of a live video."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_path = Path(tmpdir) / "brand_facts.yaml"
            cta = "Full technical guide on the HRSU blog — link in description."
            yaml_content = {
                "company": "HRSU", "domain": "hrsuindore.com", "tagline": "t",
                "differentiators": [{"id": "b_purity", "text": "pure"}],
                "cta_lines": [cta],
                "banned_claims": [],
            }
            with open(yaml_path, 'w', encoding='utf-8') as f:
                yaml.dump(yaml_content, f, allow_unicode=True)

            brand = load_brand_facts(yaml_path)
            assert brand.cta_lines[0] == cta
            assert "â€" not in brand.cta_lines[0]  # no mojibake

    def test_raises_engine_config_error_if_file_missing(self) -> None:
        """load_brand_facts() raises EngineConfigError if file not found."""
        nonexistent_path = Path("/nonexistent/brand_facts.yaml")
        with pytest.raises(EngineConfigError):
            load_brand_facts(nonexistent_path)

    def test_raises_engine_config_error_if_invalid_yaml(self) -> None:
        """load_brand_facts() raises EngineConfigError if YAML is malformed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_path = Path(tmpdir) / "brand_facts.yaml"
            with open(yaml_path, 'w') as f:
                f.write("{ invalid yaml : [ content }")

            with pytest.raises(EngineConfigError):
                load_brand_facts(yaml_path)

    def test_raises_engine_config_error_if_missing_company(self) -> None:
        """load_brand_facts() raises EngineConfigError if 'company' key is missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_path = Path(tmpdir) / "brand_facts.yaml"
            yaml_content = {
                "domain": "hrsuindore.com",
                "tagline": "Tagline",
                "differentiators": [],
                "cta_lines": [],
                "banned_claims": []
            }
            with open(yaml_path, 'w') as f:
                yaml.dump(yaml_content, f)

            with pytest.raises(EngineConfigError):
                load_brand_facts(yaml_path)

    def test_raises_engine_config_error_if_missing_domain(self) -> None:
        """load_brand_facts() raises EngineConfigError if 'domain' key is missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_path = Path(tmpdir) / "brand_facts.yaml"
            yaml_content = {
                "company": "HRSU",
                "tagline": "Tagline",
                "differentiators": [],
                "cta_lines": [],
                "banned_claims": []
            }
            with open(yaml_path, 'w') as f:
                yaml.dump(yaml_content, f)

            with pytest.raises(EngineConfigError):
                load_brand_facts(yaml_path)

    def test_raises_engine_config_error_if_missing_tagline(self) -> None:
        """load_brand_facts() raises EngineConfigError if 'tagline' key is missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_path = Path(tmpdir) / "brand_facts.yaml"
            yaml_content = {
                "company": "HRSU",
                "domain": "hrsuindore.com",
                "differentiators": [],
                "cta_lines": [],
                "banned_claims": []
            }
            with open(yaml_path, 'w') as f:
                yaml.dump(yaml_content, f)

            with pytest.raises(EngineConfigError):
                load_brand_facts(yaml_path)

    def test_raises_engine_config_error_if_missing_differentiators(self) -> None:
        """load_brand_facts() raises EngineConfigError if 'differentiators' key is missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_path = Path(tmpdir) / "brand_facts.yaml"
            yaml_content = {
                "company": "HRSU",
                "domain": "hrsuindore.com",
                "tagline": "Tagline",
                "cta_lines": [],
                "banned_claims": []
            }
            with open(yaml_path, 'w') as f:
                yaml.dump(yaml_content, f)

            with pytest.raises(EngineConfigError):
                load_brand_facts(yaml_path)

    def test_raises_engine_config_error_if_missing_cta_lines(self) -> None:
        """load_brand_facts() raises EngineConfigError if 'cta_lines' key is missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_path = Path(tmpdir) / "brand_facts.yaml"
            yaml_content = {
                "company": "HRSU",
                "domain": "hrsuindore.com",
                "tagline": "Tagline",
                "differentiators": [],
                "banned_claims": []
            }
            with open(yaml_path, 'w') as f:
                yaml.dump(yaml_content, f)

            with pytest.raises(EngineConfigError):
                load_brand_facts(yaml_path)

    def test_raises_engine_config_error_if_missing_banned_claims(self) -> None:
        """load_brand_facts() raises EngineConfigError if 'banned_claims' key is missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_path = Path(tmpdir) / "brand_facts.yaml"
            yaml_content = {
                "company": "HRSU",
                "domain": "hrsuindore.com",
                "tagline": "Tagline",
                "differentiators": [],
                "cta_lines": []
            }
            with open(yaml_path, 'w') as f:
                yaml.dump(yaml_content, f)

            with pytest.raises(EngineConfigError):
                load_brand_facts(yaml_path)

    def test_load_with_all_three_differentiator_types(self) -> None:
        """load_brand_facts() correctly loads YAML with all three approved differentiators."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_path = Path(tmpdir) / "brand_facts.yaml"
            yaml_content = {
                "company": "HRSU Indore Pvt. Ltd.",
                "domain": "hrsuindore.com",
                "tagline": "Beyond Granules. The Purity of Powder.",
                "differentiators": [
                    {"id": "b_purity", "text": "Consistent high-purity calcium nitrate powder with batch-level QC"},
                    {"id": "b_supply", "text": "Flexible minimum order quantities and responsive quoting for trial orders"},
                    {"id": "b_esg", "text": "Solar power and steam-reuse initiatives at the Indore plant"}
                ],
                "cta_lines": [
                    "Full technical guide on the HRSU blog — link in description.",
                    "Sourcing calcium nitrate? Visit hrsuindore.com"
                ],
                "banned_claims": [
                    "REACH registered",
                    "REACH-registered",
                    "certified",
                    "ISO 9001",
                    "FDA approved"
                ]
            }
            with open(yaml_path, 'w') as f:
                yaml.dump(yaml_content, f)

            brand = load_brand_facts(yaml_path)
            assert len(brand.differentiators) == 3
            assert len(brand.cta_lines) == 2
            assert len(brand.banned_claims) == 5
            ids = [d["id"] for d in brand.differentiators]
            assert ids == ["b_purity", "b_supply", "b_esg"]
