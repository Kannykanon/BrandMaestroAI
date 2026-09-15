"""Faces, character sheets, style locks and the storyboard.

    faces       uploaded photos of a character; uploading requires the user to
                confirm they have the right to use the face
    sheet       one generated image showing the character from several angles,
                approved by the user; the reference for every scene
    style lock  a saved description (and optional reference image) applied to
                every scene of a project, so one scene does not look unlike the next
    storyboard  one generated image per shot, reviewed and approved by a person
                before anything expensive (avatar video) runs

Every function takes a session and only touches one business's rows.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from youtube import projects
from youtube.images import GeneratedImage, ImageInput, ImagePort, ImageRegistry, inspect_image, prepare_reference
from youtube.models import (
    SHOT_TYPES,
    YTCast,
    YTCharacter,
    YTCharacterImage,
    YTCost,
    YTProject,
    YTShot,
    YTStyle,
)
from youtube.projects import ProjectError
from youtube.script_parser import NARRATOR
from youtube.storage import StoragePort, business_key

logger = logging.getLogger(__name__)

MAX_FACE_UPLOADS = 5
DEFAULT_STYLE = "Cinematic photorealistic film still, natural light, shallow depth of field, rich but realistic colour."
EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def aspect_ratio(project: YTProject) -> str:
    return "9:16" if project.format == "short" else "16:9"


def mime_for_key(key: str) -> str:
    ext = key.rsplit(".", 1)[-1].lower()
    return {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(ext, "image/png")


def _store_image(storage: StoragePort, key_parts: tuple, data: bytes, mime: str) -> str:
    *folders, stem = key_parts
    key = business_key(*folders, f"{stem}-{uuid.uuid4().hex[:12]}.{EXTENSIONS.get(mime, 'png')}")
    storage.put(key, data, content_type=mime)
    return key


def _delete(storage: Optional[StoragePort], key: Optional[str]) -> None:
    if storage and key:
        try:
            storage.delete(key)
        except Exception as e:
            logger.warning("Could not delete %s: %s", key, e)


def _record_cost(db: Session, project_id: Optional[int], port: ImagePort) -> None:
    # Costs are per project; character sheets are not tied to one, so they are logged only.
    if project_id is None:
        logger.info("Image generated with %s (~$%.3f)", port.name, port.cost_usd())
        return
    db.add(YTCost(project_id=project_id, stage="image", provider=port.name, units=1.0,
                  unit="image", cost_usd=port.cost_usd()))


def _clear_approvals_for_character(db: Session, character_id: int) -> None:
    """A changed face or sheet changes every storyboard that shows the character."""
    project_ids = db.execute(select(YTCast.project_id).where(YTCast.character_id == character_id)).scalars().all()
    for project in db.execute(select(YTProject).where(YTProject.id.in_(project_ids))).scalars().all():
        if project.storyboard_approved_at:
            projects.clear_approval(project)
            projects.settle_status(db, project)


# ---------------------------------------------------------------------------
#  Faces and character sheets
# ---------------------------------------------------------------------------
def character_images(db: Session, character: YTCharacter, kind: Optional[str] = None) -> list[YTCharacterImage]:
    query = select(YTCharacterImage).where(YTCharacterImage.character_id == character.id)
    if kind:
        query = query.where(YTCharacterImage.kind == kind)
    return db.execute(query.order_by(YTCharacterImage.id)).scalars().all()


def get_character_image(db: Session, character: YTCharacter, image_id: int) -> Optional[YTCharacterImage]:
    return db.execute(select(YTCharacterImage).where(
        YTCharacterImage.id == image_id, YTCharacterImage.character_id == character.id)).scalar_one_or_none()


def approved_sheet(db: Session, character: YTCharacter) -> Optional[YTCharacterImage]:
    sheets = [i for i in character_images(db, character, "sheet") if i.approved]
    return sheets[-1] if sheets else None


def confirm_rights(db: Session, character: YTCharacter, confirmed: bool) -> YTCharacter:
    """Record that the user has the right to use this character's face. Withdrawing it is allowed."""
    character.rights_confirmed_at = _now() if confirmed else None
    db.commit()
    db.refresh(character)
    return character


