"""Intake for brief-driven videos: validate brief text, classify type, vet assets."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from scheduler.briefs import formats
from scheduler.briefs.model import VIDEO_TYPES, Brief, brief_id

MAX_BRIEF_CHARS = 4000

_PDF_MAGIC = b"%PDF"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"

# extension -> detected kind (must match the magic bytes)
_ALLOWED_EXTENSIONS = {
    ".pdf": "pdf",
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
}


class NeedsType(Exception):
    """Classifier confidence is below the floor; the user must pick a type."""

    def __init__(self, guess: str, confidence: float):
        self.guess = guess
        self.confidence = confidence
        super().__init__(
            f"could not classify brief confidently (guess {guess!r}, "
            f"confidence {confidence:.2f}); pass --type explicitly"
        )


class BriefRefused(Exception):
    """The brief or its assets cannot be used; carries a human-readable message."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def classify(text: str, llm: Callable[[str], dict]) -> tuple[str, float]:
    """Ask the llm which video type fits the brief.

    Returns (type, confidence). A type outside VIDEO_TYPES is reported with
    confidence 0.0 so the caller treats it as unclassified.
    """
    prompt = (
        "Classify the following brief into exactly one video type.\n"
        f"Allowed types: {', '.join(VIDEO_TYPES)}.\n"
        'Respond as JSON: {"type": <one allowed type>, "confidence": <0.0-1.0>}.\n\n'
        f"Brief:\n{text}"
    )
    result = llm(prompt)
    video_type = str(result.get("type", "")).strip().lower()
    try:
        confidence = float(result.get("confidence", 0.0))
    except (TypeError, ValueError):   # malformed model output counts as no confidence
        return video_type, 0.0
    if video_type not in VIDEO_TYPES:
        return video_type, 0.0
    return video_type, confidence


def _detect_kind(path: Path) -> str | None:
    head = path.read_bytes()[:8]
    if head.startswith(_PDF_MAGIC):
        return "pdf"
    if head.startswith(_PNG_MAGIC):
        return "png"
    if head.startswith(_JPEG_MAGIC):
        return "jpeg"
    return None


def _validate_asset(path: Path) -> None:
    ext = path.suffix.lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise BriefRefused(
            f"asset {path.name!r} has an unsupported type; "
            "allowed: PDF, PNG, JPEG"
        )
    detected = _detect_kind(path)
    if detected is None or detected != _ALLOWED_EXTENSIONS[ext]:
        raise BriefRefused(
            f"asset {path.name!r} content does not match its {ext} extension"
        )


def _list_assets(assets_dir: Path | None) -> list[Path]:
    if assets_dir is None or not assets_dir.is_dir():
        return []
    return sorted(p for p in assets_dir.iterdir() if p.is_file())


def build_brief(
    text: str,
    type_arg: str = "auto",
    url: str | None = None,
    assets_dir: Path | None = None,
    orientation: str | None = None,
    llm: Callable | None = None,
    confidence_floor: float = 0.6,
    *,
    angles: list[str] | None = None,
    cta: str | None = None,
    fmt: str | None = None,
    ratio: str | None = None,
    duration_s: int | None = None,
    pace: str = "standard",
) -> Brief:
    """Validate the brief text and assets and return a Brief with a resolved type."""
    if not text or not text.strip():
        raise BriefRefused("brief is empty; describe the video you want")
    text = text.strip()
    if len(text) > MAX_BRIEF_CHARS:
        raise BriefRefused(
            f"brief is {len(text)} characters; the limit is {MAX_BRIEF_CHARS}"
        )

    if type_arg == "auto":
        if llm is None:
            raise ValueError("type 'auto' needs an llm callable to classify the brief")
        guess, confidence = classify(text, llm)
        if confidence < confidence_floor:
            raise NeedsType(guess, confidence)
        video_type = guess
    elif type_arg in VIDEO_TYPES:
        video_type = type_arg
    else:
        raise ValueError(
            f"unknown video type {type_arg!r}; valid types: {', '.join(VIDEO_TYPES)}"
        )

    assets = _list_assets(assets_dir)
    for asset in assets:
        _validate_asset(asset)
    if video_type == "proof" and not assets:
        raise BriefRefused(
            "a proof video needs supporting documents or photos; "
            "add them to the assets folder and try again"
        )

    fmt_orientation, duration_s = formats.resolve(fmt, ratio, duration_s)
    orientation = orientation or fmt_orientation
    angles = [a.strip() for a in angles or [] if a and a.strip()]
    cta = (cta or "").strip() or None
    # only the new options change the id, so ids of older briefs (incl. --orientation ones) stay stable
    variant = "|".join(filter(None, [",".join(angles), cta or "", fmt_orientation or "", str(duration_s or ""), "" if pace == "standard" else pace]))
    return Brief(
        id=brief_id(text, variant),
        text=text,
        type=video_type,
        url=url,
        assets=assets,
        orientation=orientation,
        angles=angles,
        cta=cta,
        duration_s=duration_s,
        pace=pace,
    )


def stage_assets(brief: Brief, project_assets_dir: Path) -> list[Path]:
    """Copy the brief's validated assets into the project's assets folder."""
    project_assets_dir.mkdir(parents=True, exist_ok=True)
    staged: list[Path] = []
    for src in brief.assets:
        dest = project_assets_dir / src.name
        shutil.copyfile(src, dest)
        staged.append(dest)
    return staged


def _pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        PdfReader = None
    if PdfReader is not None:
        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    import pdfplumber

    with pdfplumber.open(str(path)) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


def extract_asset_text(path: Path) -> str:
    """Return the text of a PDF asset. Images have no traceable text, so return ''."""
    if _detect_kind(path) == "pdf":
        return _pdf_text(path)
    return ""
