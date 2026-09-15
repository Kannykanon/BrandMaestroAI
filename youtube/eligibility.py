"""Which marketing scripts YouTube Automation may use, and how each is labelled.

Read-only: this reads marketing's `generations` and `reviewer_learning` rows and
never writes to them.

    human_approved   generations.approved   result
    true             any                    human-approved
    false            any                    hidden
    NULL             true                   enforcer-approved, not human-reviewed
    NULL             false                  hidden
    NULL             NULL (legacy row)      hidden

A human verdict always wins. Rows saved before the enforcer's verdict was
stored (approved IS NULL) are eligible only through a human approval.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from youtube.models import APPROVAL_ENFORCER, APPROVAL_HUMAN

SCRIPT_CONTENT_TYPE = "script"
# Content types that can become a video: scripts, and ads (read as narration).
VIDEO_CONTENT_TYPES = (SCRIPT_CONTENT_TYPE, "ad")


def approval_label(human_approved: Optional[bool], enforcer_approved: Optional[bool]) -> Optional[str]:
    """The label for a script, or None if it is not eligible."""
    if human_approved is True:
        return APPROVAL_HUMAN
    if human_approved is False:
        return None
    if enforcer_approved is True:
        return APPROVAL_ENFORCER
    return None


def eligibility_condition(generation, review):
    """The same rule as approval_label(), as a SQL condition for the listing query."""
    return or_(
        review.human_approved.is_(True),
        and_(review.human_approved.is_(None), generation.approved.is_(True)),
    )


@dataclass
class EligibleScript:
    generation_id: str
    topic: str
    content: str
    label: str
    score: Optional[float]
    completed_at: Optional[datetime]
    content_type: str = SCRIPT_CONTENT_TYPE

    @property
    def word_count(self) -> int:
        return len(self.content.split())

    def to_summary(self, preview_chars: int = 240) -> dict:
        return {
            "generation_id": self.generation_id,
            "topic": self.topic,
            "approval": self.label,
            "content_type": self.content_type,
            "score": float(self.score) if self.score is not None else None,
            "word_count": self.word_count,
            "preview": self.content[:preview_chars],
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


def eligible_scripts_query(business_id: str):
    from database import Generation, ReviewerLearning

    return (
        select(Generation, ReviewerLearning.human_approved)
        .outerjoin(ReviewerLearning, ReviewerLearning.generation_id == Generation.generation_id)
        .where(
            Generation.business_id == business_id,
            Generation.content_type.in_(VIDEO_CONTENT_TYPES),
            Generation.status == "completed",
            Generation.content.is_not(None),
            eligibility_condition(Generation, ReviewerLearning),
        )
        .order_by(Generation.completed_at.desc().nulls_last())
    )


def _to_eligible(generation, human_approved) -> Optional[EligibleScript]:
    # The query already filters; the label is recomputed from the same rule so
    # a row can never be shown with a label the rule would not give it.
    label = approval_label(human_approved, generation.approved)
    if label is None or not (generation.content or "").strip():
        return None
    return EligibleScript(
        generation_id=generation.generation_id,
        topic=generation.topic,
        content=generation.content,
        label=label,
        score=generation.score,
        completed_at=generation.completed_at,
        content_type=getattr(generation, "content_type", SCRIPT_CONTENT_TYPE),
    )


def list_eligible_scripts(db: Session, business_id: str) -> list[EligibleScript]:
    rows = db.execute(eligible_scripts_query(business_id)).all()
    return [s for s in (_to_eligible(g, h) for g, h in rows) if s is not None]


def get_eligible_script(db: Session, business_id: str, generation_id: str) -> Optional[EligibleScript]:
    """One eligible script, or None if it does not exist, belongs elsewhere, or is not eligible."""
    from database import Generation

    row = db.execute(
        eligible_scripts_query(business_id).where(Generation.generation_id == generation_id)
    ).first()
    return _to_eligible(*row) if row else None
