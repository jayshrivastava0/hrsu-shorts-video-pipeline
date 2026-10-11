#!/usr/bin/env python
"""gemma_director - can free Gemma (Ollama) be the visual director of a HyperFrames explainer?

Pipeline (no Claude / paid API at run time):
  1 DESIGN      BRIEF.md (message distilled by Gemma) + build-frame.mjs + dark-colour fix
  2 STORYBOARD  Gemma writes STORYBOARD.md; validated + cue-alignment feedback, <=3 retries
  3 FRAMES      per frame: Gemma writes HTML; lint/check repair loop (<=4); snapshot critique (<=2)
  4 ASSEMBLE    captions + known fixes + index + transitions + lint/check + render

Usage:
  python scripts/gemma_director.py --project <videos/h2s-gemma> [--stages design,storyboard,frames,assemble]
         [--concurrency 2] [--force storyboard,frames] [--only-frames 1,2] [--no-render]
Stages are resumable: artifacts that already exist/validate are skipped unless --force.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gemma_director_prompts as P  # noqa: E402
import gemma_director_tools as T  # noqa: E402

log = T.log
SKILLS = T.SKILLS
SUMMARY: dict = {"stages": {}, "frames": {}}


# ------------------------------------------------------------------------ utils
@contextmanager
def step(name: str):
    t = time.time()
    log.info("=== STEP %s: start", name)
    st = {"ok": False}
    try:
        yield st
        st["ok"] = st.get("ok_override", True)
    except Exception as e:  # noqa: BLE001
        st["error"] = f"{type(e).__name__}: {e}"
        log.exception("STEP %s FAILED", name)
    finally:
        st["secs"] = round(time.time() - t, 1)
        SUMMARY["stages"][name] = {k: v for k, v in st.items() if k != "ok_override"}
        log.info("=== STEP %s: %s in %.1fs", name, "OK" if st.get("ok") else "FAILED", st["secs"])


def setup_logging(project: Path):
    log.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.FileHandler(project / "gemma_director.log", encoding="utf-8")
    fh.setFormatter(fmt)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)
    log.handlers[:] = [fh, sh]


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def script_blocks(project: Path) -> list[str]:
    txt = read(project / "user_script.txt")
    parts = re.split(r"^\[[a-z_0-9]+\][^\n]*\n", txt, flags=re.M)[1:]
    return [p.strip() for p in parts]


def voices(project: Path) -> list[dict]:
    return json.loads(read(project / "audio_meta.json"))["voices"]


def words_table(v: dict) -> str:
    ws = [f"{w['text']}@{w['start']:.2f}" for w in v["words"]]
    return "\n".join(" ".join(ws[i:i + 10]) for i in range(0, len(ws), 10))


def work(project: Path) -> Path:
    d = project / "gemma_work"
    d.mkdir(exist_ok=True)
    return d


def save_json_summary(project: Path):
    SUMMARY["ollama"] = {"calls": T.STATS.calls, "failures": T.STATS.failures, "retries": T.STATS.retries,
                         "by_stage": T.STATS.by_stage}
    SUMMARY["deterministic_fixes"] = T.STATS.deterministic_fixes
    (work(project) / "summary.json").write_text(json.dumps(SUMMARY, indent=1), encoding="utf-8")


# ------------------------------------------------------------------------ stage 1
DARK = {"bg": '"#0a192f"', "text": '"#ccd6f6"', "text-muted": '"#8892b0"', "primary": '"#d4af37"',
        "card-bg": '"rgba(10, 20, 40, 0.85)"', "border": '"rgba(212, 175, 55, 0.35)"'}


def dark_fix(frame_md: str) -> str:
    m = re.search(r"^colors:\n((?:[ \t]+.*\n)+)", frame_md, re.M)
    block = m.group(1)
    for k, v in DARK.items():
        block = re.sub(rf'^(\s+{re.escape(k)}:\s*).*$', lambda mm: mm.group(1) + v, block, flags=re.M)
    out = frame_md[:m.start(1)] + block + frame_md[m.end(1):]
    # preset fonts have no files in a faceless project; the workflow's captions + frames use Arial
    out = out.replace('"Space Grotesk"', '"Arial"').replace('"Inter"', '"Arial"')
    return out


def stage_design(project: Path, force: bool):
    brief = project / "BRIEF.md"
    if force or not brief.exists():
        script = read(project / "user_script.txt")
        msg = T.llm("design.message",
                    "You distill a narrated script into a one-sentence thesis for a video brief. Reply with the sentence only "
                    "(max 40 words, no quotes, no preamble).",
                    "SCRIPT:\n" + script, attempts=3, num_predict=400)
        msg = msg.strip().strip('"').splitlines()[0].replace('"', "'")
        total = round(sum(v["duration_s"] for v in voices(project)))
        brief.write_text(
            f'---\nworkflow: faceless-explainer\nflow: automation\nstoryboard: no\nmessage: "{msg}"\n'
            f'destination: shorts-tiktok\naspect: 1080x1920\nlanguage: en\nvoice_mode: verbatim\nlength: {total}s\n---\n\n'
            "## Notes\n- Brand: dark navy canvas, gold accent, HRSU INDORE wordmark. Only facts from user_script.txt on screen.\n",
            encoding="utf-8")
    if force or not (project / "frame.md").exists():
        rc, out = T.node_script("build-frame.mjs", ["--preset", "blue-professional", "--hyperframes", "."], project)
        if rc != 0:
            raise RuntimeError("build-frame failed: " + out[-400:])
        fm = dark_fix(read(project / "frame.md"))
        (project / "frame.md").write_text(fm, encoding="utf-8")
        T.STATS.fix("frame.md: dark colour fix + preset fonts -> Arial (known issue 1, scripted)")


# ------------------------------------------------------------------------ stage 2
def sb_user_prompt(project: Path) -> str:
    vs, sc = voices(project), script_blocks(project)
    plan = json.loads(read(project.parent.parent / "explanation_plan.json"))
    parts = ["SOURCE: the locked narration, split into beats, with the real word timings of the recorded voice-over.\n"]
    for i, (v, s) in enumerate(zip(vs, sc), 1):
        parts.append(f"### BEAT {i}  (duration {v['duration_s']}s)\nNarration: {s}\nWord@seconds table (seconds from the start of this beat):\n{words_table(v)}\n")
    parts.append("\nVERIFIED EXPLANATION PLAN (the facts you may show; beats 2-4 correspond to plan steps s1-s3, beat 1 is the question hook, beat 5 the payoff + call to action):")
    parts.append(f"Question: {plan['question']}")
    for st in plan["steps"]:
        parts.append(f"- step {st['step_id']}: {st['claim_text']}")
        for c in st["claims"]:
            parts.append(f"    claim [{c['role']}]: {c['text']}")
        for t in st.get("terms", []):
            parts.append(f"    term: {t['term']} = {t['definition']}")
        vi = st["visual_intent"]
        parts.append(f"    visual_intent: entities={vi['entities']}; relationship={vi['relationship']}; quantity={vi['quantity']}")
    parts.append(f"Payoff: {plan['payoff']['takeaway']}")
    parts.append("\nDESIGN TOKENS (frame.md colours, already dark-corrected):\n" + re.search(r"^colors:\n(?:[ \t]+.*\n)+", read(project / "frame.md"), re.M).group(0))
    parts.append(P.STANDING_RULES)
    parts.append(P.SB_RULES.format(n=len(vs)))
    return "\n".join(parts)


def stage_storyboard(project: Path, force: bool):
    sbp = project / "STORYBOARD.md"
    vs, sc = voices(project), script_blocks(project)
    if sbp.exists() and not force:
        errs, met = T.validate_storyboard(read(sbp), vs)
        if not errs:
            log.info("storyboard exists and validates; skipping")
            SUMMARY["storyboard"] = {"metrics": met, "retries": "skipped", "errors": []}
            return
    system = (P.SB_SYSTEM + "\n\n=== STORYBOARD FORMAT (reference doc) ===\n" + read(SKILLS / "hyperframes/references/storyboard-format.md")
              + "\n\n=== VISUAL DESIGN METHOD (reference doc) ===\n" + read(SKILLS / "faceless-explainer/references/visual-design.md")
              + "\n\n" + P.SB_FORMAT_EXAMPLE)
    user = sb_user_prompt(project)
    (work(project) / "storyboard_prompt.txt").write_text(system + "\n\n#### USER\n" + user, encoding="utf-8")
    history: list[dict] = []
    best = None
    all_errs: list[list[str]] = []
    for attempt in range(1, 5):  # first try + 3 retries
        resp = T.llm(f"storyboard.try{attempt}", system, user, history=history, num_predict=14000, temperature=0.7)
        (work(project) / f"storyboard_resp{attempt}.md").write_text(resp, encoding="utf-8")
        sb = T.extract_block(resp)
        errs, met = T.validate_storyboard(sb, vs)
        log.info("storyboard attempt %d: %d validator errors, cues %s/%s aligned", attempt, len(errs), met["aligned"], met["cues"])
        all_errs.append(errs)
        if best is None or len(errs) < best[0]:
            best = (len(errs), sb, met)
        if not errs:
            break
        if attempt < 4:
            history = history + [{"role": "user", "content": user}, {"role": "assistant", "content": resp}]
            user = ("The validator rejected your STORYBOARD.md. Fix every error and return the COMPLETE corrected file in one ```markdown block.\n\nERRORS:\n"
                    + "\n".join("- " + e for e in errs[:30]))
            # keep history short: only last exchange
            history = history[-2:]
    n_err, sb, met = best
    pinned = T.pin_storyboard(sb, vs, sc)
    T.STATS.fix("storyboard: pinned voiceover (exact script), duration (audio), src, status (not Gemma's decisions)")
    sbp.write_text(pinned, encoding="utf-8")
    rc, out = T.node_script("audio.mjs", ["sync-durations", "--audio-meta", "./audio_meta.json", "--storyboard", "./STORYBOARD.md"], project)
    log.info("sync-durations rc=%s %s", rc, out.strip()[-200:])
    SUMMARY["storyboard"] = {"metrics": met, "attempts": attempt, "final_validator_errors": all_errs[-1], "errors_per_attempt": [len(e) for e in all_errs]}
    if n_err:
        raise RuntimeError(f"storyboard still has {n_err} validator errors after {attempt} attempts (kept best, pinned)")


# ------------------------------------------------------------------------ stage 3
def frame_list(project: Path):
    sb = read(project / "STORYBOARD.md")
    frames = T.split_frames(sb)
    out = []
    for fr in frames:
        src = T.field(fr["block"], "src")
        fid = Path(src).stem
        out.append({"n": fr["n"], "id": fid, "path": project / src, "block": fr["block"]})
    return out


def placeholder(fid: str, ddur: float, reason: str) -> str:
    r = re.sub(r"[<>&\"']", " ", reason)[:160]
    return f"""<template id="{fid}-template" data-composition-id="{fid}" data-width="1080" data-height="1920" data-duration="{ddur}">
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<style>
  #root {{ position:absolute; left:0; top:0; width:1080px; height:1920px; overflow:hidden; font-family:Arial, sans-serif; }}
  #root .bg {{ position:absolute; left:0; top:0; width:1080px; height:1920px; background:#0a192f; }}
  #root .t {{ position:absolute; left:80px; width:920px; color:#ef4444; font-weight:700; }}
