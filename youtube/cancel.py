"""Stopping a step that is already running, without losing what it has done.

Every long step in this pipeline — voicing, drawing, animating — is a loop over
shots that commits after each one. That is what makes stopping cheap: the work
already paid for is on disk and in the database, so a stop is a decision to do
no *more* work, not a decision to throw away what exists.

It has to be cooperative. Killing the worker mid-shot would abandon a clip that
an avatar provider has already been paid for, and terminating a Celery task
holding an open transaction leaves the project row claimed forever. So the API
writes a flag, and the worker reads it between shots and raises `Cancelled`.

The other half is a worker that dies without writing anything — the machine
reboots, the container is replaced mid-render. The project keeps whatever busy
status it was claimed with, `_require_not_busy` refuses every subsequent
request, and the project cannot be stopped, resumed or deleted: it is simply
stuck, which is the state this module's `is_stale` exists to end.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from youtube.models import YTProject

logger = logging.getLogger(__name__)


class Cancelled(Exception):
    """Raised inside a step when someone asked for it to stop.

    Distinct from ProjectError, which means the step could not do its work. A
    stop is not a failure, and a project that was stopped must not be presented
    as one.
    """

    def __init__(self, message: str = "Stopped"):
        super().__init__(message)
        self.message = message


# How long a claimed project may go without its worker finishing before the
# claim is treated as abandoned. Each is comfortably past that step's Celery
# time_limit (see youtube/tasks.py), so a slow step is never mistaken for a
# dead one: the worker gets killed by its own limit first and records a
# failure, and only silence beyond that counts as abandonment.
STALE_AFTER = {
    "planning": timedelta(minutes=15),
    "voicing": timedelta(hours=2),
    "drawing": timedelta(hours=2),
    "rendering": timedelta(hours=3),
    "uploading": timedelta(hours=3),
}
DEFAULT_STALE_AFTER = timedelta(hours=3)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    """Postgres returns aware datetimes; SQLite in tests returns naive ones."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def is_stale(project: YTProject) -> bool:
    """Whether this project's busy status belongs to a worker that is gone.

    A project claimed before this column existed has no `busy_since`. It is
    treated as stale rather than stuck forever: the claim predates the deploy,
    so no worker is still holding it.
    """
    from youtube.projects import BUSY_STATUSES

    if project.status not in BUSY_STATUSES:
        return False
    since = _aware(getattr(project, "busy_since", None))
    if since is None:
        return True
    return _now() - since > STALE_AFTER.get(project.status, DEFAULT_STALE_AFTER)


def request(db: Session, project: YTProject) -> None:
    """Ask the running step to stop at its next shot."""
    project.cancel_requested_at = _now()
    db.commit()
    logger.info("Stop requested for YouTube project %s (%s)", project.id, project.status)


def clear(project: YTProject) -> None:
    project.cancel_requested_at = None
    project.busy_since = None


def is_requested(db: Session, project_id: int) -> bool:
    """Whether a stop was asked for, read fresh from the database.

    Queried by id rather than read off the loaded instance: the flag is written
    by the API process while the worker holds its own session, and an attribute
    already loaded in that session's identity map would never change.
    """
    row = db.execute(
        select(YTProject.cancel_requested_at).where(YTProject.id == project_id)
    ).first()
    return bool(row and row[0])


def check(db: Session, project_id: int, did: str = "") -> None:
    """Raise `Cancelled` if this step should stop. Called between units of work.

    Also stops when the project row has gone, which is how deleting a running
    project works: the delete removes the row and the next checkpoint finds
    nothing to keep working on.
    """
    row = db.execute(
        select(YTProject.id, YTProject.cancel_requested_at).where(YTProject.id == project_id)
    ).first()
    if row is None:
        raise Cancelled("The project was deleted while this was running")
    if row[1]:
        raise Cancelled(f"Stopped{f' after {did}' if did else ''}")
