# HRSU Vertical Short-Form Video Pipeline — Implementation Plan, Part 3

> **Continuation of** `2026-05-03-video-pipeline-implementation-part-2.md`.
> **Authored:** 2026-05-09, after first end-to-end smoke run revealed quality issues.

This file amends the plan based on real output observed in the first smoke video. It does **not** replace Parts 1 or 2 — it adds a new **Sprint 4.5** (bugfix sprint for already-shipped code) and **expands Sprint 5** with a source-extractor strategy that better fits HRSU's "technical authority via real references" positioning.

**Spec impact:** §4.5.3 (`stock.py`) is **deprioritized**. Pexels stock photos clash with the technical-illustration style choice in spec §1.4. Source-extractor (research-paper screenshots, blog hero images, citation PDFs) replaces it as the primary visual fallback. `stock.py` may still be implemented as a last-resort but moves to Sprint 8.

---

## Why this part exists — observations from the smoke run

After Sprint 4 produced the first playable MP4, the following defects were observed:

| # | Symptom | Root cause |
|---|---------|------------|
| 1 | Every infographic scene fell back to a plain text card | The LLM emitted semantically-named keys (`"h2s_reduction": "90%"`) instead of canonical (`"value": "90%"`). The dispatcher's `_normalize_chart_data` was too strict and rejected unknown shapes. |
| 2 | "Key Stats" gold text drifted off-frame | Ken Burns pan was being applied to **text cards**. Text is centered for a stationary 1080×1920 viewport — any pan crops it. Ken Burns should apply only to photographs and footage. |
| 3 | Burned-in subtitles invisible / cropped at top | `:original_size=1080x1920` parameter on ffmpeg's `subtitles` filter doesn't fully override libass default `PlayResY` on Windows libass builds. Switch from SRT+`force_style` to embedded ASS where we control `PlayResX`/`PlayResY` directly in the script header. |
| 4 | Voiceover spoke stage directions like "Visual: close-up of murky water…" | Already partially fixed in `_scrub_banned`. The stricter prompt is in place. Adding a second-pass validator catches anything that slips through. |
| 5 | The video was 90% navy text cards even with non-text scenes | `stock` and `hrsu_edge` both fall back to text cards because no clip library exists yet. The user has rejected generic stock libraries — instead, the pipeline must **source images from the source blog itself**: inline `<img>`s, citation PDFs (rendered to images via PyMuPDF), and authority links. |

Premature Sprint-5 work that already shipped (must be reconciled, not duplicated):

- `video_agent/visual_engine/footage_library.py` — a minimal version of `factory_broll.py` (Sprint 5 Task 22). Will be **superseded** by the proper `factory_broll.py` + `asset_manifest.py`. Plan: keep the manifest format, rename the module, wire into the new `asset_manifest.py` schema.
- `_normalize_chart_data` in `dispatcher.py` — partial coercion. Will be **rewritten** in Task 38 below to accept arbitrary key shapes.
- `video_agent/config.py` — adds `HF_HOME`/`TORCH_HOME`/`MPLCONFIGDIR`/`TMP` redirects to `.cache/` on the project drive. Undocumented in original plan; capture in `VIDEO_SETUP.md` (Sprint 8 Task 36 amendment).

---

## Sprint Map (revised from this point forward)

| Sprint | Tasks | Status | Outcome |
|--------|-------|--------|---------|
| 1–3 | 1–15 | ✅ shipped | script + voice + subs + visual primitives |
| **4** | 16–20 | ✅ shipped (with bugs in 38–41) | composer renders an MP4 |
| **4.5 (new)** | **38–41** | ⏳ next | quality bugs from smoke run fixed |
| 5 | 21–24 + **42–45 (new)** | ⏳ | factory_broll + **source_extractor** + chart prompt + tag tool |
| 6 | 25–29 | ⏳ unchanged | publishers/youtube + scheduler + main.py CLI |
| 7 | 30–32 | ⏳ unchanged | publishers/linkedin + token_manager |
| 8 | 33–37 + **46 (new)** | ⏳ | publishers/instagram + cdn + cleanup + blog hook + VIDEO_SETUP + **stock.py (deferred)** |

---

## Sprint 4.5 — Visual quality bugfixes (composer + dispatcher + script)

Goal: eliminate the four defects observed in the first smoke run. No new features.

