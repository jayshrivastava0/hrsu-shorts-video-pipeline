"""Compose final 9:16 MP4 from scenes + visuals + voiceover + subtitles + music."""
import hashlib
import logging
import shutil
import subprocess
from pathlib import Path

from video_agent.config import (
    SHORT_FORMAT, KEN_BURNS_ZOOM_END, TRANSITION_DEFAULT_S, TRANSITION_AFTER_BROLL_S,
    LOGO_OPACITY, PROGRESS_BAR_HEIGHT_PX, MUSIC_VOLUME_DB, MUSIC_DUCKED_DB,
    BRAND_LOGO_WHITE_PATH, INTRO_VIDEO_PATH, OUTRO_VIDEO_PATH,
    BRAND_GOLD, OUTRO_VERSION, BRAND_NAVY_2, BRAND_DARK_NAVY, BRAND_LOGO_PATH,
)
from video_agent.visual_engine.brand_outro_card import render_cta_scene, render_logo_stinger
from PIL import Image
from video_agent.storyboard import Storyboard, Scene
from video_agent.motion.ken_burns import plan_ken_burns, render_motion_clip
from video_agent.motion.transitions import transition_between
from video_agent.motion.color_grade import apply_grade
from video_agent.music import mix_music_under_voice
from video_agent.safezone import (
    FRAME_W, FRAME_H, BOTTOM_RESERVE, fit_text_to_safe_zone, validate_frame, safe_rect,
)

log = logging.getLogger(__name__)


class ComposerError(RuntimeError):
    pass


# ─── Pure helpers (unit-tested) ────────────────────────────────────────────

