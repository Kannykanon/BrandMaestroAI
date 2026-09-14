"""HTTP routes for YouTube Automation, mounted at /youtube.

Every route acts only on the signed-in user's business. Slow steps (planning,
voicing, voice previews) are queued on the yt_ queues and the project's status
reports progress.
"""
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
from youtube.eligibility import get_eligible_script, list_eligible_scripts
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


class CastIn(BaseModel):
    # speaker label -> character id, or null to unassign
    assignments: dict[str, Optional[int]]


class ShotPatch(BaseModel):
    speaker: str = Field(..., min_length=1, max_length=100)


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


def _audio_response(key: str, filename: str, as_link: bool = False):
    """Stream an audio file, or with as_link return {"url": ...} for the browser to load directly.

    Browsers cannot attach the bearer token to an <audio> element, and a
    redirect to a storage bucket would need CORS on the bucket. So the UI asks
    for a link: a signed URL when the store can make one (GCS), otherwise null,
    in which case it downloads the file through this route with its token.
    """
    storage = StorageSingleton.get()
    if as_link:
        try:
            if not storage.exists(key):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audio file is missing")
            return {"url": storage.url(key)}
        except HTTPException:
            raise
        except Exception:
            return {"url": None}  # e.g. credentials that cannot sign; stream instead
    try:
        data = storage.get(key)
    except FileNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audio file is missing")
    return Response(content=data, media_type="audio/wav",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


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

    return {
        "storage": storage_status,
        "voice": voice_status,
        # Filled in by later phases.
        "channel_connected": False,
    }


@router.get("/scripts")
async def eligible_scripts(db: Db, current_user: CurrentUser):
    """Approved marketing scripts this business can turn into videos, newest first.

    Each carries `approval`: "human" (a person approved it) or "enforcer"
    (approved automatically, not reviewed by a person).
    """
    scripts = list_eligible_scripts(db, current_user.business_id)
    return {"scripts": [s.to_summary() for s in scripts]}


@router.get("/scripts/{generation_id}")
async def eligible_script(generation_id: str, db: Db, current_user: CurrentUser):
    """The full text of one eligible script. 404 if it is missing, not yours, or not eligible."""
    script = get_eligible_script(db, current_user.business_id, generation_id)
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
    return _audio_response(key, f"{voice_id}.wav", as_link)


# ---------------------------------------------------------------------------
#  Characters
# ---------------------------------------------------------------------------
@router.get("/characters")
def list_characters(db: Db, current_user: CurrentUser):
    p = _projects()
    return {"characters": [p.serialize_character(c) for c in p.list_characters(db, current_user.business_id)]}


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
    p.delete_character(db, character)
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
    p = _projects()
    try:
        project = p.create_project(db, current_user.business_id, body.generation_id, body.format)
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
    """Change who speaks a shot. The words cannot be edited here: that happens in marketing."""
    p = _projects()
    project = _project_or_404(db, current_user, project_id)
    try:
        p.reassign_speaker(db, project, shot_id, body.speaker)
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
    return _audio_response(project.audio_key, f"project-{project.id}-voice.wav", as_link)


@router.get("/projects/{project_id}/shots/{shot_id}/audio")
def shot_audio(project_id: int, shot_id: int, db: Db, current_user: CurrentUser, as_link: bool = False):
    from youtube.models import YTShot

    project = _project_or_404(db, current_user, project_id)
    shot = db.get(YTShot, shot_id)
    if shot is None or shot.project_id != project.id or not shot.audio_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No audio for that shot")
    return _audio_response(shot.audio_key, f"project-{project.id}-shot-{shot.position}.wav", as_link)
