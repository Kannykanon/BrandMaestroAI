"""HTTP routes for YouTube Automation, mounted at /youtube.

Every route acts only on the signed-in user's business. Slow steps (planning,
voicing, voice previews) are queued on the yt_ queues and the project's status
reports progress.
"""
from datetime import datetime
from typing import Annotated, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
from youtube.eligibility import get_eligible_script, list_eligible_scripts
from youtube.avatar import AvatarRegistry
from youtube.images import MAX_UPLOAD_BYTES, ImageRegistry
from youtube.storage import StorageSingleton
from youtube.voice import VoiceRegistry

router = APIRouter()

CurrentUser = Annotated[object, Depends(get_current_user)]
Db = Annotated[Session, Depends(get_db)]


# ---------------------------------------------------------------------------
#  Request bodies
# ---------------------------------------------------------------------------
class CharacterIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    voice_id: Optional[str] = None
    voice_provider: Optional[str] = None
    style_notes: Optional[str] = Field(None, max_length=2000)


class CharacterPatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    voice_id: Optional[str] = None
    voice_provider: Optional[str] = None
    style_notes: Optional[str] = Field(None, max_length=2000)


class ProjectIn(BaseModel):
    generation_id: str
    format: str = Field("long_form", pattern="^(long_form|short)$")
    # Continue a series: the project becomes its next episode and inherits its setup.
    series_id: Optional[int] = None


class SeriesIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    logline: str = Field("", max_length=2000)


class SeriesPatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    logline: Optional[str] = Field(None, max_length=2000)


class AssetDrawIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    kind: str = Field("location", pattern="^(location|product)$")
    description: str = Field(..., min_length=3, max_length=1000)


class CastIn(BaseModel):
    # speaker label -> character id, or null to unassign
    assignments: dict[str, Optional[int]]


class ShotPatch(BaseModel):
    speaker: Optional[str] = Field(None, min_length=1, max_length=100)
    shot_type: Optional[str] = None
    visual: Optional[str] = Field(None, max_length=2000)
    # Products this shot shows; null goes back to matching product names in the line and visual.
    asset_ids: Optional[list[int]] = Field(None, max_length=20)
    # Ambience under the shot, e.g. "heavy rain, distant thunder"; empty for none.
    sound: Optional[str] = Field(None, max_length=200)


class RightsIn(BaseModel):
    confirmed: bool


class StyleIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    prompt: str = Field(..., min_length=1, max_length=2000)


class StylePatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    prompt: Optional[str] = Field(None, min_length=1, max_length=2000)


class ProjectPatch(BaseModel):
    style_id: Optional[int] = None
    asset_ids: Optional[list[int]] = Field(None, max_length=20)
    end_card: Optional[dict] = None
    audio: Optional[dict] = None
    series_id: Optional[int] = None


class StoryboardIn(BaseModel):
    force: bool = False


class RenderIn(BaseModel):
    confirm_over_budget: bool = False


class MetadataPatch(BaseModel):
    title: Optional[str] = Field(None, max_length=200)
    description: Optional[str] = Field(None, max_length=10000)
    tags: Optional[list[str]] = Field(None, max_length=100)
    category_id: Optional[str] = Field(None, max_length=10)
    made_for_kids: Optional[bool] = None


class UploadIn(BaseModel):
    # The person confirms they watched the render. Nothing reaches YouTube without it.
    reviewed: bool = False
    # Seconds of the render the person played in the app; recorded, not enforced.
    watched_seconds: Optional[float] = Field(None, ge=0, le=86400)


class PublishIn(BaseModel):
    # Omit to publish now; set to schedule. Must include a timezone offset.
    publish_at: Optional[datetime] = None


class VoiceIn(BaseModel):
    force: bool = False


class PreviewsIn(BaseModel):
    provider: Optional[str] = None
    force: bool = False


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------
def _projects():
    from youtube import projects
    return projects


