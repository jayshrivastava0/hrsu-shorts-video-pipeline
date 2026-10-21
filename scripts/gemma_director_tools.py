"""Helpers for gemma_director: Ollama wrapper with logging, plugin-script runners,
scratch-project builder, lint/check/snapshot parsing, storyboard validation.

No Claude / paid API is used anywhere; the only model is the Ollama-hosted Gemma.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

SKILLS = Path.home() / ".claude/plugins/cache/hyperframes/hyperframes/0.8.147/skills"
FE_SCRIPTS = SKILLS / "faceless-explainer/scripts"
HF = "hyperframes@0.8.147"
MODEL = os.environ.get("GEMMA_DIRECTOR_MODEL", "gemma4:31b-cloud")
# Local models need an explicit context window (Ollama's VRAM-based default can be 4096, which
# silently truncates the role packet) and a longer timeout. 0 = leave Ollama's default.
NUM_CTX = int(os.environ.get("GEMMA_DIRECTOR_NUM_CTX", "0"))
TIMEOUT = float(os.environ.get("GEMMA_DIRECTOR_TIMEOUT", "300"))

log = logging.getLogger("gemma_director")


# --------------------------------------------------------------------------- stats
class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.calls = 0
        self.failures = 0
        self.retries = 0
        self.by_stage: dict[str, dict] = {}
        self.timings: list[tuple[str, float]] = []
        self.deterministic_fixes: list[str] = []

    def call(self, stage, secs, ok, retry=False):
        with self.lock:
            self.calls += 1
            if not ok:
                self.failures += 1
            if retry:
                self.retries += 1
            s = self.by_stage.setdefault(stage, {"calls": 0, "fail": 0, "secs": 0.0})
            s["calls"] += 1
            s["fail"] += 0 if ok else 1
            s["secs"] += secs

    def fix(self, msg):
        with self.lock:
            self.deterministic_fixes.append(msg)
        log.info("DETERMINISTIC-FIX %s", msg)


STATS = Stats()


# --------------------------------------------------------------------------- LLM
_THINK = re.compile(r"<\|channel>thought\n.*?<channel\|>|<\|think\|>.*?<\|/think\|>", re.S)


class LLMError(RuntimeError):
    pass


def llm(stage: str, system: str, user: str, images: list[Path] | None = None,
        *, timeout: float | None = None, attempts: int = 3, num_predict: int = 16000,
        temperature: float = 0.7, history: list[dict] | None = None) -> str:
    """One logical Gemma call (with transport retries). Returns text content.
    Raises LLMError when every attempt stalls/fails."""
    import ollama
    client = ollama.Client(timeout=timeout or TIMEOUT)
    options = {"num_predict": num_predict, "temperature": temperature}
    if NUM_CTX:
        options["num_ctx"] = NUM_CTX
    msgs = [{"role": "system", "content": system}] + (history or [])
    um: dict = {"role": "user", "content": user}
    if images:
        um["images"] = [str(p) for p in images]
    msgs.append(um)
    last = None
    for a in range(1, attempts + 1):
        t = time.time()
        try:
            r = client.chat(model=MODEL, messages=msgs,
                            options=options)
            txt = _THINK.sub("", r.message.content or "").strip()
            dt = time.time() - t
            if not txt:
                raise LLMError("empty response")
            STATS.call(stage, dt, True, retry=a > 1)
            log.info("OLLAMA stage=%s attempt=%d ok %.1fs in=%dch out=%dch imgs=%d",
                     stage, a, dt, len(system) + len(user), len(txt), len(images or []))
            return txt
        except Exception as e:  # noqa: BLE001
            dt = time.time() - t
            last = e
            STATS.call(stage, dt, False, retry=a > 1)
            log.warning("OLLAMA stage=%s attempt=%d FAILED after %.1fs: %s", stage, a, dt,
                        str(e)[:200])
            time.sleep(5 * a)
    raise LLMError(f"{stage}: all {attempts} attempts failed: {last}")


def extract_block(text: str, lang: str | None = None) -> str:
    """Pull the largest fenced block (```html / ```markdown / ```) else the text."""
    blocks = re.findall(r"```[a-zA-Z]*\n(.*?)```", text, re.S)
    if blocks:
        return max(blocks, key=len).strip()
    # unterminated fence (truncation): take after first fence
    m = re.search(r"```[a-zA-Z]*\n(.*)$", text, re.S)
    if m:
        return m.group(1).strip()
    return text.strip()


# --------------------------------------------------------------------------- process
def run(cmd: list[str] | str, cwd: Path, timeout: int = 600, env: dict | None = None):
    t = time.time()
    e = {**os.environ, "HYPERFRAMES_SKIP_SKILLS": "1", **(env or {})}
    p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout, env=e, shell=isinstance(cmd, str),
                       stdin=subprocess.DEVNULL)
    out = (p.stdout or "") + (p.stderr or "")
    log.debug("RUN %s -> %d (%.1fs)", cmd if isinstance(cmd, str) else " ".join(cmd), p.returncode,
              time.time() - t)
    return p.returncode, out


