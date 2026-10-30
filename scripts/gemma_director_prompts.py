"""Prompt texts for gemma_director. Everything here is generic (no reference-video content)."""
from __future__ import annotations

STANDING_RULES = """\
STANDING QUALITY RULES (hard requirements for the whole video):
- ONE continuous diagram per beat that builds with the narration (not a slideshow of separate cards).
- Every reveal is cued to the spoken word: use the word timings given (seconds from the start of the beat).
- Arrows DRAW ON (stroke-dasharray / stroke-dashoffset animation) and point where cause -> effect goes.
- Use the top ~83% of the 1080x1920 canvas (keep every element at y < 1594); leave no big empty areas.
- Key icons >= 120 px tall; labels >= 28 px.
- Canvas dark navy #0a192f, light text #ccd6f6, gold #d4af37 accent, green = good, red = bad.
- Only facts that appear in the script may appear on screen (no invented numbers, units, claims, certifications).
- The end card (brand + call to action) appears exactly once, in the last beat.
- Font: Arial (font-family: Arial, sans-serif) only.
"""

SB_SYSTEM = """\
You are the VISUAL DIRECTOR of a narrated explainer video. You write STORYBOARD.md: a short film plan in a strict
markdown format, one `## Frame N` block per narration beat, each containing a TIME-CODED shot sequence whose reveals
are cued to the exact spoken words. You write NO HTML and no code. Another team member (a frame builder) will turn your
Scene lines into animation, so your Scene lines must be concrete, visual, and specific: name the shapes, where they sit
on the 1080x1920 canvas (y ranges), their colours, what draws on, and which spoken word triggers each reveal.

Reply with the COMPLETE STORYBOARD.md inside ONE fenced block (```markdown ... ```) and nothing else.
"""

SB_FORMAT_EXAMPLE = """\
Tiny generic example of the shape (a different topic; do NOT reuse its content):

```markdown
---
format: 1080x1920
duration: 12s
message: "A savings account grows because interest earns interest"
arc: Hook -> Mechanism -> CTA
audience: first-time savers
mode: autonomous
---

## Video direction

**Palette.** ... (which hue means what)  **Motion grammar.** ... (reveal model, easing, what stays still)
**Rhythm.** ... (which beats are fast, which hold)  **Negative list.** ... (what never appears)

## Frame 1 — Hook

- scene: A coin stack tips over and a second stack grows beside it
- transition_in: cut
- type: hook
- blueprint: compose
- focal: the growing coin stack
- roles: coin stack = foreground subject (y 600-1400) · title = foreground (top) · baseline = supporting

Scene 1 (0.0-2.1s): Title "SAVINGS" (@0.00 "Imagine") fades up at y 160; a single gold coin drops onto a baseline (@1.10 "money") ...
Scene 2 (2.1-4.0s): A green arrow draws on from stack 1 to stack 2 (@2.40 "grows"), label "INTEREST" fades in as the arrow lands ...
```

Every reveal that depends on narration is written `(@seconds "word")` using the word@seconds table you are given.
"""

SB_RULES = """\
FORMAT RULES (a validator will check these and send you errors):
1. Frontmatter at the very top: format (1080x1920), duration, message, arc, audience, mode (autonomous).
2. Then ONE `## Video direction` block: palette system, motion grammar + reveal model, rhythm/held beats, negative list.
3. Then EXACTLY {n} frames, each headed `## Frame N — Title` (N = 1..{n}, in order), each with these bullets:
   `- scene:` one-line caption, `- transition_in:` (cut for Frame 1; later: crossfade | blur-crossfade | push-slide | zoom-through | squeeze, optional "0.4s"),
   `- type:`, `- blueprint: compose`, `- focal:` (the invented hero element), `- roles:` (each element = foreground subject / background / supporting).
   Do NOT write duration/voiceover/src/status bullets (the tooling pins them).
4. Then 4-8 lines `Scene k (a-bs): ...` per frame: windows contiguous from 0.0 to the frame duration, each window naming what is on screen,
   what moves, and where it sits (layout, y ranges, sizes in px, colours). Every reveal is cued `(@seconds "word")` with seconds copied from the
   word@seconds table (within 0.3 s). Nothing appears before the voiceover reaches it. Build finishes ~1.5 s before the beat ends, then HOLD.
5. Each beat = ONE continuous diagram that builds (not several unrelated cards). Show the MECHANISM the plan describes (what acts on what),
   with arrows that draw on from cause to effect. Short labels only (terms, never narration sentences).
6. Vary framing across beats; the end card appears exactly once (last beat).
"""

