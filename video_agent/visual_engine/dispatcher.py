"""Routes scenes to the right visual generator. Falls back to text_card on error."""
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from video_agent.config import PARALLEL_VISUAL_WORKERS, STATIC_INFOGRAPHIC_TYPES
from video_agent.visual_engine.text_card import render_text_card
from video_agent.visual_engine.infographic import render_infographic
# factory_broll.find_footage superseded by video_agent.vision.footage_index — import removed.
from video_agent.visual_engine.source_card import render_source_card

log = logging.getLogger(__name__)


def _safe_text_card(scene: dict, output_path: Path,
                    resolution: tuple[int, int]) -> dict:
    spec = scene.get("visual_spec") or {}
    layout = spec.get("layout", "hook")
    text = scene.get("on_screen_text") or scene.get("narration", "")[:40] or "HRSU"
    render_text_card(output_path, layout=layout, text=text, resolution=resolution)
    return {
        "asset_path": output_path, "is_video_clip": False, "is_static": True,
        "duration_s": None, "generator_used": "text_card",
    }


_NUM_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


def _coerce_number(s) -> float | None:
    if isinstance(s, (int, float)):
        return float(s)
    if not isinstance(s, str):
        return None
    m = _NUM_RE.search(s)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "."))
    except ValueError:
        return None


def _extract_pairs(data: dict) -> list[tuple[str, float, str]]:
    """Return (humanized_label, parsed_number, raw_string) for every numeric value in data."""
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


def _normalize_chart_data(chart_type: str, data: dict) -> dict | None:
    """Coerce the LLM's loose data shape into what each chart renderer expects.
    Returns None if data can't be salvaged for this chart type."""
    data = data or {}
    if chart_type == "bar":
        # Canonical shape
        labels = data.get("labels")
        values = data.get("values")
        if isinstance(labels, list) and isinstance(values, list) and labels and values:
            nums = [_coerce_number(v) for v in values]
            if not any(n is None for n in nums):
                return {"labels": [str(l) for l in labels[:len(nums)]],
                        "values": [n for n in nums]}
        # labelN/valueN shape
        pairs = []
        for k, v in data.items():
            m = re.match(r"label(\d+)$", k, re.IGNORECASE)
            if m:
                idx = int(m.group(1))
                val = data.get(f"value{idx}") or data.get(f"Value{idx}")
                num = _coerce_number(val)
                if num is not None:
                    pairs.append((idx, str(v), num))
        if len(pairs) >= 2:
            pairs.sort()
            return {"labels": [p[1] for p in pairs], "values": [p[2] for p in pairs]}
        # Arbitrary key fallback: every key whose value is numeric
        arb = _extract_pairs(data)
        if len(arb) >= 2:
            return {"labels": [p[0] for p in arb], "values": [p[1] for p in arb]}
        return None

    if chart_type == "callout_stat":
        # Canonical keys first
        value = data.get("value") or data.get("value1") or data.get("stat")
        label = data.get("label") or data.get("unit") or data.get("description") or ""
        if value and _coerce_number(value) is not None:
            return {"value": str(value), "label": str(label)}
        # Arbitrary key fallback: use first numeric pair; label is humanized key name
        arb = _extract_pairs(data)
        if arb:
            lbl, _, raw = arb[0]
            return {"value": raw, "label": lbl}
        return None

    if chart_type == "comparison":
        if all(k in data for k in ("left_value", "right_value")):
            return data
        # Arbitrary key fallback: take first 2 numeric pairs as left/right
        arb = _extract_pairs(data)
        if len(arb) >= 2:
            return {
                "left_label": arb[0][0], "left_value": arb[0][2],
                "right_label": arb[1][0], "right_value": arb[1][2],
            }
        return None

    if chart_type == "flow":
        steps = data.get("steps")
        if isinstance(steps, list) and steps:
            return {"steps": [str(s) for s in steps]}
        return None

    if chart_type == "line":
        x, y = data.get("x"), data.get("y")
        if isinstance(x, list) and isinstance(y, list) and x and y:
            return {"x": x, "y": y}
        return None

    return data or None


