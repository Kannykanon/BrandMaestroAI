"""Celery tasks for YouTube Automation, on the yt_ queues (see youtube/queues.py).

Start workers with:
    celery -A youtube.tasks worker --queues yt_plan,yt_media,yt_publish --concurrency 1
    celery -A youtube.tasks worker --queues yt_render --concurrency 1
"""
import logging

from celery_task import celery_app
from database import get_db_session
from youtube.queues import register_queues

register_queues(celery_app)

logger = logging.getLogger(__name__)


def _run(project_id: int, business_id: str, action, busy_status: str):
    """Load the project, run one step, and record failure on the project instead of losing it."""
    from youtube import projects
    from youtube.storage import StorageSingleton

    with get_db_session() as db:
        project = projects.get_project(db, business_id, project_id)
        if project is None:
            logger.warning("YouTube project %s not found for business %s", project_id, business_id)
            return {"status": "missing"}
        try:
            action(db, project, StorageSingleton.get())
            return {"status": project.status}
        except Exception as e:
            logger.exception("YouTube project %s failed while %s", project_id, busy_status)
            db.rollback()
            project = projects.get_project(db, business_id, project_id)
            if project is not None:
                projects.fail(db, project, f"{busy_status.capitalize()} failed: {e}")
            return {"status": "failed", "error": str(e)}


@celery_app.task(name="yt.plan.plan_project", acks_late=True, soft_time_limit=300, time_limit=360)
def plan_project(project_id: int, business_id: str):
    from youtube import projects

    return _run(project_id, business_id,
                lambda db, project, storage: projects.plan_project(db, project, storage=storage),
                "planning")


@celery_app.task(name="yt.media.voice_project", acks_late=True, soft_time_limit=3600, time_limit=3720)
def voice_project(project_id: int, business_id: str, force: bool = False):
    from youtube import projects

    return _run(project_id, business_id,
                lambda db, project, storage: projects.voice_project(db, project, storage, force=force),
                "voicing")


@celery_app.task(name="yt.media.storyboard_project", acks_late=True, soft_time_limit=3600, time_limit=3720)
def storyboard_project(project_id: int, business_id: str, force: bool = False, shot_ids: list | None = None):
    from youtube import storyboard

    return _run(project_id, business_id,
                lambda db, project, storage: storyboard.generate_storyboard(
                    db, project, storage, force=force, shot_ids=shot_ids),
                "drawing")


@celery_app.task(name="yt.media.character_sheet", acks_late=True, soft_time_limit=600, time_limit=660)
def character_sheet(character_id: int, business_id: str):
    from youtube import projects, storyboard
    from youtube.storage import StorageSingleton

    with get_db_session() as db:
        character = projects.get_character(db, business_id, character_id)
        if character is None:
            return {"status": "missing"}
        try:
            storyboard.generate_sheet(db, character, StorageSingleton.get())
            return {"status": "ready"}
        except Exception as e:
            logger.exception("Character sheet failed for character %s", character_id)
            db.rollback()
            character = projects.get_character(db, business_id, character_id)
            if character is not None:
                storyboard.fail_sheet(db, character, f"Sheet generation failed: {e}")
            return {"status": "failed", "error": str(e)}


@celery_app.task(name="yt.render.render_project", acks_late=True, soft_time_limit=7200, time_limit=7320)
def render_project(project_id: int, business_id: str, confirm_over_budget: bool = False):
    from youtube import render

    return _run(project_id, business_id,
                lambda db, project, storage: render.render_project(
                    db, project, storage, confirm_over_budget=confirm_over_budget),
                "rendering")


@celery_app.task(name="yt.publish.upload_video", acks_late=True, soft_time_limit=7200, time_limit=7320)
def upload_video(upload_id: int, business_id: str):
    """Upload a queued video as private. When quota is used up, the task schedules itself for after the reset."""
    from youtube import publishing
    from youtube.models import YTProject, YTRender, YTUpload
    from youtube.storage import StorageSingleton

    with get_db_session() as db:
        upload = db.get(YTUpload, upload_id)
        render = db.get(YTRender, upload.render_id) if upload else None
        project = db.get(YTProject, render.project_id) if render else None
        if project is None or project.business_id != business_id:
            logger.warning("YouTube upload %s not found for business %s", upload_id, business_id)
            return {"status": "missing"}
        if not publishing.claim_upload(db, upload_id):
            return {"status": "skipped"}  # not due yet, or another worker has it
        db.refresh(upload)
        try:
            outcome = publishing.run_upload(db, upload, StorageSingleton.get())
        except Exception as e:
            logger.exception("YouTube upload %s failed", upload_id)
            db.rollback()
            upload = db.get(YTUpload, upload_id)
            outcome = publishing.fail_upload(db, upload, f"Uploading failed: {e}")
        if outcome.retry_at is not None:
            upload_video.apply_async((upload_id, business_id), eta=outcome.retry_at)
        return {"status": outcome.status, "retry_at": outcome.retry_at.isoformat() if outcome.retry_at else None}


@celery_app.task(name="yt.media.voice_previews", acks_late=True, soft_time_limit=3600, time_limit=3720)
def voice_previews(provider: str | None = None, force: bool = False):
    from youtube.storage import StorageSingleton
    from youtube.voice_previews import generate_previews

    return generate_previews(StorageSingleton.get(), provider, force=force)


__all__ = ["celery_app", "plan_project", "voice_project", "voice_previews", "storyboard_project", "character_sheet",
           "render_project", "upload_video"]
