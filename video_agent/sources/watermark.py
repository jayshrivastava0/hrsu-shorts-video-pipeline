"""Reject images whose bottom strip contains visible watermarks or stock-photo text.
Uses Tesseract via pytesseract. If the binary is missing, the check skips
gracefully (returns False, "tesseract_unavailable").

Results are cached by file content hash to avoid re-OCRing on retry.
"""
from __future__ import annotations
import hashlib
import json
import logging
import re
from pathlib import Path
from PIL import Image

log = logging.getLogger(__name__)

_BLOCKLIST = re.compile(
    r"(copyright|©|getty|shutter|alamy|istock|dreamstime|"
    r"123rf|adobestock|depositphotos|watermark|stock\s*photo|"
    r"all\s*rights\s*reserved)",
    re.IGNORECASE,
)
_MIN_FLAGGED_CHARS = 8
_TESSERACT_OK: bool | None = None       # lazy import flag


def _ensure_tesseract() -> bool:
    global _TESSERACT_OK
    if _TESSERACT_OK is not None:
        return _TESSERACT_OK
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        _TESSERACT_OK = True
    except Exception as e:
        log.warning("Tesseract unavailable (%s); watermark check disabled. "
                    "Install on Windows: winget install UB-Mannheim.TesseractOCR", e)
        _TESSERACT_OK = False
    return _TESSERACT_OK


def is_watermarked(img_path: Path, cache_root: Path) -> tuple[bool, str]:
    """Returns (is_watermarked, reason).
    Caches results by file content SHA1 to skip re-OCR."""
    img_path = Path(img_path)
    digest = hashlib.sha1(img_path.read_bytes()).hexdigest()
    cache_file = Path(cache_root) / "watermark" / f"{digest}.json"
    if cache_file.exists():
        try:
            d = json.loads(cache_file.read_text())
            return (d["watermarked"], d["reason"])
        except Exception:
            pass    # fall through and re-check
    if not _ensure_tesseract():
        return (False, "tesseract_unavailable")
    import pytesseract
    try:
        with Image.open(img_path) as im:
            w, h = im.size
            strip = im.crop((0, int(h * 0.75), w, h))
            text = pytesseract.image_to_string(strip, config="--psm 6").strip()
    except Exception as e:
        log.debug("Watermark OCR failed for %s (%s)", img_path, e)
        return (False, "ocr_error")
    watermarked = False
    reason = ""
    m = _BLOCKLIST.search(text)
    if m:
        watermarked, reason = True, f"blocklist_match:{m.group(0).lower()}"
    elif len(text) >= _MIN_FLAGGED_CHARS:
        watermarked, reason = True, f"text_density:{len(text)}chars"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps({"watermarked": watermarked, "reason": reason}))
    return (watermarked, reason)