def _bad_request(e: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


def _project_or_404(db: Session, user, project_id: int):
    project = _projects().get_project(db, user.business_id, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such project")
    return project


def _file_response(key: str, filename: str, as_link: bool = False, media_type: str = "audio/wav",
                   inline: bool = False):
    """Stream a stored file, or with as_link return {"url": ...} for the browser to load directly.

    Browsers cannot attach the bearer token to an <audio> element, and a
    redirect to a storage bucket would need CORS on the bucket. So the UI asks
    for a link: a signed URL when the store can make one (GCS), otherwise null,
    in which case it downloads the file through this route with its token.
    """
    storage = StorageSingleton.get()
    if as_link:
        try:
            if not storage.exists(key):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File is missing")
            return {"url": storage.url(key)}
        except HTTPException:
            raise
        except Exception:
            return {"url": None}  # e.g. credentials that cannot sign; stream instead
    try:
        data = storage.get(key)
    except FileNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File is missing")
    disposition = "inline" if inline else "attachment"
    return Response(content=data, media_type=media_type,
                    headers={"Content-Disposition": f'{disposition}; filename="{filename}"'})


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                            detail=f"Images can be at most {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    return data


def _storyboard():
    from youtube import storyboard
    return storyboard


def _character_or_404(db: Session, user, character_id: int):
    character = _projects().get_character(db, user.business_id, character_id)
    if character is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such character")
    return character


def _style_or_404(db: Session, user, style_id: int):
    style = _storyboard().get_style(db, user.business_id, style_id)
    if style is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such style")
    return style


def _enqueue(task, *args, **kwargs):
    import youtube.tasks  # noqa: F401 - registers the yt_ queues on the Celery app
    return task.delay(*args, **kwargs)


# ---------------------------------------------------------------------------
#  Status and scripts
# ---------------------------------------------------------------------------
@router.get("/status")
async def youtube_status(current_user: CurrentUser):
    """What is set up so far, so the UI can say what is missing instead of failing later."""
    try:
        storage = StorageSingleton.provider_class()
        storage_status = {"provider": storage.name, "configured": not storage.missing_env(),
                          "missing": storage.missing_env()}
    except ValueError as e:
        storage_status = {"provider": None, "configured": False, "error": str(e)}

    try:
        voice_status = {"default_provider": VoiceRegistry.default_provider(),
                        "providers": sorted(VoiceRegistry.PROVIDERS)}
    except ValueError as e:
        voice_status = {"default_provider": None, "error": str(e)}

    try:
        image_port = ImageRegistry.get()
        image_status = {"provider": image_port.name, "configured": not image_port.missing_env(),
                        "missing": image_port.missing_env(), "usd_per_image": image_port.cost_usd()}
    except ValueError as e:
        image_status = {"provider": None, "configured": False, "error": str(e)}

    try:
        avatar_port = AvatarRegistry.get()
        avatar_status = {"provider": avatar_port.name, "configured": not avatar_port.missing_env(),
                         "missing": avatar_port.missing_env(), "lip_sync": avatar_port.lip_sync,
                         "usd_per_second": avatar_port.usd_per_second}
    except ValueError as e:
        avatar_status = {"provider": None, "configured": False, "error": str(e)}

    return {
        "storage": storage_status,
        "voice": voice_status,
        "images": image_status,
        "avatar": avatar_status,
        # Filled in by later phases.
        "channel_connected": False,
    }


@router.get("/scripts")
async def eligible_scripts(db: Db, current_user: CurrentUser):
    """Approved marketing scripts this business can turn into videos, newest first.

    Each carries `approval`: "human" (a person approved it) or "enforcer"
    (approved automatically, not reviewed by a person).
    """
    from datetime import datetime, timezone
    from youtube import imports

    scripts = imports.list_imports(db, current_user.business_id) + list_eligible_scripts(db, current_user.business_id)
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    scripts.sort(key=lambda s: (s.completed_at.replace(tzinfo=s.completed_at.tzinfo or timezone.utc)
                                if s.completed_at else oldest), reverse=True)
    return {"scripts": [s.to_summary() for s in scripts]}


@router.post("/scripts/import", status_code=status.HTTP_201_CREATED)
async def import_script(db: Db, current_user: CurrentUser, title: Optional[str] = Form(None),
                        content: Optional[str] = Form(None), file: Optional[UploadFile] = File(None)):
    """Bring in your own script: paste it as `content`, or upload a .txt or .md `file`.

    You approve it by importing it. It is labelled "imported" because content
    writing did not write or check it.
    """
    from youtube import imports

    try:
        if file is not None and file.filename:
            data = await file.read(imports.MAX_CHARS * 4 + 1)
            if len(data) > imports.MAX_CHARS * 4:
                raise imports.ProjectError(f"Scripts can be at most {imports.MAX_CHARS:,} characters")
            text = imports.decode_upload(data)
            title = title or file.filename.rsplit(".", 1)[0]
        else:
            text = content or ""
        return imports.import_script(db, current_user.business_id, title, text).to_summary()
    except ValueError as e:
        raise _bad_request(e)


@router.delete("/scripts/{generation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_imported_script(generation_id: str, db: Db, current_user: CurrentUser):
    """Delete an imported script. Scripts from content writing are managed there. Projects keep their copy."""
    from youtube import imports

    if not imports.is_import_id(generation_id) or not imports.delete_import(db, current_user.business_id, generation_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No imported script with that id")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/scripts/{generation_id}")
async def eligible_script(generation_id: str, db: Db, current_user: CurrentUser):
    """The full text of one eligible script. 404 if it is missing, not yours, or not eligible."""
    from youtube import imports

    script = (imports.get_import(db, current_user.business_id, generation_id) if imports.is_import_id(generation_id)
              else get_eligible_script(db, current_user.business_id, generation_id))
    if script is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No eligible script with that id for this business")
    return {**script.to_summary(), "content": script.content}


# ---------------------------------------------------------------------------
#  Voices
# ---------------------------------------------------------------------------
@router.get("/voices")
def list_voices(current_user: CurrentUser, provider: Optional[str] = None):
    from youtube.voice_previews import available_previews

    try:
        port = VoiceRegistry.get(provider)
        voices = port.list_voices()
    except ValueError as e:
        raise _bad_request(e)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=f"Voices from {provider or 'the default provider'} are unavailable: {e}")
    previews = available_previews(StorageSingleton.get(), port.name)
    return {
        "provider": port.name,
        "voices": [{**v.to_dict(), "has_preview": v.id in previews} for v in voices],
    }


@router.post("/voices/previews", status_code=status.HTTP_202_ACCEPTED)
def create_voice_previews(body: PreviewsIn, current_user: CurrentUser):
    """Queue a short sample for every voice that does not have one yet."""
    from youtube.tasks import voice_previews

    try:
        name = VoiceRegistry.get(body.provider).name
    except ValueError as e:
        raise _bad_request(e)
    task = _enqueue(voice_previews, name, force=body.force)
    return {"task_id": task.id, "provider": name}


@router.get("/voices/{provider}/{voice_id}/preview")
def voice_preview(provider: str, voice_id: str, current_user: CurrentUser, as_link: bool = False):
    from youtube.voice_previews import preview_key

    try:
        key = preview_key(provider, voice_id)
    except ValueError as e:
        raise _bad_request(e)
    return _file_response(key, f"{voice_id}.wav", as_link)


# ---------------------------------------------------------------------------
#  Characters
# ---------------------------------------------------------------------------
@router.get("/characters")
def list_characters(db: Db, current_user: CurrentUser):
    p = _projects()
    return {"characters": [p.serialize_character(c, db) for c in p.list_characters(db, current_user.business_id)]}


@router.post("/characters", status_code=status.HTTP_201_CREATED)
def create_character(body: CharacterIn, db: Db, current_user: CurrentUser):
    p = _projects()
    try:
        character = p.create_character(db, current_user.business_id, body.name, body.voice_id,
                                       body.voice_provider, body.style_notes)
    except ValueError as e:
        raise _bad_request(e)
    return p.serialize_character(character)


@router.patch("/characters/{character_id}")
def update_character(character_id: int, body: CharacterPatch, db: Db, current_user: CurrentUser):
    p = _projects()
    character = p.get_character(db, current_user.business_id, character_id)
    if character is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such character")
    try:
        character = p.update_character(db, character, body.name, body.voice_id,
                                       body.voice_provider, body.style_notes)
    except ValueError as e:
        raise _bad_request(e)
    return p.serialize_character(character)


@router.delete("/characters/{character_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_character(character_id: int, db: Db, current_user: CurrentUser):
    p = _projects()
    character = p.get_character(db, current_user.business_id, character_id)
    if character is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such character")
    p.delete_character(db, character, StorageSingleton.get())
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
#  Projects
# ---------------------------------------------------------------------------
@router.get("/projects")
def list_projects(db: Db, current_user: CurrentUser):
    p = _projects()
    return {"projects": [
        {k: v for k, v in p.serialize_project(db, project).items() if k not in ("shots",)}
        for project in p.list_projects(db, current_user.business_id)
    ]}


@router.post("/projects", status_code=status.HTTP_201_CREATED)
def create_project(body: ProjectIn, db: Db, current_user: CurrentUser):
    from youtube import series as series_module

    p = _projects()
    try:
        project = p.create_project(db, current_user.business_id, body.generation_id, body.format)
        if body.series_id is not None:
            series = _series_or_404(db, current_user, body.series_id)
            series_module.add_to_series(db, project, series)
    except ValueError as e:
        raise _bad_request(e)
    return p.serialize_project(db, project, include_script=True)


@router.get("/projects/{project_id}")
def get_project(project_id: int, db: Db, current_user: CurrentUser):
    p = _projects()
    return p.serialize_project(db, _project_or_404(db, current_user, project_id), include_script=True)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: int, db: Db, current_user: CurrentUser):
    p = _projects()
    project = _project_or_404(db, current_user, project_id)
    try:
        p.delete_project(db, project, StorageSingleton.get())
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/projects/{project_id}/stop", status_code=status.HTTP_202_ACCEPTED)
def stop_project(project_id: int, db: Db, current_user: CurrentUser):
    """Ask the running step to stop at its next shot, keeping what it finished.

    Returns immediately: the worker is mid-shot, and stopping means doing no
    more work rather than abandoning the shot in flight. The project settles
    into the status its own files justify, and starting the same step again
    continues from there.
    """
    from youtube import cancel

    p = _projects()
    project = _project_or_404(db, current_user, project_id)
    if project.status not in p.BUSY_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Nothing is running — the project is {project.status}.",
        )
    if cancel.is_stale(project):
        # No worker is coming back to read the flag, so settle it here.
        p.stopped(db, project, "The worker running this stopped responding. Start it again to continue.")
        return {"status": project.status, "stopped": True}
    cancel.request(db, project)
    return {"status": project.status, "stopping": True}


@router.post("/projects/{project_id}/plan", status_code=status.HTTP_202_ACCEPTED)
def plan_project(project_id: int, db: Db, current_user: CurrentUser):
    """Queue the scene planner. Re-planning replaces the shots and discards their audio."""
    from youtube.tasks import plan_project as plan_task

    p = _projects()
    project = _project_or_404(db, current_user, project_id)
    try:
        p.mark_busy(db, project, "planning")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    task = _enqueue(plan_task, project.id, current_user.business_id)
    return {"task_id": task.id, "status": "planning"}


@router.put("/projects/{project_id}/cast")
def set_cast(project_id: int, body: CastIn, db: Db, current_user: CurrentUser):
    p = _projects()
    project = _project_or_404(db, current_user, project_id)
    try:
        project = p.set_cast(db, project, body.assignments)
    except ValueError as e:
        raise _bad_request(e)
    return p.serialize_project(db, project)


@router.patch("/projects/{project_id}/shots/{shot_id}")
def update_shot(project_id: int, shot_id: int, body: ShotPatch, db: Db, current_user: CurrentUser):
    """Change who speaks a shot, its type, or what it shows. Its words cannot be edited here."""
    p = _projects()
    project = _project_or_404(db, current_user, project_id)
    try:
        if body.speaker is not None:
            p.reassign_speaker(db, project, shot_id, body.speaker)
        if body.shot_type is not None or body.visual is not None:
            _storyboard().update_shot(db, project, shot_id, visual=body.visual, shot_type=body.shot_type)
        if "asset_ids" in body.model_fields_set:
            from youtube import brand_assets
            brand_assets.set_shot_products(db, project, shot_id, body.asset_ids)
        if "sound" in body.model_fields_set:
            from youtube import sound
            sound.set_shot_sound(db, project, shot_id, body.sound or "")
    except ValueError as e:
        raise _bad_request(e)
    return p.serialize_project(db, project)


@router.post("/projects/{project_id}/voice", status_code=status.HTTP_202_ACCEPTED)
def voice_project(project_id: int, body: VoiceIn, db: Db, current_user: CurrentUser):
    """Queue voicing. Only shots without audio are synthesized, unless force is set."""
    from youtube.tasks import voice_project as voice_task

    p = _projects()
    project = _project_or_404(db, current_user, project_id)
    if not p.cast_ready(db, project):
        raise _bad_request(ValueError("Every speaker needs a character with a voice before voicing"))
    try:
        p.mark_busy(db, project, "voicing")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    task = _enqueue(voice_task, project.id, current_user.business_id, force=body.force)
    return {"task_id": task.id, "status": "voicing"}


@router.get("/projects/{project_id}/audio")
def project_audio(project_id: int, db: Db, current_user: CurrentUser, as_link: bool = False):
    project = _project_or_404(db, current_user, project_id)
    if not project.audio_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This project has not been voiced yet")
    return _file_response(project.audio_key, f"project-{project.id}-voice.wav", as_link)


@router.get("/projects/{project_id}/shots/{shot_id}/audio")
def shot_audio(project_id: int, shot_id: int, db: Db, current_user: CurrentUser, as_link: bool = False):
    from youtube.models import YTShot

    project = _project_or_404(db, current_user, project_id)
    shot = db.get(YTShot, shot_id)
    if shot is None or shot.project_id != project.id or not shot.audio_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No audio for that shot")
    return _file_response(shot.audio_key, f"project-{project.id}-shot-{shot.position}.wav", as_link)


# ---------------------------------------------------------------------------
#  Faces and character sheets
# ---------------------------------------------------------------------------
@router.post("/characters/{character_id}/rights")
def confirm_rights(character_id: int, body: RightsIn, db: Db, current_user: CurrentUser):
    """Confirm (or withdraw) that you have the right to use this character's face."""
    character = _character_or_404(db, current_user, character_id)
    character = _storyboard().confirm_rights(db, character, body.confirmed)
    return _projects().serialize_character(character, db)


@router.post("/characters/{character_id}/faces", status_code=status.HTTP_201_CREATED)
async def upload_face(character_id: int, db: Db, current_user: CurrentUser, file: UploadFile = File(...)):
    character = _character_or_404(db, current_user, character_id)
    data = await _read_upload(file)
    try:
        _storyboard().add_face(db, character, data, StorageSingleton.get())
    except ValueError as e:
        raise _bad_request(e)
    return _projects().serialize_character(character, db)


@router.get("/characters/{character_id}/images/{image_id}")
def character_image(character_id: int, image_id: int, db: Db, current_user: CurrentUser, as_link: bool = False):
    sb = _storyboard()
    character = _character_or_404(db, current_user, character_id)
    image = sb.get_character_image(db, character, image_id)
    if image is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such image")
    return _file_response(image.storage_key, image.storage_key.rsplit("/", 1)[-1], as_link,
                          media_type=sb.mime_for_key(image.storage_key), inline=True)


@router.delete("/characters/{character_id}/images/{image_id}")
def delete_character_image(character_id: int, image_id: int, db: Db, current_user: CurrentUser):
    sb = _storyboard()
    character = _character_or_404(db, current_user, character_id)
    image = sb.get_character_image(db, character, image_id)
    if image is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such image")
    sb.delete_character_image(db, character, image, StorageSingleton.get())
    return _projects().serialize_character(character, db)


@router.post("/characters/{character_id}/sheet", status_code=status.HTTP_202_ACCEPTED)
def generate_character_sheet(character_id: int, db: Db, current_user: CurrentUser):
    """Queue a character sheet from the face photos. It replaces any earlier sheet."""
    from youtube.tasks import character_sheet

    character = _character_or_404(db, current_user, character_id)
    port = ImageRegistry.get()
    if port.missing_env():
        raise _bad_request(ValueError(f"The image provider {port.name} is not configured"))
    try:
        _storyboard().start_sheet(db, character)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    task = _enqueue(character_sheet, character.id, current_user.business_id)
    return {"task_id": task.id, "sheet_status": "generating"}


@router.post("/characters/{character_id}/sheet/approve")
def approve_character_sheet(character_id: int, db: Db, current_user: CurrentUser):
    character = _character_or_404(db, current_user, character_id)
    try:
        _storyboard().approve_sheet(db, character)
    except ValueError as e:
        raise _bad_request(e)
    return _projects().serialize_character(character, db)


# ---------------------------------------------------------------------------
#  Style locks
# ---------------------------------------------------------------------------
@router.get("/styles")
def list_styles(db: Db, current_user: CurrentUser):
    sb = _storyboard()
    return {"styles": [sb.serialize_style(s) for s in sb.list_styles(db, current_user.business_id)]}


@router.post("/styles", status_code=status.HTTP_201_CREATED)
def create_style(body: StyleIn, db: Db, current_user: CurrentUser):
    sb = _storyboard()
    try:
        return sb.serialize_style(sb.create_style(db, current_user.business_id, body.name, body.prompt))
    except ValueError as e:
        raise _bad_request(e)


@router.patch("/styles/{style_id}")
def update_style(style_id: int, body: StylePatch, db: Db, current_user: CurrentUser):
    sb = _storyboard()
    style = _style_or_404(db, current_user, style_id)
    try:
        return sb.serialize_style(sb.update_style(db, style, body.name, body.prompt))
    except ValueError as e:
        raise _bad_request(e)


@router.delete("/styles/{style_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_style(style_id: int, db: Db, current_user: CurrentUser):
    _storyboard().delete_style(db, _style_or_404(db, current_user, style_id), StorageSingleton.get())
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/styles/{style_id}/reference")
async def upload_style_reference(style_id: int, db: Db, current_user: CurrentUser, file: UploadFile = File(...)):
    sb = _storyboard()
    style = _style_or_404(db, current_user, style_id)
    data = await _read_upload(file)
    try:
        return sb.serialize_style(sb.set_style_reference(db, style, data, StorageSingleton.get()))
    except ValueError as e:
        raise _bad_request(e)


@router.delete("/styles/{style_id}/reference")
def delete_style_reference(style_id: int, db: Db, current_user: CurrentUser):
    sb = _storyboard()
    style = _style_or_404(db, current_user, style_id)
    return sb.serialize_style(sb.set_style_reference(db, style, None, StorageSingleton.get()))


@router.get("/styles/{style_id}/reference")
def style_reference(style_id: int, db: Db, current_user: CurrentUser, as_link: bool = False):
    sb = _storyboard()
    style = _style_or_404(db, current_user, style_id)
    if not style.reference_storage_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This style has no reference image")
    return _file_response(style.reference_storage_key, style.reference_storage_key.rsplit("/", 1)[-1], as_link,
                          media_type=sb.mime_for_key(style.reference_storage_key), inline=True)


# ---------------------------------------------------------------------------
#  Storyboard
# ---------------------------------------------------------------------------
@router.patch("/projects/{project_id}")
def update_project(project_id: int, body: ProjectPatch, db: Db, current_user: CurrentUser):
    """Change the project's style lock, featured products or end card. Only the fields sent are changed."""
    from youtube import brand_assets

    project = _project_or_404(db, current_user, project_id)
    fields = body.model_fields_set
    try:
        if "style_id" in fields:
            _storyboard().set_project_style(db, project, body.style_id)
        if "asset_ids" in fields:
            brand_assets.set_project_products(db, project, body.asset_ids or [])
        if "end_card" in fields:
            brand_assets.set_end_card(db, project, body.end_card or {})
        if "audio" in fields:
            from youtube import sound
            sound.set_project_audio(db, project, body.audio or {})
        if "series_id" in fields:
            from youtube import series as series_module
            target = _series_or_404(db, current_user, body.series_id) if body.series_id is not None else None
            series_module.add_to_series(db, project, target)
    except ValueError as e:
        raise _bad_request(e)
    return _projects().serialize_project(db, project)


def _queue_storyboard(db: Session, user, project_id: int, force: bool = False, shot_ids=None):
    from youtube.tasks import storyboard_project

    p, sb = _projects(), _storyboard()
    project = _project_or_404(db, user, project_id)
    problems = sb.storyboard_problems(db, project)
    if problems:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="; ".join(problems))
    try:
        p.mark_busy(db, project, "drawing")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    task = _enqueue(storyboard_project, project.id, user.business_id, force=force, shot_ids=shot_ids)
    return {"task_id": task.id, "status": "drawing"}


@router.post("/projects/{project_id}/storyboard", status_code=status.HTTP_202_ACCEPTED)
def generate_storyboard(project_id: int, body: StoryboardIn, db: Db, current_user: CurrentUser):
    """Queue images for every shot without one (or every shot, with force)."""
    return _queue_storyboard(db, current_user, project_id, force=body.force)


@router.post("/projects/{project_id}/shots/{shot_id}/image", status_code=status.HTTP_202_ACCEPTED)
def redraw_shot(project_id: int, shot_id: int, db: Db, current_user: CurrentUser):
    """Queue a new image for one shot, using its current visual description."""
    from youtube.models import YTShot

    project = _project_or_404(db, current_user, project_id)
    shot = db.get(YTShot, shot_id)
    if shot is None or shot.project_id != project.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such shot")
    return _queue_storyboard(db, current_user, project_id, shot_ids=[shot_id])


@router.get("/projects/{project_id}/shots/{shot_id}/image")
def shot_image(project_id: int, shot_id: int, db: Db, current_user: CurrentUser, as_link: bool = False):
    from youtube.models import YTShot

    project = _project_or_404(db, current_user, project_id)
    shot = db.get(YTShot, shot_id)
    if shot is None or shot.project_id != project.id or not shot.image_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No image for that shot")
    return _file_response(shot.image_key, shot.image_key.rsplit("/", 1)[-1], as_link,
                          media_type=_storyboard().mime_for_key(shot.image_key), inline=True)


@router.post("/projects/{project_id}/storyboard/approve")
def approve_storyboard(project_id: int, db: Db, current_user: CurrentUser):
    project = _project_or_404(db, current_user, project_id)
    try:
        _storyboard().approve_storyboard(db, project)
    except ValueError as e:
        raise _bad_request(e)
    return _projects().serialize_project(db, project)


# ---------------------------------------------------------------------------
#  Render
# ---------------------------------------------------------------------------
def _render():
    from youtube import render
    return render


@router.get("/projects/{project_id}/render/estimate")
def render_estimate(project_id: int, db: Db, current_user: CurrentUser):
    """What rendering would animate and cost, and anything that must be fixed first."""
    r = _render()
    project = _project_or_404(db, current_user, project_id)
    return {**r.estimate(db, project), "problems": r.render_problems(db, project, check_ffmpeg=False)}


@router.post("/projects/{project_id}/render", status_code=status.HTTP_202_ACCEPTED)
def start_render(project_id: int, body: RenderIn, db: Db, current_user: CurrentUser):
    """Queue a render. Above the avatar budget, confirm_over_budget must be true."""
    from youtube.tasks import render_project

    r, p = _render(), _projects()
    project = _project_or_404(db, current_user, project_id)
    problems = r.render_problems(db, project, check_ffmpeg=False)
    if problems:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="; ".join(problems))
    cost = r.estimate(db, project)
    if cost["over_budget"] and not body.confirm_over_budget:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={
            "message": f"Animating would cost about ${cost['avatar_cost_usd']:.2f}, over the "
                       f"${cost['budget_usd']:.2f} budget. Confirm to render anyway.",
            "estimate": cost,
        })
    try:
        p.mark_busy(db, project, "rendering")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    task = _enqueue(render_project, project.id, current_user.business_id,
                    confirm_over_budget=body.confirm_over_budget)
    return {"task_id": task.id, "status": "rendering", "estimate": cost}


