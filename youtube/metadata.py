"""YouTube title, description and tags: drafted by the model from the script and Brand Brain, edited by a person.

This is a small single call, not the marketing enforcer loop. The draft is
only a starting point; nothing is uploaded until a person has saved a title.

    YT_DEFAULT_CATEGORY  YouTube category id for new videos (default 24, Entertainment)
    YT_VIDEO_LANGUAGE    language of titles, descriptions and speech (default en)
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

from sqlalchemy.orm import Session

from youtube.models import YTProject
from youtube.projects import ProjectError
from youtube.publisher import VideoMetadata

logger = logging.getLogger(__name__)

TITLE_MAX = 100
DESCRIPTION_MAX_BYTES = 5000
TAGS_MAX_CHARS = 500
TAG_MAX = 30  # YouTube's own per-tag limit, applied before the total
SCRIPT_CHARS = 8000
BRAIN_CHARS = 4000

# The categories that fit brand storytelling; any id YouTube accepts can be saved.
CATEGORIES = {
    "1": "Film & Animation",
    "22": "People & Blogs",
    "23": "Comedy",
    "24": "Entertainment",
    "26": "Howto & Style",
    "27": "Education",
    "28": "Science & Technology",
    "29": "Nonprofits & Activism",
}

METADATA_PROMPT = """You are writing the YouTube title, description and tags for a video made from an approved script.

Brand: {brand}
Format: {format}
{brain}
SCRIPT:
{script}

Write:
- "title": at most 90 characters. Specific and intriguing, true to the story. No clickbait promises the video does not keep, no ALL CAPS, no emoji.
- "description": 2 short paragraphs (under 900 characters) that set up the story without spoiling the ending, in the brand's voice, then one line inviting viewers to subscribe.{shorts}
- "tags": 8 to 15 short search phrases, most specific first.

Only state facts that are in the script. Do not use the characters < or >.
Write plainly: no hype ("witness", "epic showdown", "don't miss", "mind-blowing"), no emoji, no promises about upload schedules.{fixes}
Return JSON only: {{"title": "...", "description": "...", "tags": ["...", "..."]}}"""


def default_category() -> str:
    value = os.getenv("YT_DEFAULT_CATEGORY", "").strip()
    return value if value.isdigit() else "24"


def language() -> str:
    return os.getenv("YT_VIDEO_LANGUAGE", "").strip() or "en"


def _clean_text(value: str) -> str:
    # YouTube rejects < and > in titles and descriptions.
    return (value or "").replace("<", "‹").replace(">", "›")


def clean_title(title: Optional[str]) -> str:
    title = re.sub(r"\s+", " ", _clean_text(title or "")).strip()
    return title[:TITLE_MAX].rstrip()


def clean_description(description: Optional[str]) -> str:
    text = _clean_text(description or "").replace("\r\n", "\n").strip()
    data = text.encode("utf-8")
    if len(data) <= DESCRIPTION_MAX_BYTES:
        return text
    return data[:DESCRIPTION_MAX_BYTES].decode("utf-8", errors="ignore").rstrip()


def tags_length(tags: list[str]) -> int:
    """Characters YouTube counts: commas between tags, and quotes around tags containing spaces."""
    return sum(len(t) + (2 if " " in t else 0) for t in tags) + max(len(tags) - 1, 0)


def clean_tags(tags) -> list[str]:
    if isinstance(tags, str):
        tags = tags.split(",")
    cleaned, seen = [], set()
    for tag in tags or []:
        if not isinstance(tag, str):
            continue
        tag = re.sub(r"\s+", " ", _clean_text(tag).replace(",", " ").replace('"', "")).strip().lstrip("#")
        if not tag or tag.lower() in seen or len(tag) > TAG_MAX:
            continue
        if tags_length(cleaned + [tag]) > TAGS_MAX_CHARS:
            break
        cleaned.append(tag)
        seen.add(tag.lower())
    return cleaned


def _brand_context(business_id: str) -> tuple[str, str]:
    """The brand's name and the start of its script Brand Brain, or blanks if there is none."""
    try:
        from brand_metrics import BrandMetricsSQL
        from utils.brand_profile import extract_brand_name

        brain = (BrandMetricsSQL(business_id=business_id, content_type="script").get_context() or "").strip()
        return extract_brand_name(brain) if brain else "", brain[:BRAIN_CHARS]
    except Exception as e:
        logger.warning("Brand Brain unavailable for YouTube metadata (business %s): %s", business_id, e)
        return "", ""


