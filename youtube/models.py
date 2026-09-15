"""Tables for YouTube Automation. All prefixed yt_ and created by init_youtube_tables().

They live in the application's database but on their own declarative base and
metadata, so database.init_db() never creates or touches them, and a problem
here can never stop marketing from starting. No yt_ table has a foreign key to
a marketing table: a project keeps a snapshot of its script instead.
"""
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    DECIMAL,
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
    inspect,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

logger = logging.getLogger(__name__)

# Lifecycle of a video project, in order. `failed` can be reached from any step.
PROJECT_STATUSES = (
    "draft",
    "planned",
    "cast",
    "voiced",
    "drawing",
    "storyboard_ready",
    "storyboard_approved",
    "animated",
    "rendered",
    "uploaded_private",
    "published",
    "failed",
)

FORMATS = ("long_form", "short")

# Which kind of approval made a script eligible (see youtube/eligibility.py).
APPROVAL_HUMAN = "human"
APPROVAL_ENFORCER = "enforcer"

SHOT_TYPES = ("narration", "dialogue", "two_character", "cutaway")


class YTModel(DeclarativeBase):
    metadata = MetaData(naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    })


def _created_at():
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class YTCharacter(YTModel):
    __tablename__ = "yt_characters"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    voice_provider: Mapped[Optional[str]] = mapped_column(String(50))
    voice_id: Mapped[Optional[str]] = mapped_column(String(200))
    style_notes: Mapped[Optional[str]] = mapped_column(Text)
    # "I have the right to use this face", recorded when the user confirms it.
    rights_confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # none | generating | ready | approved | failed
    sheet_status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="none")
    # Why the last character sheet attempt failed, if it did.
    sheet_error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (UniqueConstraint("business_id", "name", name="uq_yt_characters_business_name"),)


