"""Scene planner: annotate shots with what they show. Never changes what they say.

The shots and their text come from youtube/script_parser.py. The model is
shown the shots by position and returns, per position, a shot type, a visual
description and the characters on screen. Its output contains no script text,
so there is nothing for it to reword; anything invalid falls back to a safe
default instead of failing the plan.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from youtube.models import SHOT_TYPES
from youtube.script_parser import NARRATOR, Shot

logger = logging.getLogger(__name__)

PLANNER_PROMPT = """You are storyboarding a video from an approved script. Do not rewrite, summarise or quote the script.

For each shot below, decide:
- "shot_type": one of "narration", "dialogue", "two_character", "cutaway".
    * A NARRATOR shot is always "narration".
    * A character's line is "dialogue", or "two_character" when the other person in the exchange should be visible in the same frame.
- "visual": one or two sentences describing the image on screen: setting, action, framing, mood. Describe one single moment, like one frame of a film, not a sequence of actions. Describe what we see, not what is said.
- "characters": the speaker labels of every character visible in the shot, including in narration shots. Empty only when none of them can be seen.
- "sound": the ambient sound of the scene in a few words (e.g. "heavy rain, distant thunder", "busy cafe chatter"), or "" if it should be quiet. No music, no speech.

Also describe each speaker's likely appearance and manner in a few words under "speakers", using only what the script implies.

Return JSON only, in this shape:
{{"shots": [{{"position": 1, "shot_type": "narration", "visual": "...", "characters": [], "sound": "..."}}], "speakers": {{"MAYA": "..."}}}}

VIDEO FORMAT: {format}
SPEAKERS: {speakers}

SHOTS:
{shots}
"""


@dataclass
class ShotPlan:
    position: int
    shot_type: str
    visual: str
    characters: list[str] = field(default_factory=list)
    sound: str = ""


@dataclass
class PlanAnnotations:
    shots: dict[int, ShotPlan]
    speakers: dict[str, str]
    used_fallback: bool = False


def default_annotation(shot: Shot) -> ShotPlan:
    shot_type = "narration" if shot.speaker == NARRATOR else "dialogue"
    visual = shot.context or ("Scene illustrating: " + shot.text if shot.speaker == NARRATOR
                              else f"{shot.speaker} speaking")
    characters = [] if shot.speaker == NARRATOR else [shot.speaker]
    return ShotPlan(shot.position, shot_type, visual, characters)


def _format_shots(shots: list[Shot]) -> str:
    lines = []
    for shot in shots:
        extra = []
        if shot.delivery:
            extra.append(f"delivery: {shot.delivery}")
        if shot.context:
            extra.append(f"directions before it: {shot.context}")
        suffix = f"  ({'; '.join(extra)})" if extra else ""
        lines.append(f"{shot.position}. [{shot.speaker}] {shot.text}{suffix}")
    return "\n".join(lines)


def _validate(raw: dict, shots: list[Shot]) -> PlanAnnotations:
    by_position = {s.position: s for s in shots}
    speakers = {s.speaker for s in shots}
    result = {p: default_annotation(s) for p, s in by_position.items()}
    fallback = False

    items = raw.get("shots") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        items, fallback = [], True
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        position = item.get("position")
        if position not in by_position or position in seen:
            continue
        seen.add(position)
        shot = by_position[position]
        plan = result[position]

        shot_type = item.get("shot_type")
        if shot.speaker == NARRATOR:
            plan.shot_type = "narration"
        elif shot_type in SHOT_TYPES and shot_type != "narration":
            plan.shot_type = shot_type

        visual = item.get("visual")
        if isinstance(visual, str) and visual.strip():
            plan.visual = visual.strip()[:1000]

        sound = item.get("sound")
        if isinstance(sound, str):
            plan.sound = " ".join(sound.split())[:120]

        characters = item.get("characters")
        if isinstance(characters, list):
            known = [c.strip().upper() for c in characters
                     if isinstance(c, str) and c.strip().upper() in speakers and c.strip().upper() != NARRATOR]
            if shot.speaker != NARRATOR and shot.speaker not in known:
                known.insert(0, shot.speaker)  # the speaker is always on screen in their own shot
            plan.characters = list(dict.fromkeys(known))

    # A character the visual names is on screen, whatever the model listed, so
    # their reference sheet is used and they are drawn as themselves.
    for position, plan in result.items():
        for label in sorted(speakers - {NARRATOR}):
            if label not in plan.characters and re.search(r"\b" + re.escape(label) + r"\b", plan.visual, re.IGNORECASE):
                plan.characters.append(label)

    if len(seen) < len(shots):
        fallback = True

    descriptions = raw.get("speakers") if isinstance(raw, dict) else None
    speaker_notes = {}
    if isinstance(descriptions, dict):
        for label, note in descriptions.items():
            if isinstance(label, str) and label.strip().upper() in speakers and isinstance(note, str):
                speaker_notes[label.strip().upper()] = note.strip()[:300]
    return PlanAnnotations(result, speaker_notes, fallback)


def annotate_shots(shots: list[Shot], video_format: str, llm=None) -> PlanAnnotations:
    """Ask the model for shot types and visuals. Falls back to defaults on any failure."""
    from utils.llm_output import parse_llm_json

    if llm is None:
        from model import LLMSingleton
        llm = LLMSingleton.get("extraction")

    speakers = sorted({s.speaker for s in shots})
    prompt = PLANNER_PROMPT.format(
        format="Short (vertical, under 3 minutes)" if video_format == "short" else "Long-form (16:9)",
        speakers=", ".join(speakers),
        shots=_format_shots(shots),
    )
    try:
        raw = parse_llm_json(llm.invoke(prompt).content)
    except Exception as e:
        logger.warning("Scene planner model call failed, using default annotations: %s", e)
        return PlanAnnotations({s.position: default_annotation(s) for s in shots}, {}, used_fallback=True)
    return _validate(raw, shots)