def _mark_sheet_outdated(db: Session, character: YTCharacter) -> None:
    for sheet in character_images(db, character, "sheet"):
        sheet.approved = False
    if character.sheet_status == "approved":
        character.sheet_status = "ready"
    _clear_approvals_for_character(db, character.id)


def add_face(db: Session, character: YTCharacter, data: bytes, storage: StoragePort) -> YTCharacterImage:
    if not character.rights_confirmed_at:
        raise ProjectError("Confirm you have the right to use this face before uploading it")
    if len(character_images(db, character, "upload")) >= MAX_FACE_UPLOADS:
        raise ProjectError(f"A character can have at most {MAX_FACE_UPLOADS} face photos")
    try:
        mime, _, _ = inspect_image(data)
    except ValueError as e:
        raise ProjectError(str(e)) from e
    key = _store_image(storage, (character.business_id, "characters", str(character.id), "face"), data, mime)
    image = YTCharacterImage(character_id=character.id, kind="upload", storage_key=key, approved=True)
    db.add(image)
    _mark_sheet_outdated(db, character)
    db.commit()
    db.refresh(image)
    return image


def delete_character_image(db: Session, character: YTCharacter, image: YTCharacterImage,
                           storage: StoragePort) -> None:
    was_approved_sheet = image.kind == "sheet" and image.approved
    _delete(storage, image.storage_key)
    db.delete(image)
    db.flush()
    if image.kind == "upload":
        _mark_sheet_outdated(db, character)
    elif was_approved_sheet:
        character.sheet_status = "none"
        _clear_approvals_for_character(db, character.id)
    db.commit()


def sheet_prompt(character: YTCharacter) -> str:
    notes = f"\nAbout them: {character.style_notes.strip()}" if character.style_notes else ""
    return (
        f"Create a character reference sheet for a video storyboard, based on the reference photo(s) of {character.name}.\n"
        "Keep the person exactly as in the photos: the same face and facial features, skin tone, hair, age and build."
        f"{notes}\n"
        "Layout, on one plain light-grey background, left to right: (1) head-and-shoulders front view, "
        "(2) head-and-shoulders three-quarter view, (3) full-body front view, standing. "
        "The same outfit in every view.\n"
        "Photorealistic, even studio lighting. No text, labels, numbers or borders."
    )


def start_sheet(db: Session, character: YTCharacter) -> None:
    if not character.rights_confirmed_at:
        raise ProjectError("Confirm you have the right to use this face first")
    if not character_images(db, character, "upload"):
        raise ProjectError("Upload at least one face photo first")
    if character.sheet_status == "generating":
        raise ProjectError("A character sheet is already being generated")
    character.sheet_status, character.sheet_error = "generating", None
    db.commit()


def generate_sheet(db: Session, character: YTCharacter, storage: StoragePort,
                   port: Optional[ImagePort] = None) -> YTCharacterImage:
    """Generate a new sheet from the face photos. It replaces earlier sheets and awaits approval."""
    port = port or ImageRegistry.get()
    faces = character_images(db, character, "upload")[: port.max_references]
    if not faces:
        raise ProjectError("Upload at least one face photo first")
    references = [prepare_reference(storage.get(f.storage_key), f"Reference photo {i + 1} of {character.name}")
                  for i, f in enumerate(faces)]
    image = port.generate(sheet_prompt(character), references, "16:9")
    key = _store_image(storage, (character.business_id, "characters", str(character.id), "sheet"),
                       image.data, image.mime_type)
    for old in character_images(db, character, "sheet"):
        _delete(storage, old.storage_key)
        db.delete(old)
    sheet = YTCharacterImage(character_id=character.id, kind="sheet", storage_key=key, approved=False)
    db.add(sheet)
    character.sheet_status, character.sheet_error = "ready", None
    _record_cost(db, None, port)
    _clear_approvals_for_character(db, character.id)
    db.commit()
    db.refresh(sheet)
    return sheet


