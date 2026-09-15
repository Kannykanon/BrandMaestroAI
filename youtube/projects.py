"""Video projects and characters: the operations behind the /youtube routes and tasks.

Every function takes a database session and a business_id, and only ever
reads or writes that business's rows.

Project status is worked out from what the project has, not from the last
step that ran, because voicing and storyboarding can happen in either order:

    draft                no shots yet
    planned              shots exist, but a speaker has no character with a voice
    cast                 every speaker is cast
    voiced               the voice track exists
    storyboard_ready     every shot has an image
    storyboard_approved  a person approved the storyboard, with images and audio
    rendered             a video was rendered from the storyboard as approved now
    uploaded_private     that video is on YouTube as private
    scheduled            ... and set to go public at a chosen time
    published            ... and public
    planning | voicing | drawing | rendering | uploading   a background step is running
    failed               the last background step failed (see error)

Any change to what the storyboard shows or says clears its approval.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from youtube.audio import Audio, concatenate
from youtube.eligibility import get_eligible_script
from youtube.models import FORMATS, YTCast, YTCharacter, YTCost, YTProject, YTShot
from youtube.script_parser import NARRATOR, ScriptError, plan_shots
from youtube.storage import StoragePort, business_key
from youtube.voice import VoiceRegistry

logger = logging.getLogger(__name__)

MAX_SHORT_SECONDS = 180
SPEECH_WORDS_PER_SECOND = 2.5
GAP_SAME_SPEAKER_S = 0.3
GAP_SPEAKER_CHANGE_S = 0.6

BUSY_STATUSES = {"planning", "voicing", "drawing", "rendering", "uploading"}


class ProjectError(ValueError):
    """A request that cannot be carried out in the project's current state."""


# ---------------------------------------------------------------------------
#  Characters
# ---------------------------------------------------------------------------
def list_characters(db: Session, business_id: str) -> list[YTCharacter]:
    return db.execute(
        select(YTCharacter).where(YTCharacter.business_id == business_id).order_by(YTCharacter.name)
    ).scalars().all()


def get_character(db: Session, business_id: str, character_id: int) -> Optional[YTCharacter]:
    return db.execute(
        select(YTCharacter).where(YTCharacter.id == character_id, YTCharacter.business_id == business_id)
    ).scalar_one_or_none()