def _safe_infographic(scene: dict, output_path: Path,
                      resolution: tuple[int, int], seed: int | None) -> dict:
    spec = scene.get("visual_spec") or {}
    chart_type = spec.get("chart_type", "callout_stat")
    data = _normalize_chart_data(chart_type, spec.get("data") or {})
    if data is None:
        log.warning(
            "Infographic scene %s has unusable data for chart_type=%r — falling back to text_card",
            scene.get("index"), chart_type,
        )
        return _safe_text_card(scene, output_path, resolution)
    render_infographic(
        output_path,
        chart_type=chart_type,
        title=spec.get("title", ""),
        data=data,
        resolution=resolution,
        seed=seed,
    )
    is_static = chart_type in STATIC_INFOGRAPHIC_TYPES
    return {
        "asset_path": output_path, "is_video_clip": False, "is_static": is_static,
        "duration_s": None, "generator_used": "infographic",
    }


def _safe_footage(scene: dict, output_path: Path,
                  resolution: tuple[int, int]) -> dict | None:
    """Footage lookup is now handled by video_agent.vision.footage_index
    (Workstream B-4/B-5). This dispatcher path is a legacy no-op."""
    return None


def _is_hook_or_cta(scene: dict) -> bool:
    spec = scene.get("visual_spec") or {}
    return spec.get("layout") in {"hook", "cta"}


def _safe_source_card(scene: dict, output_path: Path,
                      resolution: tuple[int, int]) -> dict:
    source = scene["_source"]
    render_source_card(output_path, source=source, resolution=resolution)
    return {
        "asset_path": output_path, "is_video_clip": False, "is_static": True,
        "duration_s": None, "generator_used": "source_card",
    }


def generate_visual(scene: dict, output_path: Path,
                    resolution: tuple[int, int] = (1080, 1920),
                    seed: int | None = None) -> dict:
    output_path = Path(output_path)
    vt = scene.get("visual_type", "text_card")
    seed = seed if seed is not None else hash((scene.get("index"), scene.get("narration", ""))) & 0xFFFF
    try:
        # Source image takes priority over the assigned visual_type,
        # except for hook/cta text cards which must stay as-is.
        if scene.get("_source") and not _is_hook_or_cta(scene):
            return _safe_source_card(scene, output_path, resolution)

        if vt == "text_card":
            return _safe_text_card(scene, output_path, resolution)
        if vt == "infographic":
            return _safe_infographic(scene, output_path, resolution, seed)
        if vt in ("stock", "hrsu_edge", "footage"):
            footage = _safe_footage(scene, output_path, resolution)
            if footage is not None:
                return footage
            log.info("No footage match for scene %s (vt=%r); using text_card",
                     scene.get("index"), vt)
            return _safe_text_card(scene, output_path, resolution)
        log.info("Visual type %r not recognized; using text_card", vt)
        return _safe_text_card(scene, output_path, resolution)
    except Exception as e:
        log.warning("Visual generation failed for scene %s (%s); falling back to text_card",
                    scene.get("index"), e)
        return _safe_text_card(scene, output_path, resolution)


def generate_all_visuals(scenes: list[dict], output_dir: Path,
                         resolution: tuple[int, int] = (1080, 1920)) -> list[dict]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    indexed = list(enumerate(scenes))

    def _job(args):
        i, s = args
        path = output_dir / f"scene_{i:02d}.png"
        return i, generate_visual(s, path, resolution)

    results: list[dict] = [None] * len(scenes)
    with ThreadPoolExecutor(max_workers=PARALLEL_VISUAL_WORKERS) as ex:
        for i, r in ex.map(_job, indexed):
            results[i] = r
    return results