def fail_sheet(db: Session, character: YTCharacter, message: str) -> None:
    character.sheet_status, character.sheet_error = "failed", message[:1000]
    db.commit()


def approve_sheet(db: Session, character: YTCharacter) -> YTCharacterImage:
    sheets = character_images(db, character, "sheet")
    if not sheets:
        raise ProjectError("Generate a character sheet first")
    sheet = sheets[-1]
    sheet.approved = True
    character.sheet_status = "approved"
    _clear_approvals_for_character(db, character.id)
    db.commit()
    return sheet


def serialize_character_images(db: Session, character: YTCharacter) -> dict:
    images = character_images(db, character)
    return {
        "rights_confirmed": bool(character.rights_confirmed_at),
        "sheet_status": character.sheet_status,
        "sheet_error": character.sheet_error,
        "faces": [{"id": i.id} for i in images if i.kind == "upload"],
        "sheet": next(({"id": i.id, "approved": i.approved} for i in reversed(images) if i.kind == "sheet"), None),
    }


# ---------------------------------------------------------------------------
#  Style locks
# ---------------------------------------------------------------------------
def list_styles(db: Session, business_id: str) -> list[YTStyle]:
    return db.execute(select(YTStyle).where(YTStyle.business_id == business_id).order_by(YTStyle.name)).scalars().all()


def get_style(db: Session, business_id: str, style_id: int) -> Optional[YTStyle]:
    return db.execute(select(YTStyle).where(YTStyle.id == style_id, YTStyle.business_id == business_id)).scalar_one_or_none()


def _projects_with_style(db: Session, style_id: int) -> list[YTProject]:
    return db.execute(select(YTProject).where(YTProject.style_id == style_id)).scalars().all()


def create_style(db: Session, business_id: str, name: str, prompt: str) -> YTStyle:
    name, prompt = (name or "").strip(), (prompt or "").strip()
    if not name or not prompt:
        raise ProjectError("A style needs a name and a description")
    style = YTStyle(business_id=business_id, name=name, prompt=prompt)
    db.add(style)
    db.commit()
    db.refresh(style)
    return style


def update_style(db: Session, style: YTStyle, name: Optional[str], prompt: Optional[str]) -> YTStyle:
    if name is not None:
        if not name.strip():
            raise ProjectError("A style needs a name")
        style.name = name.strip()
    if prompt is not None:
        if not prompt.strip():
            raise ProjectError("A style needs a description")
        style.prompt = prompt.strip()
        for project in _projects_with_style(db, style.id):
            projects.clear_approval(project)
            projects.settle_status(db, project)
    db.commit()
    db.refresh(style)
    return style


def set_style_reference(db: Session, style: YTStyle, data: Optional[bytes], storage: StoragePort) -> YTStyle:
    """Replace the style's reference image, or remove it when data is None."""
    new_key = None
    if data is not None:
        try:
            mime, _, _ = inspect_image(data)
        except ValueError as e:
            raise ProjectError(str(e)) from e
        new_key = _store_image(storage, (style.business_id, "styles", str(style.id), "reference"), data, mime)
    _delete(storage, style.reference_storage_key)
    style.reference_storage_key = new_key
    for project in _projects_with_style(db, style.id):
        projects.clear_approval(project)
        projects.settle_status(db, project)
    db.commit()
    db.refresh(style)
    return style


def delete_style(db: Session, style: YTStyle, storage: StoragePort) -> None:
    for project in _projects_with_style(db, style.id):
        project.style_id = None
        projects.clear_approval(project)
        projects.settle_status(db, project)
    _delete(storage, style.reference_storage_key)
    db.delete(style)
    db.commit()


def serialize_style(style: YTStyle) -> dict:
    return {"id": style.id, "name": style.name, "prompt": style.prompt,
            "has_reference": bool(style.reference_storage_key),
            "reference_version": style.reference_storage_key.rsplit("/", 1)[-1] if style.reference_storage_key else None}