### Task 38: Fuzzy chart-data normalization

**Problem.** The LLM produces arbitrary key names. Today's `_normalize_chart_data` only accepts canonical keys (`value`, `labels`/`values`) plus a few aliases. Result: ~80% of intended infographics fall back to text cards.

**Goal.** A normalizer that extracts (label, number, raw_string) tuples from any shape `{key: stringified_value_with_unit}`, then maps to whatever the chart renderer needs.

**Files:**
- Modify: `video_agent/visual_engine/dispatcher.py`
- Modify: `tests/video_agent/visual_engine/test_dispatcher_coerce.py`

- [ ] **Step 1: Update tests with the new shapes the LLM actually produces**

```python
def test_callout_accepts_arbitrary_first_numeric_key():
    # The LLM emits {"h2s_reduction": "90%", "volume": "1 liter", "concentration": "50 mg/L"}
    data = {"h2s_reduction": "90%", "volume": "1 liter", "concentration": "50 mg/L"}
    out = _normalize_chart_data("callout_stat", data)
    assert out is not None
    assert "90" in out["value"]
    # Label is humanized from the key name.
    assert "reduction" in out["label"].lower()


def test_bar_extracts_pairs_from_arbitrary_keys():
    data = {"chemical_cost_savings": "15%", "industry_impact": "1 Billion AUD"}
    out = _normalize_chart_data("bar", data)
    assert out is not None
    assert len(out["values"]) == 2
    assert out["labels"][0] == "Chemical Cost Savings"


def test_callout_rejects_when_no_numeric_value_anywhere():
    assert _normalize_chart_data("callout_stat", {"foo": "bar"}) is None
```

- [ ] **Step 2: Implement helper `_extract_pairs`**

```python
def _extract_pairs(data: dict) -> list[tuple[str, float, str]]:
    """Return (humanized_label, parsed_number, raw_string) for every numeric value."""
    pairs = []
    for k, v in data.items():
        if not isinstance(v, (str, int, float)):
            continue
        num = _coerce_number(v)
        if num is None:
            continue
        label = re.sub(r"[_\-]+", " ", str(k)).strip().title()
        pairs.append((label, num, str(v)))
    return pairs
```

- [ ] **Step 3: Rewrite each chart-type branch in `_normalize_chart_data`**

For `callout_stat`: prefer canonical `value`/`label`. Else take the first pair from `_extract_pairs`. Use the raw string (`"90%"`) as `value` so units are preserved.

For `bar`: prefer canonical `labels`/`values`. Else take all pairs (≥2). Use `[label for label,…]` and `[num for …]`.

For `comparison`: prefer canonical `left_value`/`right_value`. Else take the first 2 pairs.

For `flow`: unchanged (already accepts `steps` list).

For `line`: unchanged.

- [ ] **Step 4: Test → green → commit**

```
git commit -m "fix(video_agent): fuzzy chart-data normalizer accepts arbitrary keys"
```

---

### Task 39: Sharper scene-breakdown prompt

**Problem.** The LLM is technically free to emit non-canonical keys; the prompt doesn't penalize it. Even with Task 38's fuzzy normalizer, canonical keys produce more predictable visuals.

**Files:**
- Modify: `video_agent/script_builder.py` (`_scene_breakdown` system prompt)

- [ ] **Step 1: Tighten the system prompt**

Replace the `visual_spec` instructions with:

```
visual_spec must use these exact keys per chart_type:
  bar:           {"labels": [str, ...], "values": [number, ...]}
  callout_stat:  {"value": "90%", "label": "H2S reduction"}
  comparison:    {"left_label": str, "left_value": str, "right_label": str, "right_value": str}
  flow:          {"steps": [str, str, ...]}
  line:          {"x": [number, ...], "y": [number, ...]}
Do NOT invent custom key names. Numbers must include their unit ("90%", "50 mg/L").
```

- [ ] **Step 2: Add validation in `_post_process_scenes`**

For each scene with `visual_type == "infographic"`, run the same `_normalize_chart_data` from Task 38; if it returns None, downgrade the scene to `text_card` with `layout: "hook"` and the on_screen_text. This prevents silent fallback at render time.

- [ ] **Step 3: Test + commit**

```
git commit -m "feat(video_agent): canonical visual_spec keys + pre-validate at script time"
```

---

