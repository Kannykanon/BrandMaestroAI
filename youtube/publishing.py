"""Channel connection, private uploads, and publishing with a person's sign-off.

    channel   the owner connects one YouTube channel with Google sign-in; the refresh
              token is stored encrypted (youtube/token_crypto.py)
    upload    a person confirms they watched the current render, then it is uploaded as
              private on the yt_publish queue, waiting for quota when the day's is used up
    publish   a person makes the private video public, or schedules it
    refresh   YouTube's view of the video (processing, privacy, rejection) is read back

    YT_OAUTH_REDIRECT_URI  the callback registered on the OAuth client. Set it in production: the
                           default is built from the request, which behind a TLS proxy may say http
"""
from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from youtube import quota
from youtube.metadata import metadata_problems, serialize_metadata, video_metadata
from youtube.models import YTChannel, YTProject, YTRender, YTUpload
from youtube.projects import ProjectError
from youtube.publisher import AuthError, PublisherPort, PublisherSingleton, QuotaExhausted
from youtube.token_crypto import SecretError, decrypt, encrypt

logger = logging.getLogger(__name__)

STATE_TTL = timedelta(minutes=15)
STALE_CLAIM = timedelta(hours=3)  # longer than the upload task's time limit
ACTIVE_UPLOADS = ("queued", "waiting_quota", "uploading")
ON_YOUTUBE = ("uploaded", "scheduled", "published")
CALLBACK_PATH = "/youtube/channel/callback"
LOCKED_MESSAGE = ("YouTube kept this video private. Videos uploaded from a Google Cloud project that has not "
                  "passed YouTube's API compliance audit are locked private, and cannot be made public even in "
                  "YouTube Studio. Request the audit for this project, then upload again.")