def set_project_style(db: Session, project: YTProject, style_id: Optional[int]) -> YTProject:
    projects._require_not_busy(project)
    if style_id is not None and get_style(db, project.business_id, style_id) is None:
        raise ProjectError("No such style for this business")
    if project.style_id != style_id:
        project.style_id = style_id
        projects.clear_approval(project)
        projects.settle_status(db, project)
    db.commit()
    db.refresh(project)
    return project


# ---------------------------------------------------------------------------
#  Storyboard
# ---------------------------------------------------------------------------
def _cast_characters(db: Session, project: YTProject) -> dict[str, Optional[YTCharacter]]:
    rows = db.execute(select(YTCast).where(YTCast.project_id == project.id)).scalars().all()
    return {row.speaker_label: (db.get(YTCharacter, row.character_id) if row.character_id else None) for row in rows}


def on_screen(shot: YTShot, max_characters: int) -> list[str]:
    """Speaker labels to draw in a shot: the speaker first, narrator never, at most max_characters."""
    labels = [c for c in (shot.characters or []) if c != NARRATOR]
    if shot.speaker_label != NARRATOR and shot.speaker_label in labels:
        labels.remove(shot.speaker_label)
        labels.insert(0, shot.speaker_label)
    return labels[:max_characters]


def storyboard_problems(db: Session, project: YTProject, port: Optional[ImagePort] = None) -> list[str]:
    """Everything that must be fixed before the storyboard can be drawn. Empty means ready."""
    port = port or ImageRegistry.get()
    problems = []
    if port.missing_env():
        problems.append(f"The image provider {port.name} is not configured (missing {', '.join(port.missing_env())})")
    shots = projects._shots(db, project)
    if not shots:
        return problems + ["Plan the shots first"]
    cast = _cast_characters(db, project)
    needed = sorted({label for shot in shots for label in on_screen(shot, port.max_characters)})
    for label in needed:
        character = cast.get(label)
        if character is None:
            problems.append(f"{label} is on screen but has no character")
        elif not character.rights_confirmed_at:
            problems.append(f"{character.name} ({label}): confirm you have the right to use this face")
        elif approved_sheet(db, character) is None:
            problems.append(f"{character.name} ({label}) needs an approved character sheet")
    return problems


def scene_prompt(project: YTProject, shot: YTShot, style: Optional[YTStyle],
                 characters: list[tuple[str, YTCharacter]], products: Optional[list] = None) -> str:
    orientation = "vertical" if project.format == "short" else "horizontal"
    lines = [f"Create one {orientation} image ({aspect_ratio(project)}) for a video storyboard.", "",
             f"STYLE: {style.prompt if style else DEFAULT_STYLE}", "",
             f"SCENE: {shot.visual_prompt or 'An establishing shot that suits the line below.'}", ""]
    if characters:
        lines.append("ON SCREEN (match each person's reference sheet exactly: same face, hair, skin tone, build and outfit):")
        for label, character in characters:
            notes = f" {character.style_notes.strip()}" if character.style_notes else ""
            lines.append(f"- {label}: {character.name}.{notes}")
    else:
        lines.append("ON SCREEN: no specific characters. People may appear only as unrecognisable background figures.")
    if products:
        lines += ["", "PRODUCTS (reproduce each exactly as in its reference photo: shape, proportions, colours, "
                      "materials, label and logo; place it naturally in the scene, clearly visible and in focus):"]
        lines += [f"- {p.name}" for p in products]
    lines.append("")
    speaker_names = dict(characters)
    if shot.shot_type == "dialogue" and shot.speaker_label in speaker_names:
        lines.append(f"FRAMING: {speaker_names[shot.speaker_label].name} is speaking. Their face is clearly visible, "
                     "turned towards the camera, mouth unobstructed, head and shoulders in the upper two thirds of the frame.")
    elif shot.shot_type == "two_character" and len(characters) >= 2:
        names = " and ".join(c.name for _, c in characters[:2])
        lines.append(f"FRAMING: {names} are both in frame with their faces unobstructed, facing each other or the camera.")
    else:
        lines.append("FRAMING: frame the scene clearly; it is shown while narration plays.")
    lines += ["", f"MOOD: the line spoken over this shot is: \"{shot.text}\" (for mood only; do not write it in the image).", "",
              "Show one single moment as one continuous frame: no split screens, panels, insets or collages, and each person appears at most once.",
              "Fill the whole frame edge to edge: no black bars, letterboxing or borders.",
              "Keep the bottom quarter free of important detail (but still part of the scene); captions go there.",
              ("Do not add any text, letters, captions, speech bubbles, logos or watermarks, other than what is "
               "printed on the products themselves." if products else
               "Do not add any text, letters, captions, speech bubbles, logos or watermarks.")]
    return "\n".join(lines)