### Task 40: Static text cards (Ken Burns only on photos/footage)

**Problem.** `_render_main_clip` applies Ken Burns to every non-video clip, including text cards. Text is rendered for a stationary viewport, so any pan crops it.

**Files:**
- Modify: `video_agent/composer.py`

- [ ] **Step 1: Add `is_static` hint to visual results**

Update `dispatcher.py` to set `"is_static": True` in the result dict for `text_card` and `source_card` (Task 43); leave it False or absent for `infographic`, `factory_broll`, `source_image`, and any future photo/footage type.

- [ ] **Step 2: Branch in `_render_main_clip`**

```python
if vis.get("is_static") or vis["is_video_clip"]:
    # Static image, no pan. Plain ImageClip at fixed (w, h).
    c = ImageClip(str(vis["asset_path"])).set_duration(dur).resize((w, h))
elif vis.get("is_video_clip"):
    # Existing footage path.
    ...
else:
    # Photo / source image / infographic → apply Ken Burns.
    c = _ken_burns_clip(Path(vis["asset_path"]), dur, w, h, KEN_BURNS_ZOOM_END)
```

(Infographics also benefit from a gentle pan — the chart fills the frame and the motion adds life. But callout_stat is text-heavy; consider adding `is_static` for callout_stat too. Use a config flag `STATIC_INFOGRAPHIC_TYPES = {"callout_stat"}`.)

- [ ] **Step 3: Test + commit**

Add a test asserting `is_static=True` for text_card scenes and `False` (or absent) for infographic/source scenes. Re-run smoke; verify "Key Stats" no longer drifts.

```
git commit -m "fix(video_agent): static rendering for text cards; Ken Burns only on photos/charts"
```

---

### Task 41: Subtitles via embedded ASS (not SRT + force_style)

**Problem.** On Windows libass, `:original_size=1080x1920:force_style='Alignment=2,MarginV=260'` does not fully override the script's `PlayResX`/`PlayResY`, so subtitles render at the wrong position and oversized. Also, `force_style` cannot affect properties like `WrapStyle` reliably.

**Goal.** Compose subtitles as a proper ASS file with a controlled `[Script Info]` header so positioning is deterministic.

**Files:**
- Modify: `video_agent/composer.py`

- [ ] **Step 1: Add `_srt_to_ass(srt_path, ass_path, w, h)` helper**

Generate an ASS document with:

```
[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Poppins,62,&H00FFFFFF,&H00000000,&H80000000,1,1,5,0,2,60,60,260,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:01.46,Default,,0,0,0,,VISUAL CLOSE-UP
…
```

Convert the SRT cues directly into `Dialogue:` lines. Timestamps: `H:MM:SS.cc` (centiseconds, not milliseconds).

- [ ] **Step 2: Update `_burn_subtitles` to write ASS into the workspace, then call ffmpeg with the ASS filter**

```python
def _burn_subtitles(input_mp4, srt_path, output_mp4):
    ass_path = output_mp4.parent / "subs.ass"
    _srt_to_ass(srt_path, ass_path, *SHORT_FORMAT["resolution"])
    ass_str = str(ass_path).replace("\\", "/").replace(":", r"\:")
    vf = f"ass='{ass_str}'"
    _ffmpeg([... "-vf", vf, ...])
```

`subtitles` filter and `ass` filter both work on Windows; `ass` skips force_style entirely.

- [ ] **Step 3: Test + commit**

Add `test_srt_to_ass_emits_valid_header` and `test_srt_to_ass_converts_cues`. Re-run smoke; verify subtitles appear at bottom-center, readable size, no top-bleed.

```
git commit -m "fix(video_agent): subtitles via embedded ASS for deterministic positioning"
```

---

### Sprint 4.5 acceptance

- [ ] Tasks 38–41 all green.
- [ ] Re-run `scripts/smoke_video.py`. Output should now contain ≥1 callout_stat or bar chart that renders, text cards stay centered, subtitles are readable at the bottom.
- [ ] Tag: `git tag video-agent-sprint-4.5`.

---

## Sprint 5 — Real footage + source-extractor + chart prompt (revised)

Tasks 21–24 from Part 2 stay as-is. **Four new tasks (42–45)** are inserted, and **Task 22 (`factory_broll.py`) is amended** to subsume the prematurely-shipped `footage_library.py`.

