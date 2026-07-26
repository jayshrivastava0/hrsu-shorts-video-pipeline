"""End-to-end smoke: build script → voiceover → visuals → subtitles → composer."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import logging
logging.basicConfig(level=logging.INFO)

from video_agent.script_builder import build_script
from video_agent.voiceover import synthesize
from video_agent.visual_engine.dispatcher import generate_all_visuals
from video_agent.subtitles import generate_srt
from video_agent.composer import compose_short

BLOG = {
    "blog_id": "smoke_e2e",
    "title": "Calcium Nitrate Cuts H2S in Australian Wastewater Plants",
    "url": "https://blog.hrsuindore.com/test",
    "region": "australia", "persona": "procurement",
    "category": "wastewater_treatment", "subcategory": "h2s",
    "content_html": "<p>Calcium nitrate cut H2S by 90% at 50 mg/L in 24 hours. "
                    "Australian utilities reported 15% chemical-cost savings.</p>",
    "summary": "smoke",
}


def main():
    workspace = Path("output/videos/smoke_e2e")
    workspace.mkdir(parents=True, exist_ok=True)

    script = build_script(BLOG, output_dir=workspace)
    print(f"Scenes: {len(script['scenes'])}, narration words: {len(script['narration'].split())}")

    voice = synthesize(script["narration"], workspace / "voiceover.mp3", region="australia")
    print(f"Voice: {voice}")

    visuals = generate_all_visuals(script["scenes"], workspace / "scenes")

    srt = generate_srt(voice["audio_path"], workspace / "subtitles.srt",
                       narration_hint=script["narration"])

    out = workspace / "video_short.mp4"
    compose_short(
        scenes=script["scenes"], visual_results=visuals, voiceover=voice,
        subtitle_path=srt, output_path=out, music_track=None,
        region="australia", persona="procurement",
    )
    print(f"Done: {out} ({out.stat().st_size / 1_048_576:.1f} MB)")


if __name__ == "__main__":
    main()