def _render_or_404(db: Session, project, render_id: int):
    from youtube.models import YTRender

    render = db.get(YTRender, render_id)
    if render is None or render.project_id != project.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such render")
    return render


@router.get("/projects/{project_id}/renders/{render_id}/video")
def render_video(project_id: int, render_id: int, db: Db, current_user: CurrentUser, as_link: bool = False):
    project = _project_or_404(db, current_user, project_id)
    render = _render_or_404(db, project, render_id)
    return _file_response(render.video_key, f"project-{project.id}-render-{render.id}.mp4", as_link,
                          media_type="video/mp4")


@router.get("/projects/{project_id}/renders/{render_id}/thumbnail")
def render_thumbnail(project_id: int, render_id: int, db: Db, current_user: CurrentUser, as_link: bool = False):
    project = _project_or_404(db, current_user, project_id)
    render = _render_or_404(db, project, render_id)
    return _file_response(render.thumbnail_key, f"project-{project.id}-thumbnail-{render.id}.jpg", as_link,
                          media_type="image/jpeg", inline=True)


@router.get("/projects/{project_id}/shots/{shot_id}/clip")
def shot_clip(project_id: int, shot_id: int, db: Db, current_user: CurrentUser, as_link: bool = False):
    from youtube.models import YTShot

    project = _project_or_404(db, current_user, project_id)
    shot = db.get(YTShot, shot_id)
    if shot is None or shot.project_id != project.id or not shot.clip_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No talking clip for that shot")
    return _file_response(shot.clip_key, f"project-{project.id}-shot-{shot.position}.mp4", as_link,
                          media_type="video/mp4", inline=True)