def scene_references(db: Session, project: YTProject, shot: YTShot, storage: StoragePort,
                     port: ImagePort, products: Optional[list] = None) -> tuple[list[ImageInput], list[tuple[str, YTCharacter]]]:
    cast = _cast_characters(db, project)
    characters: list[tuple[str, YTCharacter]] = []
    references: list[ImageInput] = []
    for label in on_screen(shot, port.max_characters):
        character = cast.get(label)
        sheet = approved_sheet(db, character) if character else None
        if sheet is None:
            raise ProjectError(f"{label} needs a cast character with an approved sheet")
        characters.append((label, character))
        references.append(prepare_reference(storage.get(sheet.storage_key),
                                            f"Reference sheet for {label} ({character.name}):"))
    from youtube.brand_assets import flatten_on_white
    for product in products or []:
        if len(references) >= port.max_references:
            break
        references.append(prepare_reference(flatten_on_white(storage.get(product.storage_key)),
                                            f"Product reference for {product.name} (reproduce this exact product):"))
    style = db.get(YTStyle, project.style_id) if project.style_id else None
    if style and style.reference_storage_key and len(references) < port.max_references:
        references.append(prepare_reference(
            storage.get(style.reference_storage_key),
            "Style reference: match its colour palette, lighting and rendering style, not its content:"))
    return references, characters


def draw_shot(db: Session, project: YTProject, shot: YTShot, storage: StoragePort, port: ImagePort,
              checker="default") -> GeneratedImage:
    """Draw a shot, check it, and redraw it (at most YT_IMAGE_CHECK_RETRIES times) while problems are found."""
    from youtube.brand_assets import shot_products
    from youtube.quality import check_retries, scene_checker

    products = shot_products(db, project, shot)
    references, characters = scene_references(db, project, shot, storage, port, products)
    style = db.get(YTStyle, project.style_id) if project.style_id else None
    base_prompt = prompt = scene_prompt(project, shot, style, characters, products)
    checker = scene_checker(port.name) if checker == "default" else checker
    attempts = 1 + (check_retries() if checker else 0)
    for attempt in range(attempts):
        if attempt:
            _record_cost(db, project.id, port)  # the discarded attempt was paid for too
        image = port.generate(prompt, references, aspect_ratio(project))
        issues = (checker.check(image.data, image.mime_type, characters, products=[p.name for p in products])
                  if checker else [])
        if not issues:
            break
        logger.info("Project %s shot %s image flagged (attempt %s): %s", project.id, shot.position, attempt + 1, issues)
        prompt = base_prompt + "\nA previous attempt had these problems; avoid them: " + "; ".join(issues) + "."
    shot.image_issues = issues or None
    key = _store_image(storage, (project.business_id, "projects", str(project.id), "images", f"shot-{shot.position:04d}"),
                       image.data, image.mime_type)
    _delete(storage, shot.image_key)
    shot.image_key, shot.image_error = key, None
    projects.clear_clip(shot)
    _record_cost(db, project.id, port)
    return image


