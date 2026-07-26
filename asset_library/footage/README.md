# Footage Library

Drop your raw HRSU clips, factory tours, drone shots, or licensed stock here.
The video pipeline will automatically pick the right clip for each scene.

## How matching works

For every scene that requests `visual_type: "stock"` or `visual_type: "hrsu_edge"`,
`video_agent/visual_engine/footage_library.py` scores each manifest entry against
the scene's narration, on-screen text, category, and `visual_spec.query`. The highest-
scoring clip wins (ties broken by `id` so the same blog → same clip).

If no clip scores above `MIN_SCORE` (default 2), the dispatcher falls back to a
text-card so the video still renders.

## Workflow

1. Drop a clip into this folder, e.g. `wastewater_tank_01.mp4`. Keep clips at
   1080×1920 vertical or be willing to accept a center-crop scale-to-fit.
2. Add an entry to `manifest.json` (see schema below). The `filename` must match
   the file you dropped.
3. Re-run the pipeline. Watch the logs for lines like:
   `Footage match for scene 4 → wastewater_tank_01 (score 7)`.

## Manifest schema

`manifest.json` is a JSON list. Each entry:

```json
{
  "id":          "wastewater_tank_aerial_01",
  "filename":    "wastewater_tank_aerial_01.mp4",
  "tags":        ["wastewater", "tank", "aerial", "industrial", "h2s"],
  "categories":  ["wastewater_treatment", "water_treatment"],
  "description": "Drone shot of clarifier tanks at municipal sewage plant",
  "duration_s":  12.4,
  "good_for":    ["hook", "establishing", "broll"]
}
```

| field        | meaning                                                                  |
|--------------|--------------------------------------------------------------------------|
| `id`         | Stable handle. Defaults to filename stem if omitted.                     |
| `filename`   | **Required.** Must exist in this folder.                                 |
| `tags`       | Free-form keywords. Each scene's narration is tokenised and overlapped.  |
| `categories` | One or more of HRSU's blog categories — strong signal (+5 / hit).        |
| `description`| One-line plain-English description. Tokens here weakly boost matches.    |
| `duration_s` | Clip length. Used by composer to know whether to loop or trim.           |
| `good_for`   | Tags like `hook`, `establishing`, `broll`. Boosts certain scene types.   |

## Tips

- More tags = more chances to match. List the obvious ones plus regional ones
  (`australia`, `mining`, `gulf`).
- For HRSU-specific scenes (`visual_type: hrsu_edge`), tag clips with `hrsu` or
  `factory` or set `good_for: ["establishing"]` for a +3 boost.
- Clips shorter than the scene duration are auto-looped by the composer.
- Clips longer than the scene are trimmed to the scene length.