### Task 22 amendment: rename `footage_library.py` → `factory_broll.py`

**Problem.** Sprint 4 prematurely shipped `video_agent/visual_engine/footage_library.py` with a basic manifest matcher. The Sprint 5 plan calls for `factory_broll.py` with the same intent but a richer scoring model (category, persona, tags, recency, used-set deduplication).

**Migration:**
1. Rename `footage_library.py` → `factory_broll.py`. Move logic into the structure described in Part 2 Task 22.
2. Move the user-facing manifest from `asset_library/footage/manifest.json` → `asset_library/factory/manifest.json` per spec §3.
3. Update `dispatcher.py` import from `footage_library` to `factory_broll`.
4. Update tests.
5. Document the migration in `VIDEO_SETUP.md`.

Commit:
```
git commit -m "refactor(video_agent): footage_library → factory_broll (matches Sprint 5 design)"
```

---

### Task 42: TDD `source_extractor.py` — per-blog image and PDF mining

**Goal.** For each blog the pipeline processes, parse `content_html` and citation links to assemble a per-blog image library: inline images, hero images, and rendered first-pages of cited PDFs (research papers, regulatory docs, technical sheets). This becomes the primary "real visual" source — replacing generic stock and establishing technical authority implicitly.

**Files:**
- Create: `tests/video_agent/visual_engine/test_source_extractor.py`
- Create: `video_agent/visual_engine/source_extractor.py`

**Dependencies:** `pip install pymupdf beautifulsoup4` (both pure-Python wheels).

- [ ] **Step 1: Public API contract**

```python
def extract_blog_sources(blog_record: dict, cache_dir: Path) -> list[dict]:
    """
    Returns a manifest of source images mined from this blog. Each entry:
      {
        "id":           "blog123_img2",
        "path":         Path("asset_library/blog_sources/blog123/img2.jpg"),
        "source_type":  "inline_image" | "hero_image" | "pdf_page" | "html_screenshot",
        "source_url":   "https://example.gov/paper.pdf",
        "caption":      str (alt text or surrounding paragraph or PDF first-line),
        "tokens":       set[str] (used by find_source_for_scene),
        "is_authority": bool (.gov / .edu / arxiv.org / pubmed / doi.org),
      }
    Cached at cache_dir/<blog_id>/. Re-uses cache on subsequent runs.
    """


def find_source_for_scene(scene: dict, sources: list[dict]) -> dict | None:
    """Score sources by token overlap with scene narration + on_screen_text + visual_spec.query.
    Returns highest scorer or None if all below MIN_SCORE."""
```

- [ ] **Step 2: Helpers**

- `_parse_html_images(html, base_url) -> list[(src_url, alt, surrounding_text)]`
  Parse with BeautifulSoup, resolve relative URLs against `base_url`, drop SVGs, drop tiny icons (filename heuristics: `logo`, `icon`, `favicon`, `sprite`).
- `_extract_authority_links(html) -> list[(url, link_text)]`
  Find `<a href>` matching `\.pdf$|doi\.org|arxiv\.org|pubmed\.ncbi|\.gov/|\.edu/|sciencedirect\.com|nature\.com`.
- `_download(url, dest, timeout=30) -> bool` — `requests.get`, write to dest, return success.
- `_render_pdf_first_pages(pdf_path, out_dir, max_pages=2) -> list[Path]` — PyMuPDF `fitz.open(pdf_path)`, render `page.get_pixmap(dpi=150)` → PNG.
- `_should_skip_image(path) -> bool` — skip <300×300, skip aspect ratios that won't fit 9:16, skip files <5KB.

- [ ] **Step 3: Tests (mocked HTTP via `responses`)**

