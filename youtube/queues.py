"""Celery queues for YouTube Automation, kept apart from marketing's queues.

    yt_plan     scene planning and metadata (LLM calls)
    yt_media    voice, images and avatar API calls (mostly waiting on the network)
    yt_render   ffmpeg composition (CPU-heavy; runs off the production VM)
    yt_publish  YouTube uploads (limited by the API quota)

Registered onto the shared Celery app only by processes that import
youtube.tasks, so marketing workers never listen on them. A YouTube worker is
started with:

    celery -A youtube.tasks worker --queues yt_plan,yt_media,yt_publish
    celery -A youtube.tasks worker --queues yt_render
"""
from kombu import Exchange, Queue

YT_QUEUE_NAMES = ("yt_plan", "yt_media", "yt_render", "yt_publish")

# Task name prefix -> queue. Tasks are named "yt.<stage>.<action>".
YT_TASK_ROUTES = {
    "yt.plan.*": {"queue": "yt_plan"},
    "yt.media.*": {"queue": "yt_media"},
    "yt.render.*": {"queue": "yt_render"},
    "yt.publish.*": {"queue": "yt_publish"},
}


def yt_queues() -> list[Queue]:
    return [
        Queue(name, durable=True, exchange=Exchange(name, type="direct"), routing_key=name)
        for name in YT_QUEUE_NAMES
    ]


def register_queues(celery_app) -> None:
    """Add the yt_ queues and routes to a Celery app without touching existing ones. Idempotent."""
    conf = celery_app.conf
    existing = list(conf.task_queues or [])
    known = {q.name for q in existing}
    conf.task_queues = existing + [q for q in yt_queues() if q.name not in known]

    routes = conf.task_routes or {}
    if isinstance(routes, dict):
        conf.task_routes = {**routes, **YT_TASK_ROUTES}
    else:
        # Celery also accepts a list or tuple of routers; keep them and add ours.
        conf.task_routes = [*routes, YT_TASK_ROUTES]
