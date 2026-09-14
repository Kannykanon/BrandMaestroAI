"""Celery entry point for YouTube Automation workers.

Imports the shared Celery app and adds the yt_ queues to it (see
youtube/queues.py). Tasks for each pipeline stage are added here as the stages
are built; phase 0 only establishes the queues.
"""
from celery_task import celery_app
from youtube.queues import register_queues

register_queues(celery_app)

__all__ = ["celery_app"]
