"""Celery tasks for YouTube Automation, on the yt_ queues (see youtube/queues.py).

Start a worker with:
    celery -A youtube.tasks worker --queues yt_plan,yt_media --concurrency 1
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


@celery_app.task(name="yt.media.voice_previews", acks_late=True, soft_time_limit=3600, time_limit=3720)
def voice_previews(provider: str | None = None, force: bool = False):
    from youtube.storage import StorageSingleton
    from youtube.voice_previews import generate_previews

    return generate_previews(StorageSingleton.get(), provider, force=force)


__all__ = ["celery_app", "plan_project", "voice_project", "voice_previews", "storyboard_project", "character_sheet"]