```python
def test_extracts_inline_images_with_captions(tmp_path, mocked_responses):
    html = '''<p>Studies show <img src="/fig1.jpg" alt="H2S reduction chart"> a 90% reduction.</p>'''
    mocked_responses.add(GET, "https://blog.example.com/fig1.jpg", body=b"\xff\xd8\xff" + b"X"*10000)
    blog = {"blog_id": "b1", "url": "https://blog.example.com/post", "content_html": html}
    out = extract_blog_sources(blog, tmp_path)
    assert any(s["source_type"] == "inline_image" for s in out)
    assert any("90%" in (s["caption"] or "") or "reduction" in s["tokens"] for s in out)


def test_renders_authority_pdf_first_page(tmp_path, mocked_responses):
    pdf_bytes = _build_two_page_pdf("Calcium nitrate dosage in WWTPs", "Method...")
    mocked_responses.add(GET, "https://example.gov/paper.pdf", body=pdf_bytes,
                        content_type="application/pdf")
    html = 'See <a href="https://example.gov/paper.pdf">study</a>.'
    blog = {"blog_id": "b2", "url": "https://x", "content_html": html}
    out = extract_blog_sources(blog, tmp_path)
    pdf_pages = [s for s in out if s["source_type"] == "pdf_page"]
    assert pdf_pages
    assert pdf_pages[0]["is_authority"] is True
    assert pdf_pages[0]["path"].exists()


def test_caches_so_second_call_skips_download(tmp_path, mocked_responses):
    # Same html as above, count requests across two calls; second call should add 0 new.
    ...


def test_skips_logos_and_icons(tmp_path, mocked_responses):
    html = '<img src="/logo.png" alt="logo">'
    out = extract_blog_sources({"blog_id": "b", "url": "x", "content_html": html}, tmp_path)
    assert not out


def test_find_source_for_scene_scores_by_token_overlap(tmp_path):
    sources = [
        {"id": "a", "tokens": {"h2s", "reduction", "wastewater"}, "is_authority": True, ...},
        {"id": "b", "tokens": {"mining", "australia"}, "is_authority": False, ...},
    ]
    scene = {"narration": "H2S reduction in wastewater plants",
             "on_screen_text": "", "visual_spec": {}}
    match = find_source_for_scene(scene, sources)
    assert match["id"] == "a"
```

- [ ] **Step 4: Implement → green → commit**

```
git commit -m "feat(video_agent): source_extractor (blog images + citation PDFs)"
```

---

### Task 43: TDD `visual_engine/source_card.py` — render source images with brand frame

**Goal.** Display a sourced image (inline or PDF page) at 1080×1920 with a thin navy header bar (image title or section label) and a thin navy footer bar (citation host like "ncbi.nlm.nih.gov") so authority is conveyed visually without explicit "Source:" text.

**Files:**
- Create: `tests/video_agent/visual_engine/test_source_card.py`
- Create: `video_agent/visual_engine/source_card.py`

- [ ] **Step 1: API**

```python
def render_source_card(output_path: Path, *, source: dict,
                       resolution: tuple[int, int] = (1080, 1920)) -> Path:
    """Composite source['path'] into a 1080×1920 PNG with header (caption) and footer
    (URL host). Image is letterboxed onto BRAND_DARK_NAVY."""
```

- [ ] **Step 2: Layout (PIL)**

- Top bar: 110 px tall, BRAND_DARK_NAVY background, gold left-edge accent (8 px), caption text in `BRAND_TEXT_LIGHT` 36 pt, left margin 60 px. Truncate to 50 chars.
- Source image: scaled to fit `(1080-120) × (1920-110-90)`, centered. Background fill = navy.
- Bottom bar: 90 px tall, BRAND_DARK_NAVY, footer text = URL hostname in `BRAND_TEXT_MUTED` 28 pt, right-aligned with 60 px margin.

- [ ] **Step 3: Tests + implement + commit**

```
git commit -m "feat(video_agent): source_card renderer with brand header/footer"
```

---

### Task 44: Wire `source_extractor` and `source_card` into `script_builder` + `dispatcher`

**Files:**
- Modify: `video_agent/script_builder.py`
- Modify: `video_agent/visual_engine/dispatcher.py`

- [ ] **Step 1: `build_script` extracts and assigns**

In `build_script(blog_record, …)`:

```python
sources = extract_blog_sources(blog_record, cache_dir=Path("asset_library/blog_sources"))
for scene in scenes:
    match = find_source_for_scene(scene, sources)
    if match:
        scene["_source"] = {"path": str(match["path"]),
                             "caption": match["caption"],
                             "host": urlparse(match["source_url"]).hostname,
                             "is_authority": match["is_authority"]}
```

Persist the `_source` field in `script.json`.

- [ ] **Step 2: Dispatcher honors `_source`**

In `generate_visual`, **before** the visual_type switch, check `if scene.get("_source"):` and render via `source_card.render_source_card(...)`. This means even a `text_card` scene with a matching authority image gets the source treatment — sources outrank LLM-assigned visual_type.

