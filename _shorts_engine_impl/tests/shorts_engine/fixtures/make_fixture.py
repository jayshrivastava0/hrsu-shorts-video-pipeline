"""One-time fixture generator: extracts the captured Blogger page HTML from the
2026-06-25 run's storyboard.json."""
from __future__ import annotations
import json
from pathlib import Path

SRC = Path("../../../../output/videos/optimizing-nitrate-removal-via-granular-html/storyboard.json")
DST = Path(__file__).parent / "nitrate_post.html"

if __name__ == "__main__":
    sb = json.loads(SRC.read_text(encoding="utf-8"))
    DST.write_text(sb["blog"]["content_html"], encoding="utf-8")
    print(f"wrote {DST} ({DST.stat().st_size} bytes)")
