from database import get_db_session, GenerationFeedback, Generation, ReviewerLearning
from abc import ABC, abstractmethod
from typing import Optional
import logging
from datetime import datetime

from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)


class FeedbackPort(ABC):
    @property
    @abstractmethod
    def source(self) -> str: ...

    @abstractmethod
    def save(self): ...

    @abstractmethod
    def save_feedback(self): ...

    @abstractmethod
    def get_patterns(self): ...


class FeedbackPortSQL(FeedbackPort):
    def __init__(self, business_id: str):
        self.business_id = business_id

    @property
    def source(self) -> str:
        return "postgres"

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def save(self,
             generation_id: str,
             content_type: str,
             creative_angle: str,
             generated_content: str,
             auto_score: float,
             topic: str = "",
             user_id: int = None,
             format_type: str = "",
             style_match: float = 0.0,
             tone_match: float = 0.0,
             structure_match: float = 0.0,
             signature_match: float = 0.0,
             use_search: bool = False,
             regeneration_depth: int = 0):

        agent_auto_approved = auto_score >= 8.0

        with get_db_session() as session:
            # Check if already exists before inserting
            existing = session.query(ReviewerLearning).filter_by(
                generation_id=generation_id
            ).first()

            if existing:
                logger.info("ReviewerLearning record already exists for %s, skipping", generation_id)
                return
            record = ReviewerLearning(
                generation_id=generation_id,
                business_id=self.business_id,
                content_type=content_type,
                creative_angle=creative_angle,
                generated_content=generated_content,
                agent_auto_score=auto_score,
                agent_auto_approved=agent_auto_approved,
                topic=topic,
                user_id=user_id,
                format_type=format_type,
                has_human_feedback=False,
                use_search=use_search,
                regeneration_depth=regeneration_depth,
            )
            session.add(record)
            session.commit()

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def save_feedback(self,
                      generation_id: str,
                      human_approved: bool,
                      human_score: float,
                      human_feedback: str):

        with get_db_session() as session:
            record = session.query(ReviewerLearning).filter_by(
                generation_id=generation_id
            ).first()
            if not record:
                return
            record.human_approved = human_approved
            record.human_score = human_score
            record.human_feedback = human_feedback
            record.has_human_feedback = True
            record.agent_correct = (human_approved == record.agent_auto_approved)
            session.commit()

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def get_patterns(self, content_type: str, limit: int = 10) -> dict:
        """Most recent reviewed generations for this content type.

        Ordered newest-first and capped in SQL. Previously this loaded every
        reviewed row for the business into memory and the caller sliced the
        first few off an unordered result, which meant the cost grew without
        bound as reviews accumulated *and* the newest feedback — the piece the
        writer most needs — could fall outside the slice entirely.
        """
        with get_db_session() as session:
            approved = session.query(ReviewerLearning).filter_by(
                business_id=self.business_id,
                content_type=content_type,
                human_approved=True
            ).order_by(ReviewerLearning.created_at.desc()).limit(limit).all()

            rejected = session.query(ReviewerLearning).filter(
                ReviewerLearning.business_id == self.business_id,
                ReviewerLearning.content_type == content_type,
                ReviewerLearning.human_approved == False
            ).order_by(ReviewerLearning.created_at.desc()).limit(limit).all()

        return {
            "approved": [
                {"angle": r.creative_angle, "feedback": r.human_feedback}
                for r in approved
            ],
            "rejected": [
                {"angle": r.creative_angle, "feedback": r.human_feedback}
                for r in rejected
            ]
        }