def _now(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now if now.tzinfo else now.replace(tzinfo=timezone.utc)


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
#  Channel
# ---------------------------------------------------------------------------
def redirect_uri(request_base: Optional[str] = None) -> str:
    configured = os.getenv("YT_OAUTH_REDIRECT_URI", "").strip()
    if configured:
        return configured
    return (request_base or "").rstrip("/") + CALLBACK_PATH


def _secret() -> str:
    from Settings import settings
    return settings.secret_key.get_secret_value()


def make_state(business_id: str) -> tuple[str, str]:
    """A signed state for the Google redirect, and a nonce the browser keeps in a cookie.

    The state names the business; the nonce ties the callback to the browser that
    started it, so a sign-in link cannot be used to attach someone else's channel.
    """
    from jose import jwt

    nonce = secrets.token_urlsafe(24)
    state = jwt.encode({"sub": business_id, "purpose": "yt_channel", "nonce": nonce,
                        "exp": _now() + STATE_TTL}, _secret(), algorithm="HS256")
    return state, nonce


def read_state(state: str, nonce: Optional[str]) -> str:
    from jose import JWTError, jwt

    try:
        payload = jwt.decode(state, _secret(), algorithms=["HS256"])
    except JWTError as e:
        raise ProjectError("The sign-in link expired or is invalid; connect the channel again") from e
    if payload.get("purpose") != "yt_channel" or not payload.get("sub"):
        raise ProjectError("The sign-in link is invalid; connect the channel again")
    if not nonce or not secrets.compare_digest(str(payload.get("nonce")), nonce):
        raise ProjectError("Finish connecting in the same browser you started from")
    return payload["sub"]


def get_channel(db: Session, business_id: str) -> Optional[YTChannel]:
    return db.execute(select(YTChannel).where(YTChannel.business_id == business_id)).scalars().first()


def connect_channel(db: Session, business_id: str, code: str, redirect: str,
                    publisher: Optional[PublisherPort] = None) -> YTChannel:
    publisher = publisher or PublisherSingleton.get()
    refresh_token = publisher.exchange_code(code, redirect)
    info = publisher.channel(refresh_token)
    channel = get_channel(db, business_id)
    if channel is None:
        channel = YTChannel(business_id=business_id)
        db.add(channel)
    channel.channel_id, channel.channel_title = info.channel_id, info.title
    channel.refresh_token_encrypted = encrypt(refresh_token)
    channel.connected_at, channel.token_error = _now(), None
    db.commit()
    db.refresh(channel)
    return channel


def disconnect_channel(db: Session, business_id: str, publisher: Optional[PublisherPort] = None) -> None:
    channel = get_channel(db, business_id)
    if channel is None:
        return
    publisher = publisher or PublisherSingleton.get()
    if channel.refresh_token_encrypted:
        try:
            publisher.revoke(decrypt(channel.refresh_token_encrypted))
        except SecretError:
            pass
    db.delete(channel)
    db.commit()


def _token(db: Session, business_id: str) -> tuple[YTChannel, str]:
    channel = get_channel(db, business_id)
    if channel is None or not channel.refresh_token_encrypted:
        raise ProjectError("Connect a YouTube channel first")
    if channel.token_error:
        raise ProjectError(f"Connect the YouTube channel again: {channel.token_error}")
    try:
        return channel, decrypt(channel.refresh_token_encrypted)
    except SecretError as e:
        _token_failed(db, channel, str(e))
        raise ProjectError(str(e)) from e


def _token_failed(db: Session, channel: YTChannel, message: str) -> None:
    channel.token_error = message[:2000]
    db.commit()


def serialize_channel(db: Session, business_id: str, request_base: Optional[str] = None,
                      publisher: Optional[PublisherPort] = None) -> dict:
    publisher = publisher or PublisherSingleton.get()
    channel = get_channel(db, business_id)
    return {
        "configured": not publisher.missing_env(),
        "missing": publisher.missing_env(),
        "redirect_uri": redirect_uri(request_base),
        "connected": bool(channel and channel.refresh_token_encrypted),
        "channel_id": channel.channel_id if channel else None,
        "channel_title": channel.channel_title if channel else None,
        "channel_url": f"https://www.youtube.com/channel/{channel.channel_id}" if channel and channel.channel_id else None,
        "connected_at": _aware(channel.connected_at).isoformat() if channel and channel.connected_at else None,
        "token_error": channel.token_error if channel else None,
        "quota": quota.usage(db),
    }


# ---------------------------------------------------------------------------
#  Uploads
# ---------------------------------------------------------------------------
def _project_render(db: Session, upload: YTUpload) -> tuple[YTProject, YTRender]:
    render = db.get(YTRender, upload.render_id)
    return db.get(YTProject, render.project_id), render


def uploads_for(db: Session, project: YTProject) -> list[YTUpload]:
    return db.execute(select(YTUpload).join(YTRender, YTUpload.render_id == YTRender.id)
                      .where(YTRender.project_id == project.id).order_by(YTUpload.id.desc())).scalars().all()


def get_upload(db: Session, project: YTProject, upload_id: int) -> Optional[YTUpload]:
    upload = db.get(YTUpload, upload_id)
    if upload is None:
        return None
    render = db.get(YTRender, upload.render_id)
    return upload if render is not None and render.project_id == project.id else None


def upload_state_for_render(db: Session, render: YTRender) -> Optional[str]:
    """The project status a render's latest upload implies, or None if it has none that counts."""
    upload = db.execute(select(YTUpload).where(YTUpload.render_id == render.id)
                        .order_by(YTUpload.id.desc())).scalars().first()
    if upload is None:
        return None
    return {"queued": "uploading", "waiting_quota": "uploading", "uploading": "uploading",
            "uploaded": "uploaded_private", "scheduled": "scheduled", "published": "published"}.get(upload.status)


def upload_problems(db: Session, project: YTProject, publisher: Optional[PublisherPort] = None) -> list[str]:
    from youtube.render import latest_render, render_is_current

    publisher = publisher or PublisherSingleton.get()
    problems = []
    if publisher.missing_env():
        problems.append(f"YouTube sign-in is not configured (missing {', '.join(publisher.missing_env())})")
    channel = get_channel(db, project.business_id)
    if channel is None or not channel.refresh_token_encrypted:
        problems.append("Connect a YouTube channel first")
    elif channel.token_error:
        problems.append("Connect the YouTube channel again")
    render = latest_render(db, project)
    if not render_is_current(render, project):
        problems.append("Render the video from the approved storyboard first")
    else:
        latest = db.execute(select(YTUpload).where(YTUpload.render_id == render.id)
                            .order_by(YTUpload.id.desc())).scalars().first()
        if latest is not None and latest.status in ACTIVE_UPLOADS:
            problems.append("This video is already being uploaded")
        elif latest is not None and latest.status in ON_YOUTUBE:
            problems.append("This render is already on YouTube")
    problems += metadata_problems(project)
    return problems


def queue_upload(db: Session, project: YTProject, reviewed: bool,
                 publisher: Optional[PublisherPort] = None) -> YTUpload:
    """Create an upload for the current render. The caller enqueues the task."""
    from youtube import projects
    from youtube.render import latest_render

    if not reviewed:
        raise ProjectError("Watch the rendered video and confirm it is ready before uploading")
    problems = upload_problems(db, project, publisher)
    if problems:
        raise ProjectError("; ".join(problems))
    projects._require_not_busy(project)
    render = latest_render(db, project)
    upload = YTUpload(render_id=render.id, status="queued", privacy="private", title=project.video_title)
    db.add(upload)
    project.status, project.error = "uploading", None
    db.commit()
    db.refresh(upload)
    return upload


def claim_upload(db: Session, upload_id: int, now: Optional[datetime] = None) -> bool:
    """Take the upload for this worker. False if it is not due, or another worker holds a fresh claim."""
    now = _now(now)
    result = db.execute(
        update(YTUpload)
        .where(YTUpload.id == upload_id)
        .where(or_(
            (YTUpload.status == "queued"),
            (YTUpload.status == "waiting_quota") & (or_(YTUpload.retry_at.is_(None), YTUpload.retry_at <= now)),
            (YTUpload.status == "uploading") & (or_(YTUpload.claimed_at.is_(None),
                                                    YTUpload.claimed_at <= now - STALE_CLAIM)),
        ))
        .values(status="uploading", claimed_at=now, retry_at=None)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return result.rowcount == 1


@dataclass
class UploadOutcome:
    status: str
    retry_at: Optional[datetime] = None


def _settle(db: Session, project: YTProject) -> None:
    """Re-derive the project's status, unless another background step (a render) owns it right now."""
    from youtube import projects
    if project.status not in projects.BUSY_STATUSES - {"uploading"}:
        projects.settle_status(db, project)
    db.commit()


def _wait_for_quota(db: Session, upload: YTUpload, project: YTProject, retry_at: datetime, message: str) -> UploadOutcome:
    upload.status, upload.retry_at, upload.error = "waiting_quota", retry_at, message
    _settle(db, project)
    return UploadOutcome("waiting_quota", retry_at)


def run_upload(db: Session, upload: YTUpload, storage, publisher: Optional[PublisherPort] = None,
               now: Optional[datetime] = None) -> UploadOutcome:
    """Upload a claimed upload as private, then try to set its thumbnail."""
    publisher = publisher or PublisherSingleton.get()
    project, render = _project_render(db, upload)
    try:
        channel, refresh_token = _token(db, project.business_id)
    except ProjectError as e:
        return fail_upload(db, upload, str(e))

    if not upload.upload_url:  # a resumed session was already counted when it started
        try:
            quota.reserve(db, "upload", now)
        except quota.QuotaExceeded as e:
            return _wait_for_quota(db, upload, project, e.retry_at, str(e))

    video = storage.get(render.video_key)
    last_saved = {"progress": upload.progress or 0.0}

    def on_session(url: str) -> None:
        upload.upload_url = url
        db.commit()

    def on_progress(sent: int, total: int) -> None:
        progress = round(sent / total, 3) if total else 1.0
        if progress - last_saved["progress"] >= 0.05 or progress >= 1.0:
            upload.progress, last_saved["progress"] = progress, progress
            db.commit()

    try:
        video_id = publisher.upload_private(refresh_token, video, video_metadata(project),
                                            session_url=upload.upload_url, on_session=on_session,
                                            on_progress=on_progress)
    except AuthError as e:
        _token_failed(db, channel, str(e))
        return fail_upload(db, upload, str(e))
    except QuotaExhausted as e:
        retry_at = quota.exhaust(db, "upload", now)
        return _wait_for_quota(db, upload, project, retry_at, str(e))

    upload.youtube_video_id, upload.status, upload.privacy = video_id, "uploaded", "private"
    upload.uploaded_at, upload.progress, upload.upload_url, upload.error = _now(now), 1.0, None, None
    upload.youtube_status = "uploaded"
    db.commit()
    _set_thumbnail(db, upload, render, storage, publisher, refresh_token, now)
    _settle(db, project)
    return UploadOutcome("uploaded")


def _set_thumbnail(db, upload, render, storage, publisher, refresh_token, now) -> None:
    if not render.thumbnail_key:
        return
    try:
        quota.reserve(db, "thumbnail", now)
        publisher.set_thumbnail(refresh_token, upload.youtube_video_id, storage.get(render.thumbnail_key))
        upload.thumbnail_error = None
    except quota.QuotaExceeded:
        upload.thumbnail_error = "Not set: today's quota is used up. Set it in YouTube Studio."
    except Exception as e:  # e.g. custom thumbnails need a channel verified by phone
        upload.thumbnail_error = f"Not set: {e}"[:2000]
    db.commit()


def fail_upload(db: Session, upload: YTUpload, message: str) -> UploadOutcome:
    upload.status, upload.error, upload.claimed_at = "failed", message[:2000], None
    project, _ = _project_render(db, upload)
    db.commit()
    _settle(db, project)
    return UploadOutcome("failed")


def cancel_upload(db: Session, upload: YTUpload) -> YTUpload:
    if upload.status not in ("queued", "waiting_quota"):
        raise ProjectError("Only an upload that has not started can be cancelled")
    upload.status, upload.retry_at = "cancelled", None
    project, _ = _project_render(db, upload)
    _settle(db, project)
    return upload


def retry_upload(db: Session, upload: YTUpload) -> YTUpload:
    """Queue a failed upload, or one waiting for quota, again. The caller enqueues the task."""
    if upload.status not in ("failed", "waiting_quota") or upload.youtube_video_id:
        raise ProjectError("Only a failed upload, or one waiting for quota, can be tried again")
    project, render = _project_render(db, upload)
    from youtube.render import latest_render
    if upload.status == "failed":
        latest = latest_render(db, project)
        if latest is None or latest.id != render.id:
            raise ProjectError("A newer render exists; upload that one instead")
        if project.status not in ("uploading",):
            from youtube import projects
            projects._require_not_busy(project)
    upload.status, upload.retry_at, upload.error, upload.claimed_at = "queued", None, None, None
    project.status = "uploading"
    db.commit()
    return upload


# ---------------------------------------------------------------------------
#  Publish and refresh
# ---------------------------------------------------------------------------
def _call(db: Session, channel: YTChannel, action: str, fn, now=None):
    """Count quota for one API call and run it, recording a refused sign-in on the channel."""
    try:
        quota.reserve(db, action, now)
    except quota.QuotaExceeded as e:
        raise ProjectError(str(e)) from e
    try:
        return fn()
    except AuthError as e:
        _token_failed(db, channel, str(e))
        raise ProjectError(str(e)) from e
    except QuotaExhausted as e:
        quota.exhaust(db, action, now)
        raise ProjectError(f"{e}; try again after midnight Pacific time") from e


def publish_upload(db: Session, upload: YTUpload, publish_at: Optional[datetime] = None,
                   publisher: Optional[PublisherPort] = None, now: Optional[datetime] = None) -> YTUpload:
    """Make a private upload public now, or schedule it. A person does this after watching it on YouTube."""
    publisher = publisher or PublisherSingleton.get()
    now = _now(now)
    if upload.status not in ("uploaded", "scheduled") or not upload.youtube_video_id:
        raise ProjectError("Only a video that is on YouTube and not yet public can be published")
    publish_at = _aware(publish_at)
    if publish_at is not None and publish_at < now + timedelta(minutes=5):
        raise ProjectError("Schedule the video at least 5 minutes ahead, or publish it now")
    project, _ = _project_render(db, upload)
    channel, refresh_token = _token(db, project.business_id)
    metadata = video_metadata(project)
    privacy = "private" if publish_at else "public"
    _call(db, channel, "update",
          lambda: publisher.set_privacy(refresh_token, upload.youtube_video_id, metadata, privacy, publish_at), now)
    status = _call(db, channel, "list", lambda: publisher.video_status(refresh_token, upload.youtube_video_id), now)

    upload.privacy = status.privacy or privacy
    upload.youtube_status = status.upload_status
    locked = (status.privacy != "public") if publish_at is None else not status.publish_at
    if locked:
        upload.error = LOCKED_MESSAGE
        _settle(db, project)
        raise ProjectError(LOCKED_MESSAGE)
    if publish_at is None:
        upload.status, upload.published_at, upload.publish_at = "published", now, None
    else:
        upload.status, upload.publish_at = "scheduled", publish_at
    upload.error = None
    _settle(db, project)
    return upload


def refresh_upload(db: Session, upload: YTUpload, publisher: Optional[PublisherPort] = None,
                   now: Optional[datetime] = None) -> YTUpload:
    """Read the video's state back from YouTube: processing, privacy, rejection, scheduled publishing."""
    publisher = publisher or PublisherSingleton.get()
    now = _now(now)
    if not upload.youtube_video_id:
        raise ProjectError("This upload has not reached YouTube")
    project, _ = _project_render(db, upload)
    channel, refresh_token = _token(db, project.business_id)
    status = _call(db, channel, "list", lambda: publisher.video_status(refresh_token, upload.youtube_video_id), now)

    upload.youtube_status, upload.privacy = status.upload_status, status.privacy or upload.privacy
    if status.upload_status in ("failed", "rejected", "deleted"):
        reason = status.failure_reason or status.rejection_reason
        upload.error = f"YouTube reports the video {status.upload_status}" + (f" ({reason})" if reason else "")
    elif status.privacy == "public" and upload.status != "published":
        upload.status, upload.published_at = "published", upload.publish_at or now
        upload.error = None
    _settle(db, project)
    return upload


def serialize_upload(upload: YTUpload) -> dict:
    video_id = upload.youtube_video_id
    return {
        "id": upload.id,
        "render_id": upload.render_id,
        "status": upload.status,
        "title": upload.title,
        "privacy": upload.privacy,
        "progress": upload.progress,
        "error": upload.error,
        "thumbnail_error": upload.thumbnail_error,
        "youtube_status": upload.youtube_status,
        "youtube_video_id": video_id,
        "watch_url": f"https://youtu.be/{video_id}" if video_id else None,
        "studio_url": f"https://studio.youtube.com/video/{video_id}/edit" if video_id else None,
        "retry_at": _aware(upload.retry_at).isoformat() if upload.retry_at else None,
        "publish_at": _aware(upload.publish_at).isoformat() if upload.publish_at else None,
        "published_at": _aware(upload.published_at).isoformat() if upload.published_at else None,
        "uploaded_at": _aware(upload.uploaded_at).isoformat() if upload.uploaded_at else None,
        "created_at": _aware(upload.created_at).isoformat() if upload.created_at else None,
    }


def publishing_summary(db: Session, project: YTProject) -> dict:
    channel = get_channel(db, project.business_id)
    try:
        problems = upload_problems(db, project)
    except Exception as e:  # never let publishing break the project page
        logger.warning("Upload checks failed for project %s: %s", project.id, e)
        problems = [str(e)]
    return {
        "metadata": serialize_metadata(project),
        "uploads": [serialize_upload(u) for u in uploads_for(db, project)],
        "upload_problems": problems,
        "channel": {"connected": bool(channel and channel.refresh_token_encrypted),
                    "title": channel.channel_title if channel else None,
                    "token_error": channel.token_error if channel else None},
    }
