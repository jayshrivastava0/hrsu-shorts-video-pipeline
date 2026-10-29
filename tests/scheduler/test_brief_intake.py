from pathlib import Path

import pytest

from scheduler.briefs.intake import (
    BriefRefused,
    NeedsType,
    build_brief,
    extract_asset_text,
    stage_assets,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def _minimal_pdf(text: str) -> bytes:
    """Hand-written one-page PDF with a Helvetica text object and a valid xref."""
    content = f"BT /F1 24 Tf 72 712 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    ).encode()
    return bytes(out)


def _llm(type_name: str, confidence: float):
    def fake(prompt: str) -> dict:
        return {"type": type_name, "confidence": confidence}

    return fake


def test_empty_or_whitespace_brief_refused():
    with pytest.raises(BriefRefused):
        build_brief("", type_arg="explainer")
    with pytest.raises(BriefRefused):
        build_brief("   \n\t  ", type_arg="explainer")


def test_brief_over_4000_chars_refused():
    with pytest.raises(BriefRefused):
        build_brief("a" * 4001, type_arg="explainer")


def test_brief_exactly_4000_chars_accepted():
    brief = build_brief("a" * 4000, type_arg="explainer")
    assert len(brief.text) == 4000


def test_non_ascii_brief_accepted():
    text = "कैलसियम नाइट्रेट “quoted” text"
    brief = build_brief(text, type_arg="explainer")
    assert brief.text == text


def test_explicit_type_skips_classifier():
    def boom(prompt: str) -> dict:
        raise AssertionError("classifier must not be called")

    brief = build_brief("calcium nitrate sewer story", type_arg="market", llm=boom)
    assert brief.type == "market"


def test_auto_low_confidence_raises_needs_type():
    with pytest.raises(NeedsType) as exc:
        build_brief("some vague brief", type_arg="auto", llm=_llm("esg", 0.4))
    assert exc.value.guess == "esg"
    assert exc.value.confidence == pytest.approx(0.4)


def test_auto_high_confidence_sets_type():
    brief = build_brief("calcium nitrate ESG report", type_arg="auto", llm=_llm("esg", 0.9))
    assert brief.type == "esg"


def test_proof_without_assets_refused(tmp_path):
    with pytest.raises(BriefRefused) as exc:
        build_brief("prove our dosing claim", type_arg="proof", assets_dir=tmp_path)
    assert "documents or photos" in str(exc.value)


def test_proof_without_assets_dir_refused():
    with pytest.raises(BriefRefused) as exc:
        build_brief("prove our dosing claim", type_arg="proof")
    assert "documents or photos" in str(exc.value)


def test_asset_extension_not_allowed_refused(tmp_path):
    (tmp_path / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32)
    with pytest.raises(BriefRefused):
        build_brief("prove it", type_arg="proof", assets_dir=tmp_path)


def test_asset_with_lying_extension_refused(tmp_path):
    (tmp_path / "invoice.png").write_bytes(_minimal_pdf("Invoice"))
    with pytest.raises(BriefRefused):
        build_brief("prove it", type_arg="proof", assets_dir=tmp_path)


def test_valid_assets_accepted(tmp_path):
    (tmp_path / "invoice.pdf").write_bytes(_minimal_pdf("Invoice 42"))
    (tmp_path / "photo.png").write_bytes(PNG_BYTES)
    (tmp_path / "site.jpg").write_bytes(JPEG_BYTES)
    brief = build_brief("prove it", type_arg="proof", assets_dir=tmp_path)
    assert sorted(p.name for p in brief.assets) == ["invoice.pdf", "photo.png", "site.jpg"]


def test_stage_assets_copies_into_project(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "photo.png").write_bytes(PNG_BYTES)
    brief = build_brief("prove it", type_arg="proof", assets_dir=src)
    project = tmp_path / "project" / "assets"
    staged = stage_assets(brief, project)
    assert len(staged) == 1
    assert staged[0].parent == project
    assert staged[0].name == "photo.png"
    assert staged[0].read_bytes() == PNG_BYTES


def test_extract_asset_text_reads_pdf_text(tmp_path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(_minimal_pdf("Dosing 42 mg per litre"))
    assert "Dosing 42 mg per litre" in extract_asset_text(pdf)


def test_extract_asset_text_image_is_empty(tmp_path):
    png = tmp_path / "photo.png"
    png.write_bytes(PNG_BYTES)
    assert extract_asset_text(png) == ""
