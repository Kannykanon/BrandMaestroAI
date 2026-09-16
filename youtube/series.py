"""Series: several videos that continue one story, with the same cast, world and look.

    series        a named group of projects, numbered as episodes
    inheritance   a new episode starts from the last one: style lock, products and
                  locations, sound, end card, and the same character for each speaker
    story so far  each episode's recap, written when it is planned, is given to the
                  planner of later episodes together with the series logline

Nothing here changes an approved script: the recap is a separate note about the
episode, and the planner only ever decides what a shot shows.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from youtube import projects
from youtube.models import YTCast, YTProject, YTSeries
from youtube.projects import ProjectError

logger = logging.getLogger(__name__)

RECAP_WORDS = 60
RECAP_PROMPT = """Summarise this episode of a video series in at most {words} words, for the writer of the next episode.
Say what happened, where it happened, and where the characters are left. Plain sentences, no title, no commentary.

EPISODE {episode}: {topic}

SCRIPT:
{script}"""


def list_series(db: Session, business_id: str) -> list[YTSeries]:
    return db.execute(select(YTSeries).where(YTSeries.business_id == business_id)
                      .order_by(YTSeries.name)).scalars().all()


def get_series(db: Session, business_id: str, series_id: int) -> Optional[YTSeries]:
    return db.execute(select(YTSeries).where(YTSeries.id == series_id,
                                             YTSeries.business_id == business_id)).scalar_one_or_none()


def create_series(db: Session, business_id: str, name: str, logline: str = "") -> YTSeries:
    name = " ".join((name or "").split())[:200]
    if not name:
        raise ProjectError("Give the series a name")
    if db.execute(select(YTSeries.id).where(YTSeries.business_id == business_id, YTSeries.name == name)).first():
        raise ProjectError(f"A series named {name!r} already exists")
    series = YTSeries(business_id=business_id, name=name, logline=(logline or "").strip()[:2000] or None)
    db.add(series)
    db.commit()
    db.refresh(series)
    return series


def update_series(db: Session, series: YTSeries, name: Optional[str] = None,
                  logline: Optional[str] = None) -> YTSeries:
    if name is not None:
        name = " ".join(name.split())[:200]
        if not name:
            raise ProjectError("Give the series a name")
        series.name = name
    if logline is not None:
        series.logline = logline.strip()[:2000] or None
    db.commit()
    db.refresh(series)
    return series


def delete_series(db: Session, series: YTSeries) -> None:
    """Delete the series. Its episodes stay, as projects of their own."""
    for project in episodes(db, series):
        project.series_id, project.episode = None, None
    db.delete(series)
    db.commit()


def episodes(db: Session, series: YTSeries) -> list[YTProject]:
    return db.execute(select(YTProject).where(YTProject.series_id == series.id)
                      .order_by(YTProject.episode)).scalars().all()


def previous_episode(db: Session, project: YTProject) -> Optional[YTProject]:
    if not project.series_id or not project.episode:
        return None
    return db.execute(select(YTProject).where(YTProject.series_id == project.series_id,
                                              YTProject.episode < project.episode)
                      .order_by(YTProject.episode.desc())).scalars().first()


def add_to_series(db: Session, project: YTProject, series: Optional[YTSeries]) -> YTProject:
    """Put a project in a series as the next episode, and carry over what the last one set up."""
    projects._require_not_busy(project)
    if series is None:
        project.series_id, project.episode = None, None
        db.commit()
        db.refresh(project)
        return project
    if series.business_id != project.business_id:
        raise ProjectError("That series belongs to another business")
    if project.series_id != series.id:
        highest = db.execute(select(func.max(YTProject.episode)).where(YTProject.series_id == series.id)).scalar()
        project.series_id, project.episode = series.id, (highest or 0) + 1
        _inherit(db, project)
    db.commit()
    db.refresh(project)
    return project


def _inherit(db: Session, project: YTProject) -> None:
    """Start this episode from the previous one: look, world, sound, end card and cast."""
    previous = previous_episode(db, project)
    if previous is None:
        return
    if project.style_id is None:
        project.style_id = previous.style_id
    if not project.asset_ids:
        project.asset_ids = list(previous.asset_ids or []) or None
    if not project.audio:
        project.audio = dict(previous.audio) if previous.audio else None
    if not project.end_card:
        project.end_card = dict(previous.end_card) if previous.end_card else None
    inherit_cast(db, project, previous)


def inherit_cast(db: Session, project: YTProject, previous: Optional[YTProject] = None) -> int:
    """Cast each speaker as the character who played them in the previous episode. Returns how many."""
    previous = previous or previous_episode(db, project)
    if previous is None:
        return 0
    played = {row.speaker_label: row.character_id for row in projects._cast(db, previous) if row.character_id}
    filled = 0
    for row in projects._cast(db, project):
        if row.character_id is None and played.get(row.speaker_label):
            row.character_id = played[row.speaker_label]
            filled += 1
    if filled:
        projects.settle_status(db, project)
        db.commit()
    return filled


# ---------------------------------------------------------------------------
#  Story so far
# ---------------------------------------------------------------------------
def write_recap(db: Session, project: YTProject, llm=None) -> Optional[str]:
    """A short note on what happened in this episode, for the planner of the next one."""
    if not project.series_id:
        return None
    from utils.llm_output import message_text

    if llm is None:
        from model import LLMSingleton
        llm = LLMSingleton.get("extraction")
    prompt = RECAP_PROMPT.format(words=RECAP_WORDS, episode=project.episode or 1, topic=project.topic,
                                 script=project.script_snapshot[:8000])
    try:
        text = message_text(llm.invoke(prompt).content).strip()
    except Exception as e:  # a missing recap costs continuity, never the episode
        logger.warning("Recap failed for project %s: %s", project.id, e)
        return None
    recap = " ".join(re.sub(r"\s+", " ", text).split()[:RECAP_WORDS * 2])[:1000]
    project.recap = recap or None
    db.commit()
    return project.recap


def story_so_far(db: Session, project: YTProject) -> str:
    """The series logline and the recaps of earlier episodes, for the planner."""
    if not project.series_id:
        return ""
    series = db.get(YTSeries, project.series_id)
    earlier = db.execute(select(YTProject).where(YTProject.series_id == project.series_id,
                                                 YTProject.episode < (project.episode or 1))
                         .order_by(YTProject.episode)).scalars().all()
    lines = []
    if series and series.logline:
        lines.append(f"SERIES: {series.name} — {series.logline}")
    elif series:
        lines.append(f"SERIES: {series.name}")
    for episode in earlier:
        if episode.recap:
            lines.append(f"Episode {episode.episode}: {episode.recap}")
    if len(lines) <= 1 and not any(e.recap for e in earlier):
        return lines[0] if lines else ""
    return "\n".join(lines)


def serialize_series(db: Session, series: YTSeries, with_episodes: bool = False) -> dict:
    data = {"id": series.id, "name": series.name, "logline": series.logline}
    if with_episodes:
        data["episodes"] = [{"id": p.id, "episode": p.episode, "topic": p.topic, "status": p.status,
                             "recap": p.recap} for p in episodes(db, series)]
    else:
        data["episode_count"] = db.execute(select(func.count()).select_from(YTProject)
                                           .where(YTProject.series_id == series.id)).scalar()
    return data
