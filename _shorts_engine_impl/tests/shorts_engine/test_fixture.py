"""Test that the nitrate_post fixture contains canonical markers."""
from __future__ import annotations
from pathlib import Path
import pytest


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "nitrate_post.html"


class TestFixture:
    """Verify the poisoned Blogger page fixture is present and contains markers."""

    def test_fixture_exists(self) -> None:
        """Fixture file must exist."""
        assert FIXTURE_PATH.exists(), f"Fixture not found at {FIXTURE_PATH}"

    def test_target_post_marker_present(self) -> None:
        """Fixture contains the target-post marker."""
        content = FIXTURE_PATH.read_text(encoding="utf-8")
        assert "dosage range of 1.5 to 3 kg per cubic meter" in content, (
            "Target-post marker not found in fixture"
        )

    def test_sibling_teaser_marker_present(self) -> None:
        """Fixture contains the sibling-teaser marker."""
        content = FIXTURE_PATH.read_text(encoding="utf-8")
        assert "150,000 metric tons" in content, (
            "Sibling-teaser marker not found in fixture"
        )

    def test_target_post_url_present(self) -> None:
        """Fixture contains the target post URL."""
        content = FIXTURE_PATH.read_text(encoding="utf-8")
        assert "https://blog.hrsuindore.com/2026/06/optimizing-nitrate-removal-via-granular.html" in content, (
            "Target post URL not found in fixture"
        )

    def test_target_post_title_present(self) -> None:
        """Fixture contains the target post title."""
        content = FIXTURE_PATH.read_text(encoding="utf-8")
        assert "Optimizing Nitrate Removal via Granular Calcium Nitrate" in content, (
            "Target post title not found in fixture"
        )

    def test_fixture_is_html(self) -> None:
        """Fixture file contains HTML content."""
        content = FIXTURE_PATH.read_text(encoding="utf-8")
        assert content.startswith("<!"), "Fixture does not appear to be HTML"
        assert "<html" in content.lower() or "<!doctype" in content.lower(), (
            "Fixture is not valid HTML"
        )
