"""Single shared object that flows through every pipeline agent."""
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Literal


VisualType = Literal["photo", "diagram", "clip", "chart_data"]
Mood = Literal["problem", "mechanism", "proof", "brand"]
BeatName = Literal["hook", "stakes", "mechanism", "proof", "cta"]


@dataclass
class VisualConcept:
    subject: str
    modifier: str
    type: VisualType
    mood: Mood
    style_hint: str = ""


@dataclass
class HeroClaim:
    stat: str
    claim_text: str
    source_quote: str = ""


@dataclass
class Beat:
    index: int
    beat: BeatName
    purpose: str
    duration_target_s: float


@dataclass
class AssetCandidate:
    source: str               # "google_images" | "bing" | …
    url: str
    score: int
    local_path: str           # path to downloaded file
    caption: str = ""
    width: int = 0
    height: int = 0
    is_clip: bool = False
    duration_s: float | None = None
    # --- vision-judge fields (Workstream B) ---
    vision_score: int = -1          # 0-10 from the multimodal judge; -1 == not judged
    vision_reason: str = ""         # one-line why it fits / doesn't
    focus_x: float = 0.5            # normalized 0..1 horizontal centre of the subject
    focus_y: float = 0.5            # normalized 0..1 vertical centre of the subject
    subject_fills_frame: bool = False  # True == cropping to 9:16 would lose key content


@dataclass
class Motion:
    type: Literal["ken_burns", "zoom", "none"] = "ken_burns"
    direction: Literal["down", "up", "left", "right", "in", "out"] = "in"
    speed_px_per_frame: float = 0.6


@dataclass
class CriticNotes:
    alignment_score: int = 10
    flags: list[str] = field(default_factory=list)
    revision: str | None = None


@dataclass
class Cinematography:
    color_grade: str = "neutral_doc"
    # red_tension | cold_blue | warm_brand | neutral_doc | urgent_amber | clinical_white
    transition_in: str = "cut"
    # cut | hard_cut | slow_fade | slide_left
    motion: str = "slow_push"
    # slow_push | hold | fast_zoom | drift | parallax_lr
    voice_prosody: str = "conversational"
    # hook_emphasis | urgent_problem | conversational | warm_cta | matter_of_fact
    reasoning: str = ""


@dataclass
class Scene:
    index: int
    beat: BeatName
    narration: str
    on_screen_text: str
    visual_concept: VisualConcept
    duration_target_s: float
    transition_in: Literal["cut", "hard_cut", "slow_fade", "slide_left"] = "cut"
    asset_candidates: list[AssetCandidate] = field(default_factory=list)
    chosen_asset: AssetCandidate | None = None
    motion: Motion = field(default_factory=Motion)
    critic_notes: CriticNotes = field(default_factory=CriticNotes)
    degraded: bool = False
    cinematography: Cinematography | None = None


@dataclass
class DirectorNotes:
    arc_quality: int = 10
    hero_claim_supported: bool = True
    weakest_beat: int | None = None
    missing: list[str] = field(default_factory=list)
    redundant: list[int] = field(default_factory=list)
    ending_strength: int = 10
    revision_for_strategist: str | None = None


@dataclass
class Storyboard:
    version: str
    blog: dict[str, Any]
    hero_claim: HeroClaim | None = None
    arc: list[Beat] = field(default_factory=list)
    supporting_facts: list[dict[str, Any]] = field(default_factory=list)
    scenes: list[Scene] = field(default_factory=list)
    director_notes: DirectorNotes = field(default_factory=DirectorNotes)
    metadata: dict[str, Any] = field(default_factory=dict)
    narrative_thread: list[list[str]] = field(default_factory=list)


def save_storyboard(sb: Storyboard, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(sb), indent=2, ensure_ascii=False),
                    encoding="utf-8")


def load_storyboard(path: Path) -> Storyboard:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return _from_dict(raw)


def _from_dict(d: dict) -> Storyboard:
    hero = d.get("hero_claim")
    arc = [Beat(**b) for b in d.get("arc", [])]
    scenes = [_scene_from_dict(s) for s in d.get("scenes", [])]
    director = DirectorNotes(**d.get("director_notes",
                                     asdict(DirectorNotes())))
    return Storyboard(
        version=d["version"], blog=d["blog"],
        hero_claim=HeroClaim(**hero) if hero else None,
        arc=arc, supporting_facts=d.get("supporting_facts", []),
        scenes=scenes, director_notes=director,
        metadata=d.get("metadata", {}),
        narrative_thread=d.get("narrative_thread", []),
    )


def _scene_from_dict(d: dict) -> Scene:
    cands = [AssetCandidate(**c) for c in d.get("asset_candidates", [])]
    chosen_d = d.get("chosen_asset")
    chosen = AssetCandidate(**chosen_d) if chosen_d else None
    motion = Motion(**d.get("motion", asdict(Motion())))
    notes = CriticNotes(**d.get("critic_notes", asdict(CriticNotes())))
    vc = VisualConcept(**d["visual_concept"])
    cin_d = d.get("cinematography")
    cin = Cinematography(**cin_d) if cin_d else None
    return Scene(
        index=d["index"], beat=d["beat"], narration=d["narration"],
        on_screen_text=d["on_screen_text"], visual_concept=vc,
        duration_target_s=d["duration_target_s"],
        transition_in=d.get("transition_in", "cut"),
        asset_candidates=cands, chosen_asset=chosen, motion=motion,
        critic_notes=notes, degraded=d.get("degraded", False),
        cinematography=cin,
    )