def generate_storyboard(db: Session, project: YTProject, storage: StoragePort,
                        port: Optional[ImagePort] = None, force: bool = False,
                        shot_ids: Optional[list[int]] = None) -> YTProject:
    """Draw every shot without an image (or all, with force, or only shot_ids).

    One shot failing does not stop the others; its error is kept on the shot.
    """
    port = port or ImageRegistry.get()
    problems = storyboard_problems(db, project, port)
    if problems:
        raise ProjectError("; ".join(problems))

    shots = projects._shots(db, project)
    targets = [s for s in shots if (shot_ids is None and (force or not s.image_key))
               or (shot_ids is not None and s.id in shot_ids)]
    failed = []
    for shot in targets:
        try:
            draw_shot(db, project, shot, storage, port)
        except Exception as e:
            logger.warning("Storyboard image failed for project %s shot %s: %s", project.id, shot.position, e)
            shot.image_error = str(e)[:1000]
            failed.append(shot.position)
        db.commit()  # keep each finished image even if a later one fails

    if targets:
        projects.clear_approval(project)
    project.error = (f"{len(failed)} shot(s) have no new image (shots {', '.join(map(str, failed))}); "
                     "retry them individually." if failed else None)
    projects.settle_status(db, project)
    db.commit()
    db.refresh(project)
    return project


def update_shot(db: Session, project: YTProject, shot_id: int, visual: Optional[str] = None,
                shot_type: Optional[str] = None) -> YTShot:
    """Edit what a shot shows. Its words stay as approved; its image is redrawn on request."""
    projects._require_not_busy(project)
    shot = db.execute(select(YTShot).where(YTShot.id == shot_id, YTShot.project_id == project.id)).scalar_one_or_none()
    if shot is None:
        raise ProjectError("No such shot in this project")
    changed = False
    if visual is not None:
        visual = visual.strip()
        if not visual:
            raise ProjectError("Describe what the shot shows")
        if visual != shot.visual_prompt:
            shot.visual_prompt, changed = visual[:2000], True
    if shot_type is not None:
        if shot_type not in SHOT_TYPES:
            raise ProjectError(f"shot_type must be one of {', '.join(SHOT_TYPES)}")
        if shot.speaker_label == NARRATOR and shot_type != "narration":
            raise ProjectError("A narrator shot is always narration; change its speaker first")
        if shot.speaker_label != NARRATOR and shot_type == "narration":
            raise ProjectError("A character's line cannot be a narration shot; change its speaker to NARRATOR")
        if shot_type != shot.shot_type:
            shot.shot_type, changed = shot_type, True
    if changed:
        projects.clear_approval(project)
        projects.settle_status(db, project)
    db.commit()
    db.refresh(shot)
    return shot


def approve_storyboard(db: Session, project: YTProject) -> YTProject:
    """A person signs off the storyboard. Needs an image for every shot and the voice track."""
    projects._require_not_busy(project)
    shots = projects._shots(db, project)
    if not shots:
        raise ProjectError("Plan the shots first")
    missing = [s.position for s in shots if not s.image_key]
    if missing:
        raise ProjectError(f"Shots without an image: {', '.join(map(str, missing))}")
    if not project.audio_key:
        raise ProjectError("Voice the script before approving the storyboard")
    project.storyboard_approved_at = _now()
    projects.settle_status(db, project)
    db.commit()
    db.refresh(project)
    return project


def storyboard_summary(db: Session, project: YTProject, shots: list[YTShot]) -> dict:
    try:
        port = ImageRegistry.get()
        estimate = port.cost_usd(sum(1 for s in shots if not s.image_key))
        provider, configured = port.name, not port.missing_env()
    except ValueError:
        estimate, provider, configured = None, None, False
    spent = sum(float(c.cost_usd) for c in db.execute(
        select(YTCost).where(YTCost.project_id == project.id)).scalars().all())
    return {
        "style_id": project.style_id,
        "images_done": sum(1 for s in shots if s.image_key),
        "images_flagged": sum(1 for s in shots if s.image_key and s.image_issues),
        "image_provider": provider,
        "image_provider_configured": configured,
        "estimate_remaining_usd": round(estimate, 2) if estimate is not None else None,
        "spent_usd": round(spent, 2),
        "approved": bool(project.storyboard_approved_at),
        "approved_at": project.storyboard_approved_at.isoformat() if project.storyboard_approved_at else None,
    }