FRAME_DISPATCH = """\
DISPATCH CONTEXT
- PROJECT_DIR: (virtual) - you cannot run commands; just write the file.
- frame_id: {fid}   (use verbatim as the composition id, the window.__timelines key, and in id prefixes)
- canvas: 1080x1920. Captions: enabled, keep-out: all content at y < 1594 (bottom band belongs to the caption pill).
- This frame's duration is {dur}s of narration; set data-duration="{ddur}" on the <template>, the #root element and every full-length clip
  (the extra 0.4 s lets the next scene crossfade in). Your timeline must reveal pieces across 0..{dur}s and then hold still.
- Fonts: use `font-family: Arial, sans-serif` ONLY (frame.md names fonts that have no files in this project; ignore its font names).
- GSAP: put exactly this tag inside the template as the first line: <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
- No <audio>, no network fetches, no Math.random/Date.now, no repeat:-1, no exit tweens, no CSS transitions.
- Give EVERY element that is timed or animated a unique id that does NOT start with a digit (use the prefix `f{nn}-`, e.g. `f{nn}-arrow`). Style the root through `#root`.
- A layout/overflow checker runs after you: text must not be clipped by its box or by the canvas, and labels must not overlap each other.

{standing}

MINIMAL SKELETON (structure only; replace everything inside with your own design):
```html
<template id="{fid}-template" data-composition-id="{fid}" data-width="1080" data-height="1920" data-duration="{ddur}">
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<style>
  #root {{ position:absolute; left:0; top:0; width:1080px; height:1920px; overflow:hidden; font-family:Arial, sans-serif; }}
  #root .bg {{ position:absolute; left:0; top:0; width:1080px; height:1920px; background:#0a192f; }}
  #root .lab {{ position:absolute; color:#ccd6f6; font-size:40px; font-weight:700; }}
</style>
<div id="root" data-composition-id="{fid}" data-start="0" data-duration="{ddur}" data-width="1080" data-height="1920">
  <div id="f{nn}-bg" class="bg clip" data-start="0" data-duration="{ddur}" data-track-index="0"></div>
  <svg id="f{nn}-svg" class="clip" style="position:absolute;left:0;top:0" width="1080" height="1920" viewBox="0 0 1080 1920" data-start="0" data-duration="{ddur}" data-track-index="1">
    <path id="f{nn}-arrow" d="M200 600 L600 600" stroke="#d4af37" stroke-width="8" fill="none" stroke-dasharray="400" stroke-dashoffset="400"/>
  </svg>
  <div id="f{nn}-label" class="lab clip" style="left:80px;top:300px" data-start="0" data-duration="{ddur}" data-track-index="2">LABEL</div>
</div>
<script>
  window.__timelines = window.__timelines || {{}};
  const tl = gsap.timeline({{ paused: true }});
  tl.fromTo("#f{nn}-label", {{ opacity: 0, y: 30 }}, {{ opacity: 1, y: 0, duration: 0.5, ease: "power3.out" }}, 0.4);
  tl.to("#f{nn}-arrow", {{ strokeDashoffset: 0, duration: 0.7, ease: "power2.out" }}, 1.2);
  window.__timelines["{fid}"] = tl;
</script>
</template>
```
(`class="clip"` elements need data-start / data-duration / data-track-index. Elements inside the svg are animated via GSAP, not clips.)

OUTPUT: exactly ONE fenced ```html block containing the complete file (first bytes `<template`, last bytes `</template>`). No commentary.
"""

CRITIQUE = """\
You previously wrote the HTML frame below. It passed the automated structural checks. I rendered it and attached {k} snapshots,
in this order (each taken just before a Scene window ends, so the reveals of that Scene have landed):
{imgs}

Be a strict art director. For EACH image, state concretely: what is empty (big unused areas above y=1594 or inside the diagram),
overlapping or clipped (text over text, text running off the canvas or its box), too small (labels < 28 px, key icons < 120 px),
misaligned with the narration cue of that Scene, or not visibly mechanism-showing (is there a drawn arrow from cause to effect?
does it read as ONE continuous diagram?). Then return concrete edits and apply them.

THE SCENE PLAN (what the frame must show):
{scenes}

{standing}

Reply in this format:
CRITIQUE:
- (bullet per concrete defect, with image number and the element id to change)
REVISED FILE:
```html
<complete revised file, first bytes <template, last bytes </template>, keep ids/timeline registration, keep reveals cued to the same times>
```
If (and only if) there is nothing worth changing, reply `CRITIQUE: no changes` and omit the file.

CURRENT FILE:
```html
{html}
```
"""

REPAIR = """\
The file you wrote failed the automated checks. Fix EVERY finding below and return the complete corrected file
(same output format: one ```html block, first bytes `<template`, last bytes `</template>`). Keep the design and the timing; change only what the findings require.

FINDINGS:
{errors}

CURRENT FILE:
```html
{html}
```
"""