def draft_metadata(project: YTProject, llm=None, brand_context=None) -> dict:
    """Ask the model for a title, description and tags. Raises ProjectError if it gives nothing usable."""
    from utils.llm_output import message_text, parse_llm_json

    if llm is None:
        from model import LLMSingleton
        llm = LLMSingleton.get("extraction")
    from youtube.quality import metadata_issues

    brand, brain = (brand_context or _brand_context)(project.business_id)

    def attempt(fixes: str) -> dict:
        prompt = METADATA_PROMPT.format(
            brand=brand or "(not given)",
            format="YouTube Short (vertical)" if project.format == "short" else "Long-form YouTube video",
            brain=f"\nBRAND BRAIN (voice and facts, for tone):\n{brain}\n" if brain else "",
            script=project.script_snapshot[:SCRIPT_CHARS],
            shorts=" End with the hashtag #Shorts." if project.format == "short" else "",
            fixes=fixes,
        )
        try:
            raw = parse_llm_json(message_text(llm.invoke(prompt).content))
        except Exception as e:
            raise ProjectError(f"Writing the YouTube details failed: {e}") from e
        title = clean_title(raw.get("title") if isinstance(raw, dict) else None)
        if not title:
            raise ProjectError("The model returned no usable title; write one by hand")
        description = clean_description(raw.get("description"))
        if project.format == "short" and "#shorts" not in description.lower():
            description = clean_description(f"{description}\n\n#Shorts")
        draft = {"title": title, "description": description, "tags": clean_tags(raw.get("tags"))}
        draft["issues"] = metadata_issues(title, description, draft["tags"], project.script_snapshot, brain)
        return draft

    draft = attempt("")
    if draft["issues"]:
        # One rewrite with the problems named; keep whichever draft has fewer.
        try:
            second = attempt("\nYour previous draft had these problems; fix them: " + "; ".join(draft["issues"]) + ".")
            if len(second["issues"]) < len(draft["issues"]):
                draft = second
        except ProjectError as e:
            logger.info("Metadata rewrite failed; keeping the first draft: %s", e)
    return draft


def generate_metadata(db: Session, project: YTProject, llm=None, brand_context=None) -> YTProject:
    draft = draft_metadata(project, llm=llm, brand_context=brand_context)
    project.video_title, project.video_description, project.video_tags = draft["title"], draft["description"], draft["tags"]
    if project.video_category_id is None:
        project.video_category_id = default_category()
    db.commit()
    db.refresh(project)
    return project


_UNSET = object()


def update_metadata(db: Session, project: YTProject, title=_UNSET, description=_UNSET, tags=_UNSET,
                    category_id=_UNSET, made_for_kids=_UNSET) -> YTProject:
    if title is not _UNSET:
        project.video_title = clean_title(title) or None
    if description is not _UNSET:
        project.video_description = clean_description(description)
    if tags is not _UNSET:
        project.video_tags = clean_tags(tags)
    if category_id is not _UNSET:
        if category_id is not None and not str(category_id).isdigit():
            raise ProjectError("The category must be a YouTube category id")
        project.video_category_id = str(category_id) if category_id is not None else None
    if made_for_kids is not _UNSET:
        project.made_for_kids = made_for_kids
    db.commit()
    db.refresh(project)
    return project


def video_metadata(project: YTProject) -> VideoMetadata:
    return VideoMetadata(
        title=project.video_title or "",
        description=project.video_description or "",
        tags=list(project.video_tags or []),
        category_id=project.video_category_id or default_category(),
        made_for_kids=bool(project.made_for_kids),
        synthetic_media=True,
        language=language(),
    )


def metadata_problems(project: YTProject) -> list[str]:
    problems = []
    if not project.video_title:
        problems.append("Give the video a title")
    if project.made_for_kids is None:
        problems.append("Say whether the video is made for kids")
    return problems


def serialize_metadata(project: YTProject) -> dict:
    from youtube.quality import metadata_issues

    return {
        # Checked against the script on every read, so edits are checked too.
        "issues": metadata_issues(project.video_title, project.video_description, project.video_tags or [],
                                  project.script_snapshot or "") if project.video_title else [],
        "title": project.video_title,
        "description": project.video_description,
        "tags": project.video_tags or [],
        "category_id": project.video_category_id or default_category(),
        "made_for_kids": project.made_for_kids,
        "synthetic_media": True,
        "categories": CATEGORIES,
        "limits": {"title": TITLE_MAX, "description_bytes": DESCRIPTION_MAX_BYTES, "tags_chars": TAGS_MAX_CHARS},
    }
