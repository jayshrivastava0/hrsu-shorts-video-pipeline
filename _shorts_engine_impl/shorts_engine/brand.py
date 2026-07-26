"""Brand facts loader — BrandFacts dataclass and load_brand_facts()."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from shorts_engine.config import BRAND_FACTS_PATH
from shorts_engine.errors import EngineConfigError

logger = logging.getLogger(__name__)


@dataclass
class BrandFacts:
    """Brand facts dataclass.

    Stores company name, domain, tagline, differentiators, CTA lines, and banned claims.

    Attributes:
        company: Company name (e.g., "HRSU Indore Pvt. Ltd.")
        domain: Company domain (e.g., "hrsuindore.com")
        tagline: Brand tagline/motto
        differentiators: List of differentiator dicts with keys 'id' and 'text'
        cta_lines: List of call-to-action lines
        banned_claims: List of claims/phrases that should not appear in content
    """
    company: str
    domain: str
    tagline: str
    differentiators: list[dict[str, str]]
    cta_lines: list[str]
    banned_claims: list[str]


def load_brand_facts(path: Path = BRAND_FACTS_PATH) -> BrandFacts:
    """Load and parse brand_facts.yaml file.

    Args:
        path: Path to brand_facts.yaml file (defaults to config.BRAND_FACTS_PATH)

    Returns:
        BrandFacts dataclass instance

    Raises:
        EngineConfigError: If file is missing, YAML is invalid, or required keys are missing
    """
    # Check if file exists
    if not path.exists():
        raise EngineConfigError(
            f"brand_facts.yaml not found at {path}. "
            f"Expected at project root."
        )

    # Try to parse YAML. encoding= is load-bearing on Windows: the file
    # contains real UTF-8 punctuation (an em dash in a CTA line), and the
    # cp1252 default decoded it as mojibake that rendered verbatim onto the
    # LOGO_CTA end card in a live run.
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise EngineConfigError(f"Failed to parse YAML at {path}: {e}")
    except Exception as e:
        raise EngineConfigError(f"Error reading {path}: {e}")

    # Validate that data is a dict
    if not isinstance(data, dict):
        raise EngineConfigError(
            f"Expected YAML file to contain a dictionary, got {type(data).__name__}"
        )

    # Check for required keys
    required_keys = {"company", "domain", "tagline", "differentiators", "cta_lines", "banned_claims"}
    missing_keys = required_keys - set(data.keys())

    if missing_keys:
        raise EngineConfigError(
            f"brand_facts.yaml missing required keys: {', '.join(sorted(missing_keys))}"
        )

    # Extract and construct BrandFacts
    try:
        brand = BrandFacts(
            company=str(data["company"]),
            domain=str(data["domain"]),
            tagline=str(data["tagline"]),
            differentiators=list(data["differentiators"]),
            cta_lines=list(data["cta_lines"]),
            banned_claims=list(data["banned_claims"])
        )
    except Exception as e:
        raise EngineConfigError(f"Error constructing BrandFacts from YAML: {e}")

    logger.debug(f"Loaded brand facts from {path}: company={brand.company}, domain={brand.domain}")
    return brand