# ---------------------------------------------------------------------------
#  Channel
# ---------------------------------------------------------------------------
NONCE_COOKIE = "yt_oauth_nonce"
NONCE_COOKIE_PATH = "/youtube/channel"


def _publishing():
    from youtube import publishing
    return publishing


def _request_base(request: Request) -> str:
    return str(request.base_url).rstrip("/")


@router.get("/channel")
def channel_status(request: Request, db: Db, current_user: CurrentUser):
    """Whether sign-in is configured, which channel is connected, and today's quota."""
    return _publishing().serialize_channel(db, current_user.business_id, _request_base(request))


@router.post("/channel/connect")
def channel_connect(request: Request, response: Response, current_user: CurrentUser):
    """Start Google sign-in. Returns the URL to send the browser to."""
    from youtube.publisher import PublisherSingleton

    publisher = PublisherSingleton.get()
    if publisher.missing_env():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"YouTube sign-in is not configured (missing {', '.join(publisher.missing_env())})")
    pub = _publishing()
    state, nonce = pub.make_state(current_user.business_id)
    response.set_cookie(NONCE_COOKIE, nonce, max_age=int(pub.STATE_TTL.total_seconds()), httponly=True,
                        samesite="lax", secure=request.url.scheme == "https", path=NONCE_COOKIE_PATH)
    return {"url": publisher.authorization_url(state, pub.redirect_uri(_request_base(request)))}