def node_script(name: str, args: list[str], cwd: Path, timeout=300):
    return run(["node", str(FE_SCRIPTS / name)] + args, cwd, timeout)


def hf(args: list[str], cwd: Path, timeout=600):
    npx = shutil.which("npx.cmd") or shutil.which("npx") or "npx"
    return run([npx, "--yes", HF] + args, cwd, timeout)


# --------------------------------------------------------------------------- lint/check
def parse_lint(project: Path):
    """Returns (errors, warnings) lists of strings from `lint --json`."""
    rc, out = hf(["lint", "--json"], project, 180)
    m = re.search(r"\{.*\}", out, re.S)
    errs, warns = [], []
    if not m:
        return [f"lint produced no JSON (rc={rc}): {out[:400]}"], []
    try:
        d = json.loads(m.group(0))
    except Exception:
        return [f"lint JSON unparsable: {out[:400]}"], []
    for f in d.get("findings", d.get("issues", [])):
        line = f"[{f.get('code')}] {f.get('message')}" + (
            f" (selector {f.get('selector')})" if f.get("selector") else "") + (
            f" fix: {f.get('fixHint')}" if f.get("fixHint") else "")
        (errs if f.get("severity") == "error" else warns).append(line)
    return errs, warns


def parse_check(project: Path):
    """`check` output -> (errors, warnings, raw)."""
    rc, out = hf(["check", "--json"], project, 300)
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        txt = out
        errs = [l.strip() for l in txt.splitlines() if re.search(r"error|✗", l, re.I)][:15]
        return errs, [], out
    try:
        d = json.loads(m.group(0))
    except Exception:
        return [f"check JSON unparsable: {out[:300]}"], [], out
    errs, warns = [], []

    def walk(o):
        if isinstance(o, dict):
            if "severity" in o and ("message" in o or "code" in o):
                line = f"[{o.get('code') or o.get('rule')}] {o.get('message')}"
                for k in ("selector", "containerSelector", "text", "time", "coveredFraction", "bbox"):
                    if o.get(k) not in (None, ""):
                        line += f" | {k}={o.get(k)}"
                if o.get("fixHint"):
                    line += f" | FIX: {o['fixHint']}"
                (errs if o["severity"] == "error" else warns).append(line)
            else:
                for v in o.values():
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(d)
    return errs, warns, out


# --------------------------------------------------------------------------- scratch
def make_scratch(project: Path, frame_id: str, block: str, frame_html: str,
                 root: Path | None = None) -> Path:
    """One-frame mini project so lint/check/snapshot run on this frame alone,
    starting at t=0 (snapshot times are frame-relative)."""
    base = (root or Path(tempfile.gettempdir()) / "gemma_director") / (project.name + "-" + frame_id)
    if base.exists():
        shutil.rmtree(base, ignore_errors=True)
    (base / "compositions/frames").mkdir(parents=True)
    for f in ("hyperframes.json", "package.json", "meta.json", "frame.md", "index.html"):
        if (project / f).exists():
            shutil.copy(project / f, base / f)
    (base / ".hyperframes").mkdir(exist_ok=True)
    (base / "compositions/frames" / f"{frame_id}.html").write_text(frame_html, encoding="utf-8")
    # one-frame storyboard: pinned block with hard cut
    b = re.sub(r"^## Frame\s+\d+", "## Frame 1", block, flags=re.M)
    b = re.sub(r"^- transition_in:.*$", "- transition_in: cut", b, flags=re.M)
    (base / "STORYBOARD.md").write_text(
        "---\nformat: 1080x1920\nduration: 10s\nmessage: scratch\narc: x\naudience: x\nmode: autonomous\n---\n\n"
        + b + "\n", encoding="utf-8")
    rc, out = node_script("assemble-index.mjs", ["--storyboard", "./STORYBOARD.md",
                                                  "--hyperframes", "."], base, 60)
    if rc != 0:
        log.warning("scratch assemble rc=%s %s", rc, out[:300])
    return base