def _probe_audio_duration(audio_path: Path) -> float:
    """Returns audio duration in seconds via ffprobe. Raises if probing fails."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


_MAX_SCENE_S = 20.0


def _redistribute_durations(scenes: list[dict], audio_duration_s: float,
                             max_scene_s: float = _MAX_SCENE_S) -> list[dict]:
    """Scale per-scene durations so they sum to audio_duration_s, then cap
    any single scene at max_scene_s and redistribute excess evenly so no
    visual stays on screen too long. Pure function."""
    out = [dict(s) for s in scenes]
    total = sum(s.get("duration_s", 0.0) for s in out)
    n = max(1, len(out))
    if total <= 0.01:
        each = audio_duration_s / n
        for s in out:
            s["duration_s"] = each
    else:
        factor = audio_duration_s / total
        for s in out:
            s["duration_s"] = s.get("duration_s", 0.0) * factor
    # Cap and redistribute excess
    for _ in range(3):  # iterate to converge when many scenes hit the cap
        excess = sum(max(0.0, s["duration_s"] - max_scene_s) for s in out)
        if excess < 0.01:
            break
        uncapped = [s for s in out if s["duration_s"] < max_scene_s]
        for s in out:
            s["duration_s"] = min(s["duration_s"], max_scene_s)
        if uncapped:
            bonus = excess / len(uncapped)
            for s in uncapped:
                s["duration_s"] += bonus
    return out


def _pick_music(music_dir: Path, region: str, persona: str) -> Path | None:
    music_dir = Path(music_dir)
    if not music_dir.exists():
        return None
    tracks = sorted(p for p in music_dir.glob("*.mp3"))
    if not tracks:
        return None
    key = f"{region}|{persona}".encode()
    idx = int(hashlib.sha1(key).hexdigest(), 16) % len(tracks)
    return tracks[idx]


def _srt_timestamp_to_ass(ts: str) -> str:
    """Convert SRT timestamp (HH:MM:SS,mmm) to ASS centiseconds (H:MM:SS.cc)."""
    ts = ts.strip().replace(",", ".")
    parts = ts.split(":")
    h, m = int(parts[0]), int(parts[1])
    s_ms = parts[2].split(".")
    s = int(s_ms[0])
    ms = int(s_ms[1]) if len(s_ms) > 1 else 0
    cc = ms // 10
    return f"{h}:{m:02d}:{s:02d}.{cc:02d}"


def _srt_to_ass(srt_path: Path, ass_path: Path, w: int, h: int) -> Path:
    """Convert SRT cue file to ASS with embedded script header for deterministic positioning."""
    import re as _re
    srt_text = srt_path.read_text(encoding="utf-8", errors="replace")
    # Split on blank lines between cues
    cues = [c.strip() for c in _re.split(r"\n{2,}", srt_text) if c.strip()]

    ass_lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {w}",
        f"PlayResY: {h}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, "
        "Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default,Poppins,62,&H00FFFFFF,&H00000000,&H99000000,"
        "1,3,4,0,2,60,60,260,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    for cue in cues:
        lines = cue.splitlines()
        # Skip index line if numeric
        start_idx = 0
        if lines and lines[0].strip().isdigit():
            start_idx = 1
        if start_idx >= len(lines):
            continue
        timecode_line = lines[start_idx] if len(lines) > start_idx else ""
        m = _re.match(r"([\d:,\.]+)\s+-->\s+([\d:,\.]+)", timecode_line)
        if not m:
            continue
        start = _srt_timestamp_to_ass(m.group(1))
        end = _srt_timestamp_to_ass(m.group(2))
        text_lines = lines[start_idx + 1:]
        text = r"\N".join(l.strip() for l in text_lines if l.strip())
        if not text:
            continue
        ass_lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")

    ass_path.write_text("\n".join(ass_lines), encoding="utf-8")
    return ass_path


# ─── ffmpeg helpers (covered by Task 20 smoke test) ────────────────────────

def _ffmpeg(cmd: list[str]) -> None:
    log.debug("ffmpeg: %s", " ".join(cmd))
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise ComposerError(f"ffmpeg failed (exit {res.returncode}): {res.stderr[-500:]}")


def _ffprobe_duration(path: Path) -> float:
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        raise ComposerError(f"ffprobe failed: {res.stderr[-300:]}")
    return float(res.stdout.strip())


def _ken_burns_clip(image_path: Path, duration: float,
                     out_w: int, out_h: int, zoom_end: float):
    """Build a fixed-size MoviePy clip that pans+zooms a still image.

    Each output frame is exactly (out_h, out_w, 3). The source image is
    pre-scaled so even at zoom=1.0 it covers the viewport; the viewport then
    drifts diagonally toward the center as zoom increases. This sidesteps
    MoviePy's resize-with-callable bug where time-varying clip dimensions
    cause concatenate_videoclips to letterbox shorter frames with black bars.
    """
    import numpy as np
    from moviepy.editor import VideoClip
    from PIL import Image

    src = Image.open(str(image_path)).convert("RGB")
    # Scale source so its short edge matches out_w * zoom_end (or taller),
    # leaving headroom for the pan.
    scale = max(out_w / src.width, out_h / src.height) * zoom_end
    src_w = int(round(src.width * scale))
    src_h = int(round(src.height * scale))
    src = src.resize((src_w, src_h), Image.LANCZOS)
    src_arr = np.asarray(src)

    max_dx = max(0, src_w - out_w)
    max_dy = max(0, src_h - out_h)

    def make_frame(t):
        p = min(1.0, max(0.0, t / max(duration, 1e-3)))
        # Smooth ease-in-out (cosine) so motion doesn't stop abruptly at scene cuts.
        eased = 0.5 - 0.5 * np.cos(np.pi * p)
        x = int(round(max_dx * 0.5 * (1.0 + 0.6 * (eased - 0.5) * 2)))  # drift ±30%
        y = int(round(max_dy * 0.5 * (1.0 + 0.6 * (eased - 0.5) * 2)))
        x = max(0, min(max_dx, x))
        y = max(0, min(max_dy, y))
        return src_arr[y:y + out_h, x:x + out_w]

    clip = VideoClip(make_frame, duration=duration)
    clip.size = (out_w, out_h)
    return clip


def _render_main_clip(scenes: list[dict], visuals: list[dict],
                      voiceover_path: Path, output_path: Path,
                      music_path: Path | None) -> Path:
    """Render concatenated scenes + voice + (optional) music to MP4 (no subs/intro yet)."""
    from moviepy.editor import (
        ImageClip, VideoFileClip, AudioFileClip, CompositeAudioClip,
        concatenate_videoclips, afx,
    )

    w, h = SHORT_FORMAT["resolution"]
    fps = SHORT_FORMAT["fps"]
    clips = []
    for scene, vis in zip(scenes, visuals):
        dur = scene["duration_s"]
        if vis["is_video_clip"]:
            c = VideoFileClip(str(vis["asset_path"])).without_audio()
            c = c.subclip(0, min(c.duration, dur)).resize((w, h))
            if c.duration < dur:
                c = c.fx(__import__("moviepy.video.fx.all", fromlist=["loop"]).loop, duration=dur)
        elif vis.get("is_static"):
            # Text cards and static infographics: no pan — center locked.
            c = ImageClip(str(vis["asset_path"])).set_duration(dur).resize((w, h))
        else:
            # Photos / bar charts / source images → gentle Ken Burns pan.
            c = _ken_burns_clip(Path(vis["asset_path"]), dur, w, h, KEN_BURNS_ZOOM_END)
        xfade = TRANSITION_AFTER_BROLL_S if scene.get("visual_type") == "hrsu_edge" else TRANSITION_DEFAULT_S
        if scene.get("transition_in") == "fade":
            c = c.crossfadein(xfade)
        clips.append(c)

    main = concatenate_videoclips(clips, method="compose", padding=-min(TRANSITION_DEFAULT_S, 0.2))

    voice = AudioFileClip(str(voiceover_path))
    audio = voice
    if music_path and music_path.exists():
        music = AudioFileClip(str(music_path)).volumex(10 ** (MUSIC_VOLUME_DB / 20))
        if music.duration < main.duration:
            music = afx.audio_loop(music, duration=main.duration)
        else:
            music = music.subclip(0, main.duration)
        audio = CompositeAudioClip([music, voice])
    main = main.set_audio(audio).set_duration(min(main.duration, voice.duration + 0.5))

    main.write_videofile(
        str(output_path),
        fps=fps, codec="libx264", audio_codec="aac",
        bitrate=SHORT_FORMAT["bitrate"], preset="medium",
        threads=2, logger=None,
        ffmpeg_params=["-pix_fmt", "yuv420p", "-movflags", "+faststart",
                       "-profile:v", "high", "-level", "4.0"],
    )
    return output_path


def _burn_subtitles(input_mp4: Path, srt_path: Path, output_mp4: Path) -> Path:
    w, h = SHORT_FORMAT["resolution"]
    ass_path = output_mp4.parent / "subs.ass"
    _srt_to_ass(srt_path, ass_path, w, h)
    # `ass` filter reads the embedded [Script Info] header so PlayResX/Y and
    # style properties are authoritative — no force_style needed.
    ass_str = str(ass_path).replace("\\", "/").replace(":", r"\:")
    vf = f"ass='{ass_str}'"
    _ffmpeg([
        "ffmpeg", "-y", "-i", str(input_mp4), "-vf", vf,
        "-c:a", "copy", "-c:v", "libx264", "-preset", "medium",
        "-pix_fmt", "yuv420p", str(output_mp4),
    ])
    ass_path.unlink(missing_ok=True)
    return output_mp4


# _OUTRO_CTA_TEXT and _add_cta_voiceover removed in v2.2 — outro voiceover
# now comes from the Storyboarder-written CTA beat via the main TTS pass.


def _append_logo_stinger(main_mp4: Path, output_mp4: Path) -> Path:
    """Render the silent 2s logo stinger and concat it after main_mp4."""
    stinger = main_mp4.parent / "_stinger.mp4"
    render_logo_stinger(
        logo_path=Path(BRAND_LOGO_PATH), output_path=stinger,
    )
    listfile = output_mp4.with_suffix(".concat.txt")
    listfile.write_text(
        f"file '{main_mp4.resolve().as_posix()}'\n"
        f"file '{stinger.resolve().as_posix()}'\n",
        encoding="utf-8",
    )
    _ffmpeg([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(listfile), "-c", "copy", str(output_mp4),
    ])
    listfile.unlink(missing_ok=True)
    stinger.unlink(missing_ok=True)
    return output_mp4


def _concat_intro_outro(main_mp4: Path, intro_mp4: Path | None,
                        outro_mp4: Path | None, output_mp4: Path) -> Path:
    if not intro_mp4 and not outro_mp4:
        shutil.copy2(main_mp4, output_mp4)
        return output_mp4
    parts = [Path(p).resolve() for p in (intro_mp4, main_mp4, outro_mp4) if p and Path(p).exists()]
    if len(parts) == 1:
        shutil.copy2(parts[0], output_mp4)
        return output_mp4
    listfile = output_mp4.with_suffix(".concat.txt")
    listfile.write_text("\n".join(f"file '{p.as_posix()}'" for p in parts), encoding="utf-8")
    _ffmpeg([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile),
        "-c", "copy", str(output_mp4),
    ])
    listfile.unlink(missing_ok=True)
    return output_mp4


def _validate_output(path: Path) -> None:
    if not path.exists() or path.stat().st_size < 50_000:
        raise ComposerError(f"Output too small: {path}")
    duration = _ffprobe_duration(path)
    if not (SHORT_FORMAT["min_duration_s"] - 2 <= duration <= SHORT_FORMAT["max_duration_s"] + 2):
        raise ComposerError(f"Duration {duration:.1f}s outside [{SHORT_FORMAT['min_duration_s']}, {SHORT_FORMAT['max_duration_s']}]")
    size_mb = path.stat().st_size / 1_048_576
    if size_mb > SHORT_FORMAT["max_filesize_mb"]:
        raise ComposerError(f"File {size_mb:.1f}MB exceeds {SHORT_FORMAT['max_filesize_mb']}MB cap")


# ─── Public API ────────────────────────────────────────────────────────────

def compose_short(*, scenes: list[dict], visual_results: list[dict],
                  voiceover: dict, subtitle_path: Path, output_path: Path,
                  music_track: Path | None = None,
                  region: str = "default", persona: str = "procurement") -> Path:
    if len(visual_results) != len(scenes):
        raise ComposerError(
            f"visual_results length ({len(visual_results)}) != scenes length ({len(scenes)})"
        )
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    workspace = output_path.parent

    audio_path = Path(voiceover["audio_path"])
    audio_duration = float(voiceover["duration_s"])
    scaled_scenes = _redistribute_durations(scenes, audio_duration)

    main_mp4 = workspace / "_main.mp4"
    _render_main_clip(scaled_scenes, visual_results, audio_path, main_mp4, music_track)

    subs_mp4 = workspace / "_main_subs.mp4"
    _burn_subtitles(main_mp4, subtitle_path, subs_mp4)

    intro = Path(INTRO_VIDEO_PATH) if Path(INTRO_VIDEO_PATH).exists() else None
    outro = Path(OUTRO_VIDEO_PATH) if Path(OUTRO_VIDEO_PATH).exists() else None
    _concat_intro_outro(subs_mp4, intro, outro, output_path)

    _validate_output(output_path)
    main_mp4.unlink(missing_ok=True)
    subs_mp4.unlink(missing_ok=True)
    return output_path


# ─── V2 composer (Task 4.5) ────────────────────────────────────────────────

def _overlay_on_screen_text(scene_clip: Path, scene: Scene,
                             font_path: str | None = None,
                             fps: int = 30,
                             skip_band: bool = False) -> Path:
    """Pass-through: the dark bottom band is removed in favour of the
    per-line subtitle box (BorderStyle=3 in the ASS style). All callers
    retain the skip_band parameter for compatibility but it is ignored."""
    import shutil as _sh
    out = scene_clip.with_name(scene_clip.stem + "_text.mp4")
    _sh.copy2(scene_clip, out)
    scene_clip.unlink()
    out.rename(scene_clip)
    return scene_clip


def _resolve_palette(scene: Scene) -> str:
    """Return the color palette for grading this scene.

    Prefers the Cinematographer's decision. Falls back to mood-based grading
    when the Cinematographer left the default 'neutral_doc' (i.e. it failed
    to apply any tool calls for this scene)."""
    cin = scene.cinematography
    if cin and cin.color_grade and cin.color_grade != "neutral_doc":
        return cin.color_grade
    return scene.visual_concept.mood


def _render_scene_clip(scene: Scene, workspace: Path, fps: int = 30) -> Path:
    """Render one scene to a per-scene MP4 with motion + on-screen text."""
    out = workspace / "scene_clips" / f"scene_{scene.index:02d}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)

    # CTA beat → brand template card with logo + URL wordmark. Voiceover
    # for this scene is produced by the main TTS pass; we only render video here.
    if scene.beat == "cta" and Path(BRAND_LOGO_PATH).exists():
        render_cta_scene(
            logo_path=Path(BRAND_LOGO_PATH),
            url_text="hrsuindore.com",
            duration_s=scene.duration_target_s,
            output_path=out, fps=fps,
        )
        scene._framing = "brand_card"
        # Brand-card already solid navy — no dark subtitle band needed.
        _overlay_on_screen_text(out, scene, fps=fps, skip_band=True)
        palette = _resolve_palette(scene)
        apply_grade(out, palette)
        return out

    if not scene.chosen_asset or scene.degraded:
        # Fallback: render a brand-coloured card (same navy as letterbox bands
        # so any frame using the brand colour is visually unified).
        img_path = workspace / "scene_clips" / f"_fb_{scene.index:02d}.jpg"
        Image.new("RGB", (FRAME_W, FRAME_H), BRAND_DARK_NAVY).save(img_path)
        plan = plan_ken_burns((FRAME_W, FRAME_H),
                              mood=scene.visual_concept.mood,
                              duration_s=scene.duration_target_s, fps=fps)
        render_motion_clip(img_path, plan, out, scene.duration_target_s, fps)
        scene._framing = "brand_card"
        # Brand-card already solid navy — no dark subtitle band needed.
        _overlay_on_screen_text(out, scene, fps=fps, skip_band=True)
        palette = _resolve_palette(scene)
        apply_grade(out, palette)
        return out

    asset = scene.chosen_asset
    src = Path(asset.local_path)
    # Vision-judge focal point for framing (defaults to centre when absent)
    focus_x: float = getattr(asset, "focus_x", 0.5) or 0.5
    focus_y: float = getattr(asset, "focus_y", 0.5) or 0.5
    subject_fills_frame: bool = getattr(asset, "subject_fills_frame", False)

    if asset.is_clip:
        if subject_fills_frame:
            # Subject fills frame → use blurred fill so landscape clips look
            # intentional rather than shrunk with navy letterbox bars.
            _render_blurfill_image(src, out, asset.duration_s or scene.duration_target_s,
                                   fps, focus_x=focus_x, focus_y=focus_y)
            scene._framing = "blurfill"
        else:
            # Standard clip: letterbox into 9:16 with brand navy.
            bg = BRAND_DARK_NAVY.lstrip("#")
            subprocess.run([
                "ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
                "-t", str(scene.duration_target_s),
                "-vf",
                f"scale={FRAME_W}:{FRAME_H}:force_original_aspect_ratio=decrease,"
                f"pad={FRAME_W}:{FRAME_H}:(ow-iw)/2:(oh-ih)/2:color=0x{bg},"
                f"setsar=1",
                "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                "-pix_fmt", "yuv420p", "-an", str(out),
            ], check=True)
            scene._framing = "letterbox"
        _overlay_on_screen_text(out, scene, fps=fps)
        palette = _resolve_palette(scene)
        apply_grade(out, palette)
        return out

    with Image.open(src) as im:
        size = im.size
    src_aspect = size[0] / max(1, size[1])
    target_aspect = FRAME_W / FRAME_H
    # Diagrams must stay fully visible — letterbox wide ones rather than crop.
    # subject_fills_frame=True from vision judge means cropping would lose content —
    # use blurred fill instead of navy letterbox for a more cinematic look.
    # Photos without that flag always crop-pan: a 9:16 viewport Ken Burns across
    # the source gives full-frame imagery with real motion.
    if (scene.visual_concept.type == "diagram"
            and src_aspect > target_aspect * 1.15):
        _render_letterbox_image(src, out, scene.duration_target_s, fps)
        scene._framing = "letterbox"
    elif subject_fills_frame and src_aspect > target_aspect * 1.1:
        _render_blurfill_image(src, out, scene.duration_target_s, fps,
                               focus_x=focus_x, focus_y=focus_y)
        scene._framing = "blurfill"
    else:
        plan = plan_ken_burns(size, mood=scene.visual_concept.mood,
                              duration_s=scene.duration_target_s, fps=fps,
                              focus_x=focus_x, focus_y=focus_y)
        render_motion_clip(src, plan, out, scene.duration_target_s, fps)
        scene._framing = "kenburns"
    _overlay_on_screen_text(out, scene, fps=fps)
    palette = (scene.cinematography.color_grade
               if scene.cinematography else scene.visual_concept.mood)
    apply_grade(out, palette)
    return out


def _render_blurfill_image(src: Path, dest: Path, duration_s: float,
                           fps: int, focus_x: float = 0.5,
                           focus_y: float = 0.5) -> Path:
    """Render a still image into 9:16 using a blurred-fill background.

    The full source is blurred and scaled to cover the frame (no navy bars).
    The original, unblurred source is letterboxed on top. Focal-point panning
    is applied on the sharp overlay so the subject stays centred."""
    total = max(1, int(duration_s * fps))
    # Blurred background: scale to cover full frame, heavy gaussian blur
    bg_vf = (
        f"scale={FRAME_W}:{FRAME_H}:force_original_aspect_ratio=increase,"
        f"crop={FRAME_W}:{FRAME_H},"
        f"gblur=sigma=40,setsar=1"
    )
    # Sharp overlay: letterbox inside the frame then animate with focus pan
    fg_vf = (
        f"scale={FRAME_W}:{FRAME_H}:force_original_aspect_ratio=decrease,"
        f"pad={FRAME_W}:{FRAME_H}:(ow-iw)/2:(oh-ih)/2:color=black@0,"
        f"setsar=1"
    )
    # Composite: background (blurred) + foreground (sharp letterbox)
    vf = (
        f"[0:v]split=2[bg][fg];"
        f"[bg]{bg_vf}[bg_out];"
        f"[fg]{fg_vf}[fg_out];"
        f"[bg_out][fg_out]overlay=0:0,setsar=1"
    )
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-loop", "1", "-i", str(src),
        "-filter_complex", vf,
        "-t", str(duration_s), "-r", str(fps),
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p", str(dest),
    ], check=True)
    return dest


def _render_letterbox_image(src: Path, dest: Path, duration_s: float, fps: int) -> Path:
    """Render a landscape still as letterbox (full width, brand navy bars) with a
    subtle zoom for motion."""
    bg = BRAND_DARK_NAVY.lstrip("#")
    total = max(1, int(duration_s * fps))
    # Subtle zoom 1.00 → 1.05 over the clip duration
    z = f"'min(zoom+0.0005,1.05)'"
    vf = (
        f"scale={FRAME_W}:{FRAME_H}:force_original_aspect_ratio=decrease,"
        f"pad={FRAME_W}:{FRAME_H}:(ow-iw)/2:(oh-ih)/2:color=0x{bg},"
        f"zoompan=z={z}:x='iw/2-(iw/zoom)/2':y='ih/2-(ih/zoom)/2':"
        f"d={total}:s={FRAME_W}x{FRAME_H}:fps={fps},setsar=1"
    )
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-loop", "1", "-i", str(src),
        "-vf", vf, "-t", str(duration_s), "-r", str(fps),
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p", str(dest),
    ], check=True)
    return dest


def _concat_with_transitions(scene_clips: list[Path], scenes: list[Scene],
                              workspace: Path) -> Path:
    """Concat per-scene clips with beat-aware transitions via xfade.

    xfade with duration=0 is invalid and causes a timebase-mismatch crash.
    Strategy: if all transitions are cuts, use the concat filter (forgiving
    about timebases).  If any transition has actual duration, normalize each
    input to 30 fps first so timebases match before xfade runs.
    """
    out = workspace / "_concat.mp4"
    if len(scene_clips) == 1:
        shutil.copy2(scene_clips[0], out)
        return out

    inputs = []
    for c in scene_clips:
        inputs += ["-i", str(c)]

    # Probe actual rendered clip durations — video clips (YouTube) may be
    # shorter than duration_target_s. Using target_s for xfade offsets causes
    # the accumulated offset to exceed the actual stream length, silently
    # dropping all clips after the overflow point.
    clip_durations = [_ffprobe_duration(c) for c in scene_clips]

    # Compute per-pair transitions
    pairs: list[tuple[str, float]] = []   # (ffx_name, duration_s)
    offset = clip_durations[0]
    offsets: list[float] = []
    for i in range(1, len(scene_clips)):
        cin = scenes[i].cinematography
        if cin and cin.transition_in:
            kind = cin.transition_in
        else:
            kind = transition_between(scenes[i - 1].beat, scenes[i].beat)
        if kind in ("slide_left", "dissolve_flash"):
            ffx, dur = "smoothleft", 0.4
        elif kind in ("cut", "hard_cut"):
            ffx, dur = "fade", 0.0
        elif kind in ("slow_fade",):
            ffx, dur = "fade", 0.5
        else:
            ffx, dur = "fade", 0.25
        # Use actual_dur for offset arithmetic — xfade silently truncates to
        # the first clip when duration < 1 frame (≈0.033s at 30fps). Use 2
        # frames as the minimum so ffmpeg always processes all streams.
        actual_dur_for_offset = dur if dur > 0 else 2 / 30
        pairs.append((ffx, dur))
        offsets.append(offset - actual_dur_for_offset)
        offset += clip_durations[i] - actual_dur_for_offset

    all_cuts = all(dur == 0.0 for _, dur in pairs)

    if all_cuts:
        # Simple concat — handles any timebase, no xfade filter needed
        n = len(scene_clips)
        fc = "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0[out]"
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error",
            *inputs, "-filter_complex", fc, "-map", "[out]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-pix_fmt", "yuv420p", str(out),
        ], check=True)
        return out

    # Mixed: normalize each input to 30 fps so timebases agree, then xfade
    norm = [f"[{i}:v]fps=fps=30[n{i}v]" for i in range(len(scene_clips))]
    last_label = "[n0v]"
    xfade_parts = []
    for i, ((ffx, dur), off) in enumerate(zip(pairs, offsets), 1):
        actual_dur = dur if dur > 0 else 2 / 30   # min 2 frames — sub-frame truncates to first clip
        actual_ffx = ffx if dur > 0 else "fade"
        out_label = f"[v{i}]"
        xfade_parts.append(
            f"{last_label}[n{i}v]xfade=transition={actual_ffx}:"
            f"duration={actual_dur}:offset={off}{out_label}"
        )
        last_label = out_label

    fc = ";".join(norm + xfade_parts)
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        *inputs, "-filter_complex", fc, "-map", last_label,
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p", str(out),
    ], check=True)
    return out


def _write_quality_report(sb, workspace: Path, voice_duration: float,
                          video_duration: float, outro_concatenated: bool) -> None:
    """Writes a machine-readable quality_report.json next to the final video."""
    import json
    from dataclasses import asdict

    report = {
        "video_duration_s": round(video_duration, 2),
        "voice_duration_s": round(voice_duration, 2),
        "durations_redistributed": True,
        "intro_concatenated": False,
        "outro_concatenated": outro_concatenated,
        "narrative_thread": list(sb.narrative_thread or []),
        "scenes": [
            {
                "index": s.index,
                "mood": getattr(s.visual_concept, "mood", None),
                "duration_s": round(s.duration_target_s, 2),
                "source": (s.chosen_asset.source if s.chosen_asset else None),
                "score": (s.chosen_asset.score if s.chosen_asset else None),
                "width": (s.chosen_asset.width if s.chosen_asset else 0),
                "height": (s.chosen_asset.height if s.chosen_asset else 0),
                "critic_flags": list(getattr(s.critic_notes, "flags", None) or []),
                "re_sourced": getattr(s, "_re_sourced", False),
                "degraded": s.degraded,
                "cinematography": (asdict(s.cinematography)
                                    if s.cinematography else None),
                # Vision provenance (Task D-2)
                "vision_score": (getattr(s.chosen_asset, "vision_score", None)
                                  if s.chosen_asset else None),
                "is_own_footage": (s.chosen_asset.source == "own_footage"
                                   if s.chosen_asset else False),
                "framing": getattr(s, "_framing", None),
            } for s in sb.scenes
        ],
    }
    output_path = workspace / "quality_report.json"
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")


def _validate_safe_zone(video_path: Path, n_samples: int = 12) -> list[str]:
    """Sample N frames from the video and run safe-zone checks."""
    problems = []
    tmp = video_path.parent / "_safezone_samples"
    tmp.mkdir(exist_ok=True)
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(video_path),
        "-vf", f"fps=fps={n_samples}/30,scale={FRAME_W}:{FRAME_H}",
        str(tmp / "f_%03d.jpg"),
    ], check=True)
    for fp in sorted(tmp.glob("f_*.jpg")):
        with Image.open(fp) as img:
            issues = validate_frame(img)
        if issues:
            problems.append((fp.name, issues))
    return problems


def compose_short_v2(sb: Storyboard, voice_path: Path, subtitle_path: Path,
                      output_path: Path, workspace: Path,
                      fps: int = 30) -> Path:
    """V2 composer: one MP4 per scene with motion + on-screen text, then
    concat with beat-aware xfades, then mux voice + music + subtitles, then
    auto-render+concat outro, then safe-zone validate. Raises on any
    safe-zone violation."""
    workspace = Path(workspace)
    output_path = Path(output_path)

    # 0. Probe voice and redistribute scene durations so each scene's visual
    #    stays synced to its narration (proportional to voice_duration).
    voice_duration = _probe_audio_duration(voice_path)
    CTA_TAIL_S = 1.2
    pre = sum(s.duration_target_s for s in sb.scenes)
    # Size scenes to the VOICE length so each scene's visual stays synced to
    # its own narration (proportional redistribution to voice_duration).
    scaled = _redistribute_durations(
        [{"duration_s": s.duration_target_s} for s in sb.scenes],
        voice_duration,
    )
    for s, new in zip(sb.scenes, scaled):
        s.duration_target_s = float(new["duration_s"])
    # Hold the FINAL CTA brand card CTA_TAIL_S longer so the spoken
    # "...visit hrsuindore.com" is never clipped by -shortest and the card
    # lingers a beat after the URL. Only the last (static) card is extended,
    # so every other scene stays tight to its narration.
    if sb.scenes:
        sb.scenes[-1].duration_target_s += CTA_TAIL_S
    # Guard: if any scene duration is <= 0 after redistribution, clamp to 0.5.
    for s in sb.scenes:
        if s.duration_target_s <= 0:
            log.warning("Scene %d duration <= 0 after redistribution, clamping to 0.5s", s.index)
            s.duration_target_s = 0.5
    post = sum(s.duration_target_s for s in sb.scenes)
    log.info("Redistributed scene durations: voice=%.2fs, pre=%.2fs -> post=%.2fs "
             "(CTA card held +%.1fs)", voice_duration, pre, post, CTA_TAIL_S)

    # 1. Render per-scene clips
    scene_clips = [_render_scene_clip(s, workspace, fps) for s in sb.scenes]

    # 2. Concat with transitions
    concat = _concat_with_transitions(scene_clips, sb.scenes, workspace)

    # 3. Mix music under voice
    voice_with_music = mix_music_under_voice(
        voice_path, workspace / "voice_with_music.mp3",
        region=sb.blog.get("region", "default"),
    )

    # 4. Mux audio + burn subtitles
    # Convert SRT → ASS and use the 'ass' filter (avoids Windows path issues
    # with the 'subtitles' filter which mishandles backslashes).
    ass_path = workspace / "_subs_v2.ass"
    _srt_to_ass(Path(subtitle_path), ass_path, FRAME_W, FRAME_H)
    ass_str = str(ass_path).replace("\\", "/").replace(":", r"\:")
    vf_subs = f"ass='{ass_str}'"
    subs_mp4 = workspace / "_with_subs.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(concat), "-i", str(voice_with_music),
        "-vf", vf_subs,
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p", "-shortest", str(subs_mp4),
    ], check=True)

    # Guard: the muxed video must contain the full voice track.
    muxed_dur = _probe_audio_duration(subs_mp4)
    if muxed_dur + 0.25 < voice_duration:  # 0.25s tolerance for ffmpeg rounding
        raise RuntimeError(
            f"CTA-truncation guard: muxed video {muxed_dur:.2f}s is shorter "
            f"than voice {voice_duration:.2f}s — the spoken CTA would be cut."
        )

    # 6. The CTA scene IS the outro — it shows the brand card with the
    #    Storyboarder-written voiceover. A separate silent stinger after the
    #    voice ends just creates an abrupt 2s dead-air at the tail, so we
    #    skip it. outro_concatenated stays True for quality_report continuity.
    shutil.copy2(subs_mp4, output_path)
    outro_concatenated = True

    # 7. Safe-zone validation
    problems = _validate_safe_zone(output_path)
    if problems:
        raise RuntimeError(f"Safe-zone violations: {problems}")

    # 8. Quality report
    video_duration = _probe_audio_duration(output_path)
    _write_quality_report(sb, workspace, voice_duration, video_duration,
                          outro_concatenated=outro_concatenated)

    return output_path