def _check_voice(voice_provider: Optional[str], voice_id: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    if not voice_id:
        return None, None
    provider = VoiceRegistry.get(voice_provider)
    if not provider.has_voice(voice_id):
        raise ProjectError(f"Voice {voice_id!r} is not available from {provider.name}")
    return provider.name, voice_id


def create_character(db: Session, business_id: str, name: str, voice_id: Optional[str] = None,
                     voice_provider: Optional[str] = None, style_notes: Optional[str] = None) -> YTCharacter:
    name = (name or "").strip()
    if not name:
        raise ProjectError("A character needs a name")
    if db.execute(select(YTCharacter.id).where(
            YTCharacter.business_id == business_id, YTCharacter.name == name)).first():
        raise ProjectError(f"A character named {name!r} already exists")
    provider, voice = _check_voice(voice_provider, voice_id)
    character = YTCharacter(business_id=business_id, name=name, voice_provider=provider,
                            voice_id=voice, style_notes=style_notes)
    db.add(character)
    db.commit()
    db.refresh(character)
    return character


def update_character(db: Session, character: YTCharacter, name: Optional[str] = None,
                     voice_id: Optional[str] = None, voice_provider: Optional[str] = None,
                     style_notes: Optional[str] = None) -> YTCharacter:
    if name is not None:
        name = name.strip()
        if not name:
            raise ProjectError("A character needs a name")
        clash = db.execute(select(YTCharacter.id).where(
            YTCharacter.business_id == character.business_id, YTCharacter.name == name,
            YTCharacter.id != character.id)).first()
        if clash:
            raise ProjectError(f"A character named {name!r} already exists")
        character.name = name
    if voice_id is not None:
        character.voice_provider, character.voice_id = _check_voice(
            voice_provider or character.voice_provider, voice_id)
    if style_notes is not None:
        character.style_notes = style_notes
    db.commit()
    db.refresh(character)
    return character


def delete_character(db: Session, character: YTCharacter, storage: Optional[StoragePort] = None) -> None:
    """Delete a character with its face photos and character sheets, including the stored files."""
    from youtube.models import YTCharacterImage

    images = db.execute(select(YTCharacterImage).where(YTCharacterImage.character_id == character.id)).scalars().all()
    _delete_files(storage, [image.storage_key for image in images])
    for image in images:
        db.delete(image)
    db.delete(character)  # cast rows pointing at it are set to NULL by the foreign key
    db.commit()


def serialize_character(character: YTCharacter, db: Optional[Session] = None) -> dict:
    data = {
        "id": character.id,
        "name": character.name,
        "voice_provider": character.voice_provider,
        "voice_id": character.voice_id,
        "style_notes": character.style_notes,
    }
    if db is not None:
        from youtube.storyboard import serialize_character_images
        data.update(serialize_character_images(db, character))
    return data


# ---------------------------------------------------------------------------
#  Projects
# ---------------------------------------------------------------------------
def list_projects(db: Session, business_id: str) -> list[YTProject]:
    return db.execute(
        select(YTProject).where(YTProject.business_id == business_id).order_by(YTProject.created_at.desc())
    ).scalars().all()


def get_project(db: Session, business_id: str, project_id: int) -> Optional[YTProject]:
    return db.execute(
        select(YTProject).where(YTProject.id == project_id, YTProject.business_id == business_id)
    ).scalar_one_or_none()


def create_project(db: Session, business_id: str, generation_id: str, video_format: str) -> YTProject:
    """Snapshot an eligible marketing script into a new project."""
    if video_format not in FORMATS:
        raise ProjectError(f"format must be one of {', '.join(FORMATS)}")
    script = get_eligible_script(db, business_id, generation_id)
    if script is None:
        raise ProjectError("That script does not exist, belongs to another business, or is not approved")
    project = YTProject(
        business_id=business_id,
        source_generation_id=script.generation_id,
        topic=script.topic[:500],
        script_snapshot=script.content,
        approval_label=script.label,
        approved_at=script.completed_at,
        format=video_format,
        status="draft",
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def _shots(db: Session, project: YTProject) -> list[YTShot]:
    return db.execute(
        select(YTShot).where(YTShot.project_id == project.id).order_by(YTShot.position)
    ).scalars().all()


def _cast(db: Session, project: YTProject) -> list[YTCast]:
    return db.execute(
        select(YTCast).where(YTCast.project_id == project.id).order_by(YTCast.speaker_label)
    ).scalars().all()


def _require_not_busy(project: YTProject) -> None:
    if project.status in BUSY_STATUSES:
        raise ProjectError(f"The project is {project.status}; wait for it to finish")


def _delete_files(storage: Optional[StoragePort], keys: list[Optional[str]]) -> None:
    if storage is None:
        return
    for key in keys:
        if key:
            try:
                storage.delete(key)
            except Exception as e:  # a leftover file must not block the project
                logger.warning("Could not delete %s: %s", key, e)


def mark_busy(db: Session, project: YTProject, status: str) -> None:
    """Claim the project for a background step, so a second request is refused."""
    _require_not_busy(project)
    project.status, project.error = status, None
    db.commit()


def fail(db: Session, project: YTProject, message: str) -> None:
    project.status, project.error = "failed", message[:2000]
    db.commit()


def _sync_cast(db: Session, project: YTProject, speakers: set[str], descriptions: dict[str, str]) -> None:
    """One cast row per speaker. Keeps existing assignments; drops speakers no longer present."""
    rows = {row.speaker_label: row for row in _cast(db, project)}
    for label, row in rows.items():
        if label not in speakers:
            db.delete(row)
    for label in speakers:
        row = rows.get(label)
        if row is None:
            row = YTCast(project_id=project.id, speaker_label=label)
            db.add(row)
        if descriptions.get(label):
            row.description = descriptions[label]


def clear_approval(project: YTProject) -> None:
    project.storyboard_approved_at = None


def settle_status(db: Session, project: YTProject) -> str:
    """Set and return the status that describes what the project has."""
    shots = _shots(db, project)
    if not shots:
        status = "draft"
    elif not cast_ready(db, project):
        status = "planned"
    elif project.storyboard_approved_at and project.audio_key and all(s.image_key for s in shots):
        from youtube.render import latest_render, render_is_current
        render = latest_render(db, project)
        if render_is_current(render, project):
            from youtube.publishing import upload_state_for_render
            status = upload_state_for_render(db, render) or "rendered"
        else:
            status = "storyboard_approved"
    elif all(s.image_key for s in shots):
        status = "storyboard_ready"
    elif project.audio_key:
        status = "voiced"
    else:
        status = "cast"
    project.status = status
    return status


def cast_ready(db: Session, project: YTProject) -> bool:
    rows = _cast(db, project)
    if not rows:
        return False
    for row in rows:
        character = db.get(YTCharacter, row.character_id) if row.character_id else None
        if character is None or not character.voice_id:
            return False
    return True


def plan_project(db: Session, project: YTProject, llm=None, storage: Optional[StoragePort] = None) -> YTProject:
    """Split the snapshot into shots, verify the words, annotate, and create the cast.

    Re-planning replaces the shots and discards their audio; cast assignments
    for speakers that remain are kept.
    """
    from youtube.planner import annotate_shots

    try:
        _, shots = plan_shots(project.script_snapshot)
    except ScriptError as e:
        fail(db, project, f"Could not plan the script without changing it: {e}")
        raise ProjectError(str(e)) from e

    annotations = annotate_shots(shots, project.format, llm=llm)

    old = _shots(db, project)
    _delete_files(storage, [k for s in old for k in (s.audio_key, s.image_key, s.clip_key)] + [project.audio_key])
    for shot in old:
        db.delete(shot)
    db.flush()  # remove the old rows before new ones reuse their positions
    for shot in shots:
        plan = annotations.shots[shot.position]
        db.add(YTShot(
            project_id=project.id,
            position=shot.position,
            text=shot.text,
            speaker_label=shot.speaker,
            shot_type=plan.shot_type,
            delivery=shot.delivery or None,
            characters=plan.characters,
            visual_prompt=plan.visual,
            status="planned",
        ))
    project.audio_key, project.audio_duration_s = None, None
    clear_approval(project)
    db.flush()
    _sync_cast(db, project, {s.speaker for s in shots}, annotations.speakers)
    db.flush()
    settle_status(db, project)
    project.error = None
    db.commit()
    db.refresh(project)
    if annotations.used_fallback:
        logger.info("Project %s planned with default annotations for some shots", project.id)
    return project


def set_cast(db: Session, project: YTProject, assignments: dict[str, Optional[int]]) -> YTProject:
    """Assign characters to speakers. Changing a speaker's character discards its audio."""
    _require_not_busy(project)
    rows = {row.speaker_label: row for row in _cast(db, project)}
    if not rows:
        raise ProjectError("Plan the project before casting it")
    changed_speakers = set()
    for label, character_id in assignments.items():
        label = label.strip().upper()
        if label not in rows:
            raise ProjectError(f"No speaker {label!r} in this project")
        if character_id is not None and get_character(db, project.business_id, character_id) is None:
            raise ProjectError(f"No character {character_id} for this business")
        if rows[label].character_id != character_id:
            rows[label].character_id = character_id
            changed_speakers.add(label)
    if changed_speakers:
        _invalidate_audio(db, project, [s for s in _shots(db, project) if s.speaker_label in changed_speakers])
        clear_approval(project)
    settle_status(db, project)
    db.commit()
    db.refresh(project)
    return project


def clear_clip(shot: YTShot) -> None:
    """A talking clip is made from a shot's audio and image; when either changes, it is stale.

    The file is left for delete_project or the next render of this shot to remove.
    """
    shot.clip_provider, shot.clip_duration_s = None, None


def _invalidate_audio(db: Session, project: YTProject, shots: list[YTShot]) -> None:
    for shot in shots:
        shot.audio_key, shot.duration_s, shot.status = None, None, "planned"
        clear_clip(shot)
    project.audio_key, project.audio_duration_s = None, None


def reassign_speaker(db: Session, project: YTProject, shot_id: int, speaker: str) -> YTShot:
    """Change who speaks a shot. The words stay exactly as they are."""
    _require_not_busy(project)
    shot = db.execute(select(YTShot).where(YTShot.id == shot_id, YTShot.project_id == project.id)).scalar_one_or_none()
    if shot is None:
        raise ProjectError("No such shot in this project")
    speaker = " ".join((speaker or "").split()).upper()
    if not speaker:
        raise ProjectError("A speaker is required")
    if speaker in ("VO", "V.O.", "VOICEOVER"):
        speaker = NARRATOR
    if speaker != shot.speaker_label:
        shot.speaker_label = speaker
        if speaker == NARRATOR:
            shot.shot_type, shot.characters = "narration", []
        else:
            if shot.shot_type == "narration":
                shot.shot_type = "dialogue"
            shot.characters = list(dict.fromkeys([speaker, *(shot.characters or [])]))
        _invalidate_audio(db, project, [shot])
        clear_approval(project)
        db.flush()
        _sync_cast(db, project, {s.speaker_label for s in _shots(db, project)}, {})
        db.flush()
        settle_status(db, project)
    db.commit()
    db.refresh(shot)
    return shot


def voice_project(db: Session, project: YTProject, storage: StoragePort, force: bool = False) -> YTProject:
    """Speak every shot in its character's voice and join them into one track.

    Shots that already have audio are kept unless force is set, so re-running
    after a failure or a recast only does the missing work.
    """
    if not cast_ready(db, project):
        raise ProjectError("Every speaker needs a character with a voice before voicing")

    voices = {row.speaker_label: db.get(YTCharacter, row.character_id) for row in _cast(db, project)}
    shots = _shots(db, project)
    clips: list[Audio] = []
    for shot in shots:
        character = voices[shot.speaker_label]
        key = business_key(project.business_id, "projects", str(project.id), "audio",
                           f"shot-{shot.position:04d}.wav")
        if shot.audio_key and not force and storage.exists(shot.audio_key):
            clips.append(Audio.from_wav(storage.get(shot.audio_key)))
            continue
        provider = VoiceRegistry.get(character.voice_provider)
        audio = provider.synthesize(shot.text, character.voice_id)
        storage.put(key, audio.to_wav(), content_type="audio/wav")
        shot.audio_key, shot.duration_s, shot.status = key, audio.duration_s, "voiced"
        clear_clip(shot)
        db.add(YTCost(project_id=project.id, stage="voice", provider=provider.name,
                      units=float(len(shot.text)), unit="character", cost_usd=provider.cost_usd(shot.text)))
        db.commit()  # keep finished shots if a later one fails
        clips.append(audio)

    gaps = [
        GAP_SAME_SPEAKER_S if shots[i].speaker_label == shots[i + 1].speaker_label else GAP_SPEAKER_CHANGE_S
        for i in range(len(shots) - 1)
    ]
    track = concatenate(clips, gaps)
    track_key = business_key(project.business_id, "projects", str(project.id), "audio", "track.wav")
    storage.put(track_key, track.to_wav(), content_type="audio/wav")
    project.audio_key, project.audio_duration_s = track_key, track.duration_s
    if force:
        clear_approval(project)
    project.error = None
    settle_status(db, project)
    db.commit()
    db.refresh(project)
    return project


def delete_project(db: Session, project: YTProject, storage: Optional[StoragePort]) -> None:
    _require_not_busy(project)
    shots = _shots(db, project)
    from youtube.models import YTRender
    renders = db.execute(select(YTRender).where(YTRender.project_id == project.id)).scalars().all()
    _delete_files(storage, [k for s in shots for k in (s.audio_key, s.image_key, s.clip_key)]
                  + [project.audio_key] + [k for r in renders for k in (r.video_key, r.thumbnail_key)])
    db.delete(project)
    db.commit()


def estimated_duration_s(project: YTProject, shots: list[YTShot]) -> float:
    if project.audio_duration_s:
        return float(project.audio_duration_s)
    return sum(len(s.text.split()) for s in shots) / SPEECH_WORDS_PER_SECOND


def serialize_project(db: Session, project: YTProject, include_script: bool = False) -> dict:
    shots = _shots(db, project)
    cast_rows = _cast(db, project)
    characters = {c.id: c for c in list_characters(db, project.business_id)}
    duration = estimated_duration_s(project, shots) if shots else None
    warnings = []
    if project.format == "short" and duration and duration > MAX_SHORT_SECONDS:
        warnings.append(
            f"About {duration:.0f}s long; Shorts can be at most {MAX_SHORT_SECONDS}s. "
            "Use long-form instead. The script's words are never trimmed."
        )
    from youtube.brand_assets import project_products, serialize_end_card, shot_products
    products = project_products(db, project)
    data = {
        "id": project.id,
        "topic": project.topic,
        "asset_ids": [p.id for p in products],
        "end_card": serialize_end_card(project),
        "source_generation_id": project.source_generation_id,
        "approval": project.approval_label,
        "format": project.format,
        "status": project.status,
        "error": project.error,
        "warnings": warnings,
        "duration_s": round(duration, 1) if duration else None,
        "duration_is_estimate": not project.audio_duration_s,
        "has_audio": bool(project.audio_key),
        "cast": [
            {
                "speaker": row.speaker_label,
                "description": row.description,
                "character_id": row.character_id,
                "character_name": characters[row.character_id].name if row.character_id in characters else None,
                "has_voice": bool(row.character_id in characters and characters[row.character_id].voice_id),
            }
            for row in cast_rows
        ],
        "shots": [
            {
                "id": s.id,
                "position": s.position,
                "speaker": s.speaker_label,
                "text": s.text,
                "shot_type": s.shot_type,
                "delivery": s.delivery,
                "characters": s.characters or [],
                "visual": s.visual_prompt,
                "has_audio": bool(s.audio_key),
                "duration_s": round(s.duration_s, 2) if s.duration_s else None,
                "has_image": bool(s.image_key),
                "image_version": s.image_key.rsplit("/", 1)[-1] if s.image_key else None,
                "image_error": s.image_error,
                "image_issues": s.image_issues or [],
                "asset_ids": s.asset_ids,
                "products": [p.id for p in shot_products(db, project, s, products)],
            }
            for s in shots
        ],
        "created_at": project.created_at.isoformat() if project.created_at else None,
    }
    from youtube.render import render_summary
    from youtube.storyboard import storyboard_summary
    data["storyboard"] = storyboard_summary(db, project, shots)
    data["video"] = render_summary(db, project)
    from youtube.publishing import publishing_summary
    data["publishing"] = publishing_summary(db, project)
    if include_script:
        data["script"] = project.script_snapshot
    return data