class YTCharacterImage(YTModel):
    __tablename__ = "yt_character_images"

    id: Mapped[int] = mapped_column(primary_key=True)
    character_id: Mapped[int] = mapped_column(
        ForeignKey("yt_characters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # upload | sheet
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = _created_at()


class YTStyle(YTModel):
    __tablename__ = "yt_styles"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    reference_storage_key: Mapped[Optional[str]] = mapped_column(String(500))
    created_at: Mapped[datetime] = _created_at()


class YTProject(YTModel):
    __tablename__ = "yt_projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # The marketing generation this project was created from. Not a foreign
    # key: marketing may delete its row, and the project keeps its snapshot.
    source_generation_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    topic: Mapped[str] = mapped_column(String(500), nullable=False)
    # A copy of the approved script taken at creation. Never re-read from marketing.
    script_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    approval_label: Mapped[str] = mapped_column(String(20), nullable=False)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    format: Mapped[str] = mapped_column(String(20), nullable=False)
    style_id: Mapped[Optional[int]] = mapped_column(ForeignKey("yt_styles.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="draft")
    error: Mapped[Optional[str]] = mapped_column(Text)
    estimated_cost_usd: Mapped[Optional[float]] = mapped_column(DECIMAL(10, 4))
    actual_cost_usd: Mapped[Optional[float]] = mapped_column(DECIMAL(10, 4))
    # The joined voice track for the whole script.
    audio_key: Mapped[Optional[str]] = mapped_column(String(500))
    audio_duration_s: Mapped[Optional[float]] = mapped_column(Float)
    # When a person approved the storyboard. Cleared by any change to it.
    storyboard_approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class YTCast(YTModel):
    __tablename__ = "yt_cast"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("yt_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    speaker_label: Mapped[str] = mapped_column(String(100), nullable=False)
    character_id: Mapped[Optional[int]] = mapped_column(ForeignKey("yt_characters.id", ondelete="SET NULL"))
    # The planner's description of this speaker, a hint when casting.
    description: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (UniqueConstraint("project_id", "speaker_label", name="uq_yt_cast_project_speaker"),)


class YTShot(YTModel):
    __tablename__ = "yt_shots"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("yt_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    speaker_label: Mapped[str] = mapped_column(String(100), nullable=False)
    shot_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # Delivery notes from the script, e.g. "whispering". Never spoken.
    delivery: Mapped[Optional[str]] = mapped_column(Text)
    # Speaker labels of the characters visible in the shot.
    characters: Mapped[Optional[list]] = mapped_column(JSON)
    visual_prompt: Mapped[Optional[str]] = mapped_column(Text)
    audio_key: Mapped[Optional[str]] = mapped_column(String(500))
    image_key: Mapped[Optional[str]] = mapped_column(String(500))
    clip_key: Mapped[Optional[str]] = mapped_column(String(500))
    # Why the last image attempt for this shot failed, if it did.
    image_error: Mapped[Optional[str]] = mapped_column(Text)
    duration_s: Mapped[Optional[float]] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")

    __table_args__ = (UniqueConstraint("project_id", "position", name="uq_yt_shots_project_position"),)


class YTRender(YTModel):
    __tablename__ = "yt_renders"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("yt_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    format: Mapped[str] = mapped_column(String(20), nullable=False)
    video_key: Mapped[Optional[str]] = mapped_column(String(500))
    thumbnail_key: Mapped[Optional[str]] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class YTUpload(YTModel):
    __tablename__ = "yt_uploads"

    id: Mapped[int] = mapped_column(primary_key=True)
    render_id: Mapped[int] = mapped_column(
        ForeignKey("yt_renders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    youtube_video_id: Mapped[Optional[str]] = mapped_column(String(50), index=True)
    # private | unlisted | public
    privacy: Mapped[str] = mapped_column(String(20), nullable=False, server_default="private")
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class YTChannel(YTModel):
    __tablename__ = "yt_channel"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    channel_id: Mapped[Optional[str]] = mapped_column(String(100))
    refresh_token_encrypted: Mapped[Optional[str]] = mapped_column(Text)
    connected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class YTCost(YTModel):
    __tablename__ = "yt_costs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("yt_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # voice | image | avatar | render | publish | llm
    stage: Mapped[str] = mapped_column(String(20), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    units: Mapped[float] = mapped_column(Float, nullable=False)
    # second | image | character | megapixel | call
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    cost_usd: Mapped[float] = mapped_column(DECIMAL(10, 4), nullable=False)
    created_at: Mapped[datetime] = _created_at()


# Columns added after a table first shipped. create_all() creates missing
# tables but never alters existing ones, so each is added if absent. Only yt_
# tables appear here.
COLUMN_MIGRATIONS = (
    ("yt_projects", "audio_key", "VARCHAR(500)"),
    ("yt_projects", "audio_duration_s", "FLOAT"),
    ("yt_cast", "description", "TEXT"),
    ("yt_shots", "delivery", "TEXT"),
    ("yt_shots", "characters", "JSON"),
    ("yt_projects", "storyboard_approved_at", "TIMESTAMP WITH TIME ZONE"),
    ("yt_shots", "image_error", "TEXT"),
    ("yt_characters", "sheet_error", "TEXT"),
)


def add_column_ddl(dialect: str, table: str, column: str, ddl_type: str) -> str:
    """The ALTER for one column. On Postgres it cannot fail because the column already exists."""
    if not table.startswith("yt_"):
        raise ValueError(f"YouTube migrations may only touch yt_ tables, not {table}")
    if_not_exists = " IF NOT EXISTS" if dialect == "postgresql" else ""
    return f"ALTER TABLE {table} ADD COLUMN{if_not_exists} {column} {ddl_type}"


def _column_names(engine, table: str) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(table)}


def _add_missing_columns(engine) -> None:
    """Add each missing column in its own transaction.

    The API runs several worker processes that all start at once, so two of
    them can find the same column missing and both try to add it. On Postgres
    IF NOT EXISTS makes that harmless; elsewhere a failed ALTER is accepted if
    the column turns out to exist after all.
    """
    for table, column, ddl_type in COLUMN_MIGRATIONS:
        ddl = add_column_ddl(engine.dialect.name, table, column, ddl_type)
        if column in _column_names(engine, table):
            continue
        logger.info("Adding missing '%s' column to '%s'", column, table)
        try:
            with engine.begin() as conn:
                conn.execute(text(ddl))
        except Exception:
            if column not in _column_names(engine, table):
                raise
            logger.info("'%s.%s' was added by another process", table, column)


def _create_tables(engine) -> None:
    """Create missing yt_ tables. A concurrent start may create one first; that is not a failure."""
    try:
        YTModel.metadata.create_all(engine)
    except Exception:
        # create_all checks before creating, so a second pass succeeds if the
        # only problem was another process creating the same table meanwhile.
        YTModel.metadata.create_all(engine)


def init_youtube_tables(engine) -> bool:
    """Create the yt_ tables and add any missing columns. Never raises.

    Uses YTModel's own metadata, so this cannot create or alter anything
    marketing owns.
    """
    try:
        _create_tables(engine)
        _add_missing_columns(engine)
        logger.info("YouTube Automation tables ready")
        return True
    except Exception as e:
        logger.error("YouTube Automation tables could not be created: %s", e)
        return False