</style>
<div id="root" data-composition-id="{fid}" data-start="0" data-duration="{ddur}" data-width="1080" data-height="1920">
  <div id="f-bg" class="bg clip" data-start="0" data-duration="{ddur}" data-track-index="0"></div>
  <div id="ph-t1" class="t clip" style="top:600px;font-size:72px" data-start="0" data-duration="{ddur}" data-track-index="1">GEMMA FAILED</div>
  <div id="ph-t2" class="t clip" style="top:760px;font-size:40px;color:#ccd6f6" data-start="0" data-duration="{ddur}" data-track-index="2">{fid}: {r}</div>
</div>
<script>
  window.__timelines = window.__timelines || {{}};
  const tl = gsap.timeline({{ paused: true }});
  tl.fromTo("#ph-t1", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.4 }}, 0.1);
  window.__timelines["{fid}"] = tl;
</script>
</template>
"""


def clean_html(raw: str) -> str:
    h = T.extract_block(raw)
    a, b = h.find("<template"), h.rfind("</template>")
    if a > 0 or (b != -1 and b + len("</template>") < len(h.rstrip())):
        if a != -1 and b != -1:
            T.STATS.fix("frame html: stripped text outside <template>...</template>")
            h = h[a:b + len("</template>")]
    return h.strip()


def gate(project: Path, fr: dict, html: str, ddur: float):
    """precheck + `check` on one-frame scratch. Returns (errors, warnings, scratch)."""
    pre = T.html_precheck(html)
    if pre:
        return pre, [], None
    sc = T.make_scratch(project, fr["id"], fr["block"], html)
    errs, warns, _raw = T.parse_check(sc)
    return errs, warns, sc


def process_frame(project: Path, fr: dict, nframes: int, role: str, frame_md: str, packet: str) -> dict:
    fid, n = fr["id"], fr["n"]
    wd = work(project) / f"frame-{n:02d}"
    wd.mkdir(exist_ok=True)
    res_path = wd / "result.json"
    dur = voices(project)[n - 1]["duration_s"]
    ddur = round(dur + (0.4 if n < nframes else 0), 3)
    res = {"frame": n, "id": fid, "status": "pending", "repair_rounds": 0, "critique_rounds": 0,
           "critique_accepted": 0, "critique_reverted": 0, "errors_history": [], "ollama_calls": 0}
    t0 = time.time()
    calls0 = T.STATS.calls
    system = role
    base_user = (P.FRAME_DISPATCH.format(fid=fid, nn=f'{n:02d}', dur=dur, ddur=ddur, standing=P.STANDING_RULES)
                 + "\n\n=== YOUR FRAME PACKET ===\n" + packet + "\n\n=== frame.md (design truth) ===\n" + frame_md)
    try:
        # --- initial write
        raw = T.llm(f"frame{n}.write", system, base_user, num_predict=20000, temperature=0.6)
        (wd / "raw_0.txt").write_text(raw, encoding="utf-8")
        html = clean_html(raw)
        errs, warns, sc = gate(project, fr, html, ddur)
        res["errors_history"].append(len(errs))
        rounds = 0
        while errs and rounds < 4:
            rounds += 1
            log.info("frame %d repair round %d: %d findings: %s", n, rounds, len(errs), "; ".join(e[:90] for e in errs[:3]))
            raw = T.llm(f"frame{n}.repair{rounds}", system,
                        P.REPAIR.format(errors="\n".join("- " + e for e in errs[:25]), html=html),
                        num_predict=20000, temperature=0.4)
            (wd / f"raw_repair{rounds}.txt").write_text(raw, encoding="utf-8")
            html = clean_html(raw)
            errs, warns, sc = gate(project, fr, html, ddur)
            res["errors_history"].append(len(errs))
        res["repair_rounds"] = rounds
        (wd / "frame_v1.html").write_text(html, encoding="utf-8")
        if errs:
            res["status"] = "FAILED"
            res["failure"] = errs[:10]
            (wd / "failed_last.html").write_text(html, encoding="utf-8")
            fr["path"].write_text(placeholder(fid, ddur, f"{len(errs)} unresolved lint/check findings after 4 repair rounds: {errs[0][:80]}"), encoding="utf-8")
            T.STATS.fix(f"frame {n}: replaced failed frame with labelled placeholder")
        else:
            res["status"] = "PASSED_CHECK"
            res["warnings"] = len(warns)
            fr["path"].write_text(html, encoding="utf-8")
            # --- critique rounds (accept-or-revert)
            for cr in range(1, 3):
                times = T.scene_times(fr["block"], dur)
                imgs = T.snapshot(sc, [t for _k, t in times], wd / f"snaps_r{cr - 1}")
                if len(imgs) != len(times):
                    log.warning("frame %d: expected %d snapshots got %d; skipping critique", n, len(times), len(imgs))
                    break
                small = T.downscale(imgs)
                scenes = "\n".join(l for l in fr["block"].splitlines() if l.startswith("Scene"))
                imgdesc = "\n".join(f"Image {i + 1}: t={t}s (end of Scene {k})" for i, (k, t) in enumerate(times))
                raw = T.llm(f"frame{n}.critique{cr}", system,
                            P.CRITIQUE.format(k=len(small), imgs=imgdesc, scenes=scenes, standing=P.STANDING_RULES, html=html),
                            images=small, num_predict=20000, temperature=0.5)
                (wd / f"raw_critique{cr}.txt").write_text(raw, encoding="utf-8")
                res["critique_rounds"] = cr
                m = re.search(r"CRITIQUE:(.*?)(?:REVISED FILE:|```|$)", raw, re.S)
                res.setdefault("critiques", []).append((m.group(1).strip() if m else raw[:800])[:1500])
                if "```" not in raw:
                    log.info("frame %d critique %d: no changes proposed", n, cr)
                    break
                cand = clean_html(raw)
                e2, w2, sc2 = gate(project, fr, cand, ddur)
                if e2:
                    res["critique_reverted"] += 1
                    log.info("frame %d critique %d reverted (%d new errors: %s)", n, cr, len(e2), e2[0][:100])
                else:
                    res["critique_accepted"] += 1
                    html, sc = cand, sc2
                    fr["path"].write_text(html, encoding="utf-8")
                    (wd / f"frame_v{cr + 1}.html").write_text(html, encoding="utf-8")
            # final snapshots of the accepted version for the report
            times = T.scene_times(fr["block"], dur)
            T.snapshot(sc, [t for _k, t in times], wd / "snaps_final")
            res["status"] = "PASSED"
    except Exception as e:  # noqa: BLE001
        log.exception("frame %d crashed", n)
        res["status"] = "FAILED"
        res["failure"] = f"{type(e).__name__}: {e}"
        fr["path"].write_text(placeholder(fid, ddur, str(e)[:100]), encoding="utf-8")
        T.STATS.fix(f"frame {n}: replaced failed frame with labelled placeholder")
    res["secs"] = round(time.time() - t0, 1)
    res_path.write_text(json.dumps(res, indent=1), encoding="utf-8")
    log.info("FRAME %d done: %s", n, {k: res[k] for k in ("status", "repair_rounds", "critique_rounds", "critique_accepted", "secs")})
    return res


def stage_frames(project: Path, force: bool, conc: int, only: set[int] | None):
    rc, out = T.node_script("frame-packets.mjs", ["--project", str(project), "--storyboard", str(project / "STORYBOARD.md")], project)
    log.info("frame-packets rc=%s %s", rc, out.strip()[-300:])
    if rc != 0:
        raise RuntimeError("frame-packets rejected the storyboard: " + out[-500:])
    pk = project / ".hyperframes/frame-packets"
    role = read(pk / "_role.md")
    frame_md = read(project / "frame.md")
    frs = frame_list(project)
    todo = []
    for fr in frs:
        rp = work(project) / f"frame-{fr['n']:02d}" / "result.json"
        if only and fr["n"] not in only:
            continue
        if rp.exists() and not force and json.loads(read(rp)).get("status") in ("PASSED", "FAILED"):
            log.info("frame %d already has a result; skipping", fr["n"])
            continue
        todo.append(fr)
    (project / "compositions/frames").mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=conc) as ex:
        futs = [ex.submit(process_frame, project, fr, len(frs), role, frame_md, read(pk / f"{fr['id']}.md")) for fr in todo]
        for f in futs:
            f.result()
    for fr in frs:
        rp = work(project) / f"frame-{fr['n']:02d}" / "result.json"
        if rp.exists():
            SUMMARY["frames"][str(fr["n"])] = json.loads(read(rp))
    bad = [n for n, r in SUMMARY["frames"].items() if r["status"] != "PASSED"]
    if bad:
        raise RuntimeError(f"frames not passed: {bad}")


# ------------------------------------------------------------------------ stage 4
def stage_assemble(project: Path, render: bool):
    sb, am = "./STORYBOARD.md", "./audio_meta.json"
    rc, out = T.node_script("captions.mjs", ["build", "--storyboard", sb, "--audio-meta", am, "--hyperframes", ".", "--out", "./caption_groups.json"], project)
    log.info("captions build rc=%s %s", rc, out.strip()[-200:])
    cap = project / "compositions/captions.html"
    if rc != 0 or not cap.exists():
        raise RuntimeError("captions build failed: " + out[-300:])
    h = read(cap)
    h2 = re.sub(r"Space Grotesk|Inter(?=[\"',])", "Arial", h)
    if h2 != h:
        cap.write_text(h2, encoding="utf-8")
        T.STATS.fix("captions.html: font names -> Arial (known issue 2, scripted)")
    rc, out = T.node_script("assemble-index.mjs", ["--storyboard", sb, "--hyperframes", "."], project)
    log.info("assemble-index rc=%s %s", rc, out.strip()[-300:])
    if rc != 0:
        raise RuntimeError("assemble-index failed: " + out[-300:])
    rc, out = T.node_script("transitions.mjs", ["inject", "--storyboard", sb, "--hyperframes", "."], project)
    log.info("transitions inject rc=%s %s", rc, out.strip()[-300:])
    idx = project / "index.html"
    ih = read(idx)
    if 'data-track-kind="captions"' not in ih:
        ih2 = re.sub(r'(id="el-captions")', r'\1 data-track-kind="captions"', ih, count=1)
        idx.write_text(ih2, encoding="utf-8")
        T.STATS.fix('index.html: data-track-kind="captions" added (known issue 3, scripted)')
    rc, out = T.node_script("transitions.mjs", ["verify", "--storyboard", sb, "--index", "./index.html"], project)
    log.info("transitions verify rc=%s %s", rc, out.strip()[-300:])
    SUMMARY["transitions_verify_rc"] = rc
    errs, warns = T.parse_lint(project)
    log.info("FINAL LINT errors=%d warnings=%d", len(errs), len(warns))
    for e in errs[:20]:
        log.info("  lint error: %s", e[:200])
    cerr, cwarn, raw = T.parse_check(project)
    (work(project) / "final_check.json").write_text(raw, encoding="utf-8")
    real = [e for e in cerr if "#caption-word-" not in e and ".caption-line" not in e]
    log.info("FINAL CHECK errors=%d (non-caption %d) warnings=%d", len(cerr), len(real), len(cwarn))
    for e in cerr[:20]:
        log.info("  check error: %s", e[:220])
    SUMMARY["final"] = {"lint_errors": len(errs), "lint_warnings": len(warns), "check_errors": len(cerr),
                        "check_errors_non_caption": len(real), "check_warnings": len(cwarn),
                        "lint_error_text": errs[:10], "check_error_text": cerr[:10]}
    if errs:
        raise RuntimeError(f"final lint has {len(errs)} errors")
    if real:
        raise RuntimeError(f"final check has {len(real)} non-caption errors")
    if render:
        (project / "renders").mkdir(exist_ok=True)
        rc, out = T.hf(["render", "--skill=faceless-explainer", "--quality", "high", "--output", "renders/video.mp4"], project, 1500)
        log.info("render rc=%s %s", rc, out.strip()[-400:])
        if rc != 0 or not (project / "renders/video.mp4").exists():
            raise RuntimeError("render failed: " + out[-300:])


# ------------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--stages", default="design,storyboard,frames,assemble")
    ap.add_argument("--force", default="")
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--only-frames", default="")
    ap.add_argument("--no-render", action="store_true")
    a = ap.parse_args()
    project = Path(a.project).resolve()
    setup_logging(project)
    sj = work(project) / "summary.json"
    if sj.exists():
        try:
            prev = json.loads(read(sj))
            SUMMARY["stages"].update({k: v for k, v in prev.get("stages", {}).items()})
        except Exception:
            pass
    stages, force = a.stages.split(","), set(filter(None, a.force.split(",")))
    only = {int(x) for x in a.only_frames.split(",") if x}
    t0 = time.time()
    log.info("gemma_director start project=%s model=%s stages=%s", project, T.MODEL, stages)
    if "design" in stages:
        with step("design"):
            stage_design(project, "design" in force)
    if "storyboard" in stages:
        with step("storyboard"):
            stage_storyboard(project, "storyboard" in force)
    if "frames" in stages:
        with step("frames"):
            stage_frames(project, "frames" in force, a.concurrency, only or None)
    if "assemble" in stages:
        with step("assemble"):
            stage_assemble(project, not a.no_render)
    SUMMARY["total_secs_this_invocation"] = round(time.time() - t0, 1)
    save_json_summary(project)
    log.info("DONE total %.1fs, ollama calls=%d failures=%d retries=%d", time.time() - t0, T.STATS.calls, T.STATS.failures, T.STATS.retries)


if __name__ == "__main__":
    main()
