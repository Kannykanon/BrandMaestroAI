"""Anti-slop checks for what YouTube Automation produces outside the marketing enforcer.

    metadata_issues   hype, promises, facts and contact details that are not in the
                      script, and the brand's banned punctuation in titles and descriptions
    SceneChecker      problems in a storyboard image: borders and bars (in code), and
                      text, panels, blurred bands or repeated people (a vision model)

    YT_IMAGE_CHECK          auto | gemini | basic | off   (auto: gemini with Nano Banana, else basic)
    YT_IMAGE_CHECK_MODEL    default gemini-2.5-flash
    YT_IMAGE_CHECK_RETRIES  automatic redraws when an image is flagged (default 1)

Checks only report; they never block a person. A flagged image is redrawn
automatically at most YT_IMAGE_CHECK_RETRIES times, then shown with its issues.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Marketing filler that makes a listing read as generated. Matched as whole phrases.
HYPE_PHRASES = (
    "witness", "you won't believe", "must-watch", "must watch", "don't miss", "do not miss",
    "mind-blowing", "jaw-dropping", "game-changer", "game changer", "unleash", "delve",
    "buckle up", "smash that", "like and subscribe", "in a world where", "like never before",
    "shake the very foundations", "take your", "next level", "epic showdown", "epic confrontation",
    "the ultimate", "you need to see", "stay tuned",
)
# Promises the channel may not keep.
PROMISE_PATTERNS = (
    r"\bevery (?:day|week|month|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    r"\b(?:daily|weekly) (?:videos|shorts|uploads|episodes)\b",
    r"\bnew (?:videos|shorts|episodes) (?:every|each)\b",
)
_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:[.,]\d+)?", text or ""))


def metadata_issues(title: str, description: str, tags: list[str], script: str, brain: str = "") -> list[str]:
    """What would make this listing read as AI slop or claim something the script does not."""
    title, description = title or "", description or ""
    text = f"{title}\n{description}"
    lower = text.lower()
    issues = []

    hype = [p for p in HYPE_PHRASES if re.search(r"(?<![\w-])" + re.escape(p) + r"(?![\w-])", lower)]
    if hype:
        issues.append("Hype phrases: " + ", ".join(f'"{p}"' for p in hype))
    promises = [m.group(0) for pattern in PROMISE_PATTERNS for m in re.finditer(pattern, lower)]
    if promises:
        issues.append("Promises the channel may not keep: " + ", ".join(f'"{p}"' for p in promises))

    grounding = f"{script}\n{brain}"
    invented = sorted(_numbers(text) - _numbers(grounding))
    if invented:
        issues.append(f"Numbers not in the script: {', '.join(invented)}")

    shouting = [w for w in re.findall(r"\b[A-Z]{4,}\b", title) if w not in grounding]
    if "!" in title or shouting:
        issues.append("Title shouts (exclamation marks or words in capitals)")
    if _EMOJI.search(text):
        issues.append("Emoji in the title or description")

    try:
        from utils.enforcement.provenance import find_ungrounded_contact_details
        for finding in find_ungrounded_contact_details(text, grounding):
            issues.append(f"Contact detail not in the script or Brand Brain: {finding.get('excerpt') or finding.get('message')}")
    except Exception as e:  # the checks are advisory; never fail the listing over them
        logger.debug("Contact-detail check unavailable: %s", e)
    if brain:
        try:
            from utils.enforcement.punctuation import sanitize_banned_punctuation
            _, notes = sanitize_banned_punctuation(description, brain)
            if notes:
                issues.append(f"Punctuation the brand avoids: {'; '.join(notes)}")
        except Exception as e:
            logger.debug("Punctuation check unavailable: %s", e)
    return issues


# ---------------------------------------------------------------------------
#  Storyboard images
# ---------------------------------------------------------------------------
def border_issues(image_bytes: bytes) -> list[str]:
    """Flat bars along an edge that differ from the picture inside: letterboxing or a frame."""
    from PIL import Image, ImageStat

    with Image.open(io.BytesIO(image_bytes)) as image:
        grey = image.convert("L")
        width, height = grey.size
        band_h, band_w = max(int(height * 0.04), 2), max(int(width * 0.04), 2)
        inner = ImageStat.Stat(grey.crop((band_w, band_h, width - band_w, height - band_h)))
        if inner.stddev[0] < 8:
            return []  # a flat picture overall; nothing to compare the edges with
        edges = {
            "top": (0, 0, width, band_h), "bottom": (0, height - band_h, width, height),
            "left": (0, 0, band_w, height), "right": (width - band_w, 0, width, height),
        }
        flat = [name for name, box in edges.items() if ImageStat.Stat(grey.crop(box)).stddev[0] < 3]
    return [f"Flat bar or border along the {', '.join(flat)} edge"] if flat else []


VISION_PROMPT = """You are checking one storyboard frame for a video. It should be a single continuous scene.
Characters meant to be in it: {characters}.
Products meant to be in it: {products}. Text printed on those products (their labels, packaging or logos) does not count as text.
Answer in JSON only, with true or false for each:
{{"text": <any other words, letters, captions, signs or watermarks are visible>,
 "panels": <the frame is split into panels, has an inset, or looks like a collage>,
 "blurred_band": <a strip along an edge is blurred, stretched or filled rather than part of the scene>,
 "repeated_person": <the same person appears more than once>,
 "malformed": <clearly malformed hands, faces or bodies>}}"""

VISION_LABELS = {
    "text": "Visible text in the picture",
    "panels": "Split into panels or a collage",
    "blurred_band": "Blurred or filled strip along an edge",
    "repeated_person": "The same person appears more than once",
    "malformed": "Malformed hands, faces or bodies",
}


class SceneChecker:
    """Deterministic checks only."""

    name = "basic"

    def check(self, image_bytes: bytes, mime_type: str, characters: list[str], products: tuple = ()) -> list[str]:
        try:
            return border_issues(image_bytes)
        except Exception as e:
            logger.warning("Image border check failed: %s", e)
            return []


class GeminiSceneChecker(SceneChecker):
    """Border checks plus a Gemini look at the frame (a fraction of a cent per image)."""

    name = "gemini"

    def __init__(self, client=None):
        self._client = client

    def _get_client(self):
        if self._client is None:
            from google import genai
            self._client = genai.Client(vertexai=True, project=_env("PROJECT_ID"),
                                        location=_env("YT_IMAGE_CHECK_LOCATION") or "global")
        return self._client

    def check(self, image_bytes, mime_type, characters, products=()):
        issues = super().check(image_bytes, mime_type, characters)
        try:
            from google.genai import types

            response = self._get_client().models.generate_content(
                model=_env("YT_IMAGE_CHECK_MODEL") or "gemini-2.5-flash",
                contents=[types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                          VISION_PROMPT.format(characters=", ".join(characters) or "none named",
                                               products=", ".join(products) or "none")],
                config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0),
            )
            verdict = json.loads(response.text)
        except Exception as e:  # a failed check must never cost the user their image
            logger.warning("Vision check of a storyboard image failed: %s", e)
            return issues
        return issues + [label for key, label in VISION_LABELS.items() if verdict.get(key) is True]


def scene_checker(image_provider: str) -> Optional[SceneChecker]:
    mode = (_env("YT_IMAGE_CHECK") or "auto").lower()
    if mode == "off":
        return None
    if mode == "gemini" or (mode == "auto" and image_provider == "nano_banana" and _env("PROJECT_ID")):
        return GeminiSceneChecker()
    return SceneChecker()


def check_retries() -> int:
    value = _env("YT_IMAGE_CHECK_RETRIES")
    return max(int(value), 0) if value.isdigit() else 1