Skip when `visual_type == "text_card"` AND `visual_spec.layout in {"hook", "cta"}` — hooks and CTAs should stay clean text cards.

- [ ] **Step 3: Test the integration**

Mock `extract_blog_sources` to return one PDF-page entry for a wastewater scene. Verify:
1. `script.json` contains `_source` on that scene.
2. `generate_visual` uses `source_card`, not text_card or infographic.
3. The result is `is_static=True` (no Ken Burns — sources are already information-dense).

- [ ] **Step 4: Commit**

```
git commit -m "feat(video_agent): blog-source images outrank generated visuals"
```

---

### Task 45: Acceptance — re-run smoke with a richer test blog

**Goal.** Confirm the pipeline produces a video with ≥2 sourced visuals when given a blog that has inline images and citations.

- [ ] **Step 1: Add a test blog fixture**

Update `scripts/smoke_video.py` (or a new `scripts/smoke_video_rich.py`) with a `BLOG` dict whose `content_html` includes:
- 2 inline `<img>` tags pointing to real Wikimedia Commons images of treatment plants (free to use, stable URLs)
- 1 `<a href>` link to a real `.gov` PDF or NCBI paper

- [ ] **Step 2: Run end-to-end**

`python scripts/smoke_video_rich.py`. Inspect the resulting MP4 — ≥2 scenes should show real images with brand frames.

- [ ] **Step 3: Tag**

```
git tag video-agent-sprint-5
```

---

## Sprint 5 acceptance (revised)

- [ ] Tasks 21–24 (factory_broll, asset_manifest, tag_assets) — as in Part 2.
- [ ] Tasks 22-amendment, 42, 43, 44, 45 — as above.
- [ ] Task 23 (`stock.py` Pexels) — **defer to Sprint 8 Task 46**. Implement only if source_extractor coverage is too sparse on real blogs.
- [ ] Smoke run shows real factory clips when manifest has them, source images otherwise, text cards as final fallback.

---

## Sprint 8 amendment: stock.py deferred + setup doc captures cache redirects

### Task 46 (new): `stock.py` — Pexels last-resort, gated behind feature flag

Implement per Part 2 design (`§4.5.3`), but **only if** measurement on 5+ real published blogs shows source_extractor matches <60% of `stock`/`hrsu_edge` scenes. Add a `ENABLE_PEXELS_FALLBACK = False` flag in `config.py`; turn on per-blog if needed.

### Task 36 amendment: `VIDEO_SETUP.md` includes cache-redirect explanation

The `video_agent/config.py` bootstrap that redirects `HF_HOME`, `TORCH_HOME`, `MPLCONFIGDIR`, and `TMP`/`TEMP`/`TMPDIR` to `<project>/.cache/` was added during Sprint 4 to keep model downloads off the C: drive. Document it in `VIDEO_SETUP.md`:

- Why it exists (SSD layout / disk-budget concerns).
- How to opt out (set `HF_HOME` etc. before running).
- Disk impact estimate (Whisper base.en ≈ 145 MB; Kokoro ONNX ≈ 350 MB; matplotlib cache trivial).

---

## Notes for the Implementing Agent

- **Migration discipline.** Task 22-amendment renames `footage_library.py` to `factory_broll.py`. This is a **rename**, not a rewrite — preserve the test coverage, then add the richer scoring fields. The premature ship was useful; don't throw it away.
- **Source-first ordering.** Task 44 is explicit: source images outrank LLM-assigned visual_type, except for hook/CTA cards. This is intentional. The blog's own references are always more authoritative than an LLM-imagined chart.
- **Caching is mandatory.** `extract_blog_sources` runs once per blog. The cache lives in `asset_library/blog_sources/<blog_id>/`. Subsequent regenerations (e.g., re-running with `force=True`) must skip already-downloaded files unless explicitly cleared.
- **Don't add AI image generation.** Spec §4.13 still rejects this. The user has confirmed: real source images > generated imagery.
- **One commit per task.** As before.

---

**End of Part 3.** When Sprints 4.5 and 5-revised land, the pipeline produces videos with: real research-paper screenshots, real blog images, real factory footage, on-brand infographics from canonical chart data, static text cards that don't drift, and readable subtitles. After Sprint 8 ships, the system meets spec §8 acceptance criteria.