@router.get("/channel/callback", include_in_schema=False)
def channel_callback(request: Request, db: Db, state: str = "", code: str = "", error: str = ""):
    """Google redirects here after sign-in. Authenticated by the signed state and the browser's nonce cookie."""
    pub = _publishing()

    def back(outcome: str, message: str = ""):
        query = {"yt_channel": outcome, **({"yt_message": message[:300]} if message else {})}
        redirect = RedirectResponse(f"/?{urlencode(query)}", status_code=status.HTTP_303_SEE_OTHER)
        redirect.delete_cookie(NONCE_COOKIE, path=NONCE_COOKIE_PATH)
        return redirect

    if error:
        return back("error", "Google sign-in was cancelled" if error == "access_denied" else f"Google sign-in failed: {error}")
    try:
        business_id = pub.read_state(state, request.cookies.get(NONCE_COOKIE))
        if not code:
            raise pub.ProjectError("Google did not return a sign-in code")
        pub.connect_channel(db, business_id, code, pub.redirect_uri(_request_base(request)))
    except Exception as e:
        return back("error", str(e))
    return back("connected")


@router.delete("/channel", status_code=status.HTTP_204_NO_CONTENT)
def channel_disconnect(db: Db, current_user: CurrentUser):
    _publishing().disconnect_channel(db, current_user.business_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
#  Metadata, upload and publish
# ---------------------------------------------------------------------------
@router.patch("/projects/{project_id}/metadata")
def update_metadata(project_id: int, body: MetadataPatch, db: Db, current_user: CurrentUser):
    from youtube import metadata

    project = _project_or_404(db, current_user, project_id)
    try:
        metadata.update_metadata(db, project, **body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise _bad_request(e)
    return _projects().serialize_project(db, project)


@router.post("/projects/{project_id}/metadata/generate")
def generate_metadata(project_id: int, db: Db, current_user: CurrentUser):
    """Draft a title, description and tags from the script and Brand Brain. Replaces the current ones."""
    from youtube import metadata

    project = _project_or_404(db, current_user, project_id)
    try:
        metadata.generate_metadata(db, project)
    except ValueError as e:
        raise _bad_request(e)
    return _projects().serialize_project(db, project)


@router.post("/projects/{project_id}/upload", status_code=status.HTTP_202_ACCEPTED)
def start_upload(project_id: int, body: UploadIn, db: Db, current_user: CurrentUser):
    """Queue the current render for a private upload. Needs reviewed=true."""
    from youtube.tasks import upload_video

    project = _project_or_404(db, current_user, project_id)
    try:
        upload = _publishing().queue_upload(db, project, body.reviewed, watched_seconds=body.watched_seconds)
    except ValueError as e:
        raise _bad_request(e)
    task = _enqueue(upload_video, upload.id, current_user.business_id)
    return {"task_id": task.id, "status": "uploading", "upload": _publishing().serialize_upload(upload)}


def _upload_or_404(db: Session, project, upload_id: int):
    upload = _publishing().get_upload(db, project, upload_id)
    if upload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such upload")
    return upload


@router.post("/projects/{project_id}/uploads/{upload_id}/publish")
def publish_upload(project_id: int, upload_id: int, body: PublishIn, db: Db, current_user: CurrentUser):
    """Make the private video public now, or schedule it with publish_at."""
    if body.publish_at is not None and body.publish_at.tzinfo is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="publish_at must include a timezone offset")
    project = _project_or_404(db, current_user, project_id)
    upload = _upload_or_404(db, project, upload_id)
    try:
        _publishing().publish_upload(db, upload, publish_at=body.publish_at)
    except ValueError as e:
        raise _bad_request(e)
    return _projects().serialize_project(db, project)


@router.post("/projects/{project_id}/uploads/{upload_id}/refresh")
def refresh_upload(project_id: int, upload_id: int, db: Db, current_user: CurrentUser):
    """Read the video's processing and privacy state back from YouTube."""
    project = _project_or_404(db, current_user, project_id)
    upload = _upload_or_404(db, project, upload_id)
    try:
        _publishing().refresh_upload(db, upload)
    except ValueError as e:
        raise _bad_request(e)
    return _projects().serialize_project(db, project)


@router.post("/projects/{project_id}/uploads/{upload_id}/cancel")
def cancel_upload(project_id: int, upload_id: int, db: Db, current_user: CurrentUser):
    project = _project_or_404(db, current_user, project_id)
    upload = _upload_or_404(db, project, upload_id)
    try:
        _publishing().cancel_upload(db, upload)
    except ValueError as e:
        raise _bad_request(e)
    return _projects().serialize_project(db, project)


@router.post("/projects/{project_id}/uploads/{upload_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_upload(project_id: int, upload_id: int, db: Db, current_user: CurrentUser):
    from youtube.tasks import upload_video

    project = _project_or_404(db, current_user, project_id)
    upload = _upload_or_404(db, project, upload_id)
    try:
        _publishing().retry_upload(db, upload)
    except ValueError as e:
        raise _bad_request(e)
    _enqueue(upload_video, upload.id, current_user.business_id)
    return _projects().serialize_project(db, project)


# ---------------------------------------------------------------------------
#  Brand assets and end card
# ---------------------------------------------------------------------------
def _assets():
    from youtube import brand_assets
    return brand_assets


def _asset_or_404(db: Session, user, asset_id: int):
    asset = _assets().get_asset(db, user.business_id, asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such asset")
    return asset


@router.get("/assets")
def list_assets(db: Db, current_user: CurrentUser):
    a = _assets()
    return {"assets": [a.serialize_asset(x) for x in a.list_assets(db, current_user.business_id)]}


@router.post("/assets", status_code=status.HTTP_201_CREATED)
async def create_asset(db: Db, current_user: CurrentUser, name: str = Form(...), kind: str = Form(...),
                       file: UploadFile = File(...)):
    """Upload a product photo or a logo. Name products as scripts call them, so shots find them."""
    a = _assets()
    data = await _read_upload(file)
    try:
        return a.serialize_asset(a.create_asset(db, current_user.business_id, name, kind, data, StorageSingleton.get()))
    except ValueError as e:
        raise _bad_request(e)


@router.delete("/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_asset(asset_id: int, db: Db, current_user: CurrentUser):
    _assets().delete_asset(db, _asset_or_404(db, current_user, asset_id), StorageSingleton.get())
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/assets/{asset_id}/image")
def asset_image(asset_id: int, db: Db, current_user: CurrentUser, as_link: bool = False):
    asset = _asset_or_404(db, current_user, asset_id)
    ext = asset.storage_key.rsplit(".", 1)[-1].lower()
    media_type = _assets().AUDIO_TYPES.get(ext) or _storyboard().mime_for_key(asset.storage_key)
    return _file_response(asset.storage_key, asset.storage_key.rsplit("/", 1)[-1], as_link,
                          media_type=media_type, inline=True)


@router.get("/projects/{project_id}/end-card/preview")
def end_card_preview(project_id: int, db: Db, current_user: CurrentUser, as_link: bool = False):
    """The end card as it will appear, at half size."""
    from youtube.media import SIZES

    project = _project_or_404(db, current_user, project_id)
    card = _assets().active_end_card(project)
    if card is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The end card is off")
    if as_link:
        return {"url": None}  # drawn on request, so the browser downloads it through this route
    width, height = SIZES[project.format]
    png = _assets().render_end_card(db, project, StorageSingleton.get(), width // 2, height // 2)
    return Response(content=png, media_type="image/png", headers={"Content-Disposition": 'inline; filename="end-card.png"'})


# ---------------------------------------------------------------------------
#  Series
# ---------------------------------------------------------------------------
def _series():
    from youtube import series
    return series


def _series_or_404(db: Session, user, series_id: int):
    series = _series().get_series(db, user.business_id, series_id)
    if series is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such series")
    return series


@router.get("/series")
def list_series(db: Db, current_user: CurrentUser):
    """Series this business has, with how many episodes each holds."""
    s = _series()
    return {"series": [s.serialize_series(db, x) for x in s.list_series(db, current_user.business_id)]}


@router.post("/series", status_code=status.HTTP_201_CREATED)
def create_series(body: SeriesIn, db: Db, current_user: CurrentUser):
    s = _series()
    try:
        return s.serialize_series(db, s.create_series(db, current_user.business_id, body.name, body.logline))
    except ValueError as e:
        raise _bad_request(e)


@router.get("/series/{series_id}")
def get_series(series_id: int, db: Db, current_user: CurrentUser):
    """One series with its episodes in order, and what each left behind (its recap)."""
    s = _series()
    return s.serialize_series(db, _series_or_404(db, current_user, series_id), with_episodes=True)


@router.patch("/series/{series_id}")
def update_series(series_id: int, body: SeriesPatch, db: Db, current_user: CurrentUser):
    s = _series()
    try:
        return s.serialize_series(db, s.update_series(db, _series_or_404(db, current_user, series_id),
                                                      name=body.name, logline=body.logline), with_episodes=True)
    except ValueError as e:
        raise _bad_request(e)


@router.delete("/series/{series_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_series(series_id: int, db: Db, current_user: CurrentUser):
    """Delete the series. Its episodes stay as projects of their own."""
    _series().delete_series(db, _series_or_404(db, current_user, series_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/assets/draw", status_code=status.HTTP_201_CREATED)
def draw_asset(body: AssetDrawIn, db: Db, current_user: CurrentUser):
    """Draw a location (or product) from a description instead of uploading a photo."""
    a = _assets()
    try:
        asset = a.generate_asset_image(db, current_user.business_id, body.name, body.kind, body.description,
                                       StorageSingleton.get())
    except ValueError as e:
        raise _bad_request(e)
    return a.serialize_asset(asset)