def scene_times(block: str, duration: float, maxn: int = 6) -> list[tuple[int, float]]:
    """(scene number, snapshot time) pairs: just before each Scene window ends."""
    pairs = []
    for m in re.finditer(r"^Scene\s+(\d+)\s*\(\s*([\d.]+)\s*[-–—]\s*([\d.]+)\s*s?\s*\)", block, re.M):
        pairs.append((int(m.group(1)), round(max(0.3, min(float(m.group(3)) - 0.15, duration - 0.1)), 2)))
    if not pairs:
        pairs = [(k, round(duration * k / 5, 2)) for k in range(1, 6)]
    seen, uniq = set(), []
    for k, t in pairs:
        if t not in seen:
            seen.add(t)
            uniq.append((k, t))
    while len(uniq) > maxn:
        uniq.pop(len(uniq) // 2)
    return uniq


def snapshot(scratch: Path, times: list[float], out: Path) -> list[Path]:
    if out.exists():
        shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    rc, o = hf(["snapshot", "--at", ",".join(str(t) for t in times), "--no-end",
                "--output", str(out)], scratch, 300)
    imgs = sorted(out.glob("*.png"))
    if rc != 0 or not imgs:
        log.warning("snapshot rc=%s imgs=%d out=%s", rc, len(imgs), o[-400:])
    return imgs


def downscale(imgs: list[Path], width=540) -> list[Path]:
    from PIL import Image
    out = []
    for p in imgs:
        im = Image.open(p).convert("RGB")
        h = int(im.height * width / im.width)
        q = p.with_name(p.stem + "_s.jpg")
        im.resize((width, h)).save(q, quality=82)
        out.append(q)
    return out


# --------------------------------------------------------------------------- storyboard
ALLOWED_TRANSITIONS = ("cut", "crossfade", "blur-crossfade", "push-slide", "zoom-through", "squeeze")
FM_KEYS = ("format", "duration", "message", "arc", "audience", "mode")
FRAME_REQ = ("scene", "duration", "transition_in", "voiceover", "type", "blueprint", "focal", "roles",
             "src", "status")


def slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return "-".join(s.split("-")[:3]) or "frame"


def split_frames(sb: str):
    ms = list(re.finditer(r"^## Frame\s+(\d+)\s*[—–-]\s*(.*)$", sb, re.M))
    out = []
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(sb)
        out.append({"n": int(m.group(1)), "title": m.group(2).strip(), "block": sb[m.start():end].strip(),
                    "start": m.start(), "end": end})
    return out


def field(block: str, name: str):
    m = re.search(rf"^-\s+{name}:\s*(.+)$", block, re.M | re.I)
    return m.group(1).strip() if m else None


def set_field(block: str, name: str, value: str) -> str:
    line = f"- {name}: {value}"
    if re.search(rf"^-\s+{name}:.*$", block, re.M | re.I):
        return re.sub(rf"^-\s+{name}:.*$", lambda _m: line, block, count=1, flags=re.M | re.I)
    # insert after heading
    parts = block.split("\n", 1)
    return parts[0] + "\n\n" + line + ("\n" + parts[1].lstrip("\n") if len(parts) > 1 else "")


def pin_storyboard(sb: str, voices: list[dict], script_blocks: list[str]) -> str:
    """Overwrite the fields that are NOT Gemma's to decide: voiceover (exact script),
    src, status, duration. Logged as deterministic tooling."""
    frames = split_frames(sb)
    new = sb
    # work back to front so offsets stay valid
    for fr, v, sc in reversed(list(zip(frames, voices, script_blocks))):
        b = fr["block"]
        n = fr["n"]
        b = set_field(b, "duration", f"{v['duration_s']}s")
        b = set_field(b, "voiceover", json.dumps(sc, ensure_ascii=False))
        b = set_field(b, "src", f"compositions/frames/{n:02d}-{slug(fr['title'])}.html")
        b = set_field(b, "status", "outline")
        new = new[:fr["start"]] + b + "\n\n" + new[fr["end"]:]
    return new.rstrip() + "\n"


def word_starts(voice: dict) -> list[float]:
    return [w["start"] for w in voice["words"]]


def validate_storyboard(sb: str, voices: list[dict]) -> tuple[list[str], dict]:
    """Deterministic validation + cue-alignment score. Returns (errors, metrics)."""
    errs: list[str] = []
    m = re.match(r"^---\n(.*?)\n---\n", sb, re.S)
    if not m:
        errs.append("Missing YAML frontmatter block at the very top (--- ... ---).")
    else:
        for k in FM_KEYS:
            if not re.search(rf"^{k}:\s*\S", m.group(1), re.M):
                errs.append(f"Frontmatter is missing key `{k}:`.")
        if not re.search(r"^format:\s*1080x1920", m.group(1), re.M):
            errs.append("Frontmatter `format:` must be 1080x1920.")
    if not re.search(r"^## Video direction\s*$", sb, re.M):
        errs.append("Missing a `## Video direction` heading (written once, before Frame 1).")
    else:
        vd = sb.split("## Video direction", 1)[1].split("\n## Frame", 1)[0]
        for kw in ("palette", "motion", "negative"):
            if kw not in vd.lower():
                errs.append(f"`## Video direction` must cover `{kw}` (palette / motion grammar / rhythm / negative list).")
    frames = split_frames(sb)
    if len(frames) != len(voices):
        errs.append(f"Need exactly {len(voices)} frames headed `## Frame N — Title` (found {len(frames)}).")
    aligned = total = 0
    for i, fr in enumerate(frames):
        b, n = fr["block"], fr["n"]
        if n != i + 1:
            errs.append(f"Frame numbering must be 1..N in order (got Frame {n} at position {i+1}).")
        for k in FRAME_REQ:
            if k in ("duration", "voiceover", "src", "status"):
                continue  # pinned by tooling
            if not field(b, k):
                errs.append(f"Frame {n}: missing `- {k}:` bullet.")
        tr = field(b, "transition_in") or ""
        if tr and tr.split()[0].lower() not in ALLOWED_TRANSITIONS:
            errs.append(f"Frame {n}: transition_in `{tr}` must start with one of {', '.join(ALLOWED_TRANSITIONS)}.")
        if i == 0 and tr and tr.split()[0].lower() != "cut":
            errs.append("Frame 1: transition_in must be `cut` (nothing precedes it).")
        bp = (field(b, "blueprint") or "").strip().lower()
        if bp and not bp.startswith("compose"):
            errs.append(f"Frame {n}: `blueprint:` must be `compose` (no blueprint library is available).")
        if i >= len(voices):
            continue
        dur = voices[i]["duration_s"]
        scenes = re.findall(r"^Scene\s+(\d+)\s*\(\s*([\d.]+)\s*[-–—]\s*([\d.]+)\s*s?\s*\)\s*:(.*)$", b, re.M)
        if len(scenes) < 3:
            errs.append(f"Frame {n}: needs at least 3 time-coded `Scene k (a-bs): ...` lines (found {len(scenes)}).")
        prev_end = 0.0
        ws = word_starts(voices[i])
        for (k, a, e, body) in scenes:
            a, e = float(a), float(e)
            if abs(a - prev_end) > 0.6:
                errs.append(f"Frame {n} Scene {k}: starts at {a}s but the previous window ended at {prev_end}s (windows must be contiguous).")
            if e <= a:
                errs.append(f"Frame {n} Scene {k}: window end must be after start.")
            prev_end = e
            for tm in re.findall(r"@\s*([\d.]+)", body):
                t = float(tm)
                total += 1
                near = min(abs(t - w) for w in ws)
                if t > dur + 0.05:
                    errs.append(f"Frame {n} Scene {k}: cue @{tm} is beyond the frame duration {dur}s.")
                elif near <= 0.3:
                    aligned += 1
                else:
                    errs.append(f"Frame {n} Scene {k}: cue @{tm} is not within 0.3s of any spoken word start "
                                f"(nearest word start is {min(ws, key=lambda w: abs(w - t))}). Use the word@seconds table.")
        if scenes and prev_end < dur - 3.0:
            errs.append(f"Frame {n}: Scene windows end at {prev_end}s but the frame lasts {dur}s - extend the shot across the full duration.")
        if scenes and prev_end > dur + 1.0:
            errs.append(f"Frame {n}: Scene windows run to {prev_end}s, beyond the frame duration {dur}s.")
        if total == 0 and scenes:
            errs.append(f"Frame {n}: no `@seconds` cues found. Cite each reveal as `(word @12.34)` using the word timings.")
    metrics = {"cues": total, "aligned": aligned, "frames": len(frames)}
    return errs, metrics


# --------------------------------------------------------------------------- HTML sanity
def html_precheck(html: str) -> list[str]:
    """Cheap structural checks before spending a lint round."""
    errs = []
    h = html.strip()
    if not h.startswith("<template"):
        errs.append("Output must start with `<template` (no doctype/html/head/body, no preamble).")
    if not h.endswith("</template>"):
        errs.append("Output must end with `</template>` - the response was probably truncated or has trailing text; "
                    "make the file shorter/simpler so it fits.")
    if h.count("<template") != 1:
        errs.append("Exactly one <template> element is allowed.")
    return errs
