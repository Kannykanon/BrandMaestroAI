"""Scene and character images, with the same plug-and-adapter design as model.py.

    ImagePort          the abstract adapter
    NanoBananaImage    Gemini 3.1 Flash Image ("Nano Banana 2") on Vertex AI
    SeedreamImage      ByteDance Seedream 4 through fal.ai
    ImageRegistry      one shared instance per provider

    YT_IMAGE_PROVIDER   nano_banana | seedream   (default nano_banana)
    YT_IMAGE_MODEL      Vertex model id (default gemini-3.1-flash-image)
    YT_IMAGE_LOCATION   Vertex location (default global)
    YT_IMAGE_SIZE       1K | 2K | 4K for Nano Banana (default 1K)
    YT_IMAGE_USD        override the per-image price used for cost records
    YT_IMAGE_MAX_RETRIES  retries after a rate limit (default 5)
    FAL_KEY             API key, for seedream

References are passed with a label ("Reference sheet for MAYA"), so a model
can tell which character each image shows.
"""
from __future__ import annotations

import base64
import io
import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

ASPECT_RATIOS = ("1:1", "16:9", "9:16")

UPLOAD_MIME_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MIN_UPLOAD_SIDE = 256
REFERENCE_MAX_SIDE = 1024


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


class ImageError(RuntimeError):
    """The provider did not return an image (blocked, refused, or failed)."""


class RateLimited(ImageError):
    """The provider refused the request because a quota or rate limit was hit."""


# Waits between attempts after a rate limit. Image models on a fresh Google
# Cloud project allow only a few requests a minute, so a storyboard of dozens of
# shots hits the limit; waiting and retrying finishes it instead of failing it.
RETRY_DELAYS_S = (15, 30, 60, 60, 60)


def with_rate_limit_retry(call, is_rate_limited, sleep=time.sleep):
    """Run call(), retrying after rate limits with increasing waits. Other errors are raised at once."""
    retries = int(_env("YT_IMAGE_MAX_RETRIES") or len(RETRY_DELAYS_S))
    for attempt in range(retries + 1):
        try:
            return call()
        except Exception as e:
            if not is_rate_limited(e):
                raise
            if attempt == retries:
                raise RateLimited(f"Rate limited after {retries} retries: {e}") from e
            delay = RETRY_DELAYS_S[min(attempt, len(RETRY_DELAYS_S) - 1)]
            logger.info("Image provider rate limited; retrying in %ss (%s/%s)", delay, attempt + 1, retries)
            sleep(delay)


@dataclass(frozen=True)
class ImageInput:
    data: bytes
    mime_type: str
    label: str = ""


@dataclass(frozen=True)
class GeneratedImage:
    data: bytes
    mime_type: str
    width: int
    height: int

    @property
    def extension(self) -> str:
        return {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}.get(self.mime_type, "png")


# ---------------------------------------------------------------------------
#  Uploads and references
# ---------------------------------------------------------------------------
def inspect_image(data: bytes) -> tuple[str, int, int]:
    """(mime type, width, height) of an uploaded image. Raises ValueError if it is not acceptable."""
    from PIL import Image, UnidentifiedImageError

    if not data:
        raise ValueError("The file is empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(f"Images can be at most {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            fmt, (width, height) = image.format, image.size
    except (UnidentifiedImageError, OSError) as e:
        raise ValueError("The file is not a readable image") from e
    if fmt not in UPLOAD_MIME_TYPES:
        raise ValueError("Upload a JPEG, PNG or WebP image")
    if min(width, height) < MIN_UPLOAD_SIDE:
        raise ValueError(f"Images must be at least {MIN_UPLOAD_SIDE}px on each side")
    return UPLOAD_MIME_TYPES[fmt], width, height


def prepare_reference(data: bytes, label: str = "") -> ImageInput:
    """Shrink an image to a sensible reference size and re-encode it as JPEG.

    Keeps requests small (inline limits are a few MB) and strips metadata such
    as camera location from uploaded photos before they leave the server.
    """
    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        image = image.convert("RGB")
        image.thumbnail((REFERENCE_MAX_SIDE, REFERENCE_MAX_SIDE))
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=90)
    return ImageInput(out.getvalue(), "image/jpeg", label)


def _dimensions(data: bytes) -> tuple[int, int]:
    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        return image.size


# ---------------------------------------------------------------------------
#  Port and adapters
# ---------------------------------------------------------------------------
class ImagePort(ABC):
    """Adapter between YouTube Automation and one image-generation provider."""

    #: Value of YT_IMAGE_PROVIDER that selects this adapter.
    name: str = ""
    #: Environment variables that must be set before this provider can be used.
    required_env: tuple[str, ...] = ()
    #: Most reference images one request accepts.
    max_references: int = 4
    #: Most characters the model keeps consistent in one image.
    max_characters: int = 4
    #: Default price of one generated image in USD, for cost records.
    usd_per_image: float = 0.0

    @classmethod
    def missing_env(cls) -> list[str]:
        return [var for var in cls.required_env if not _env(var)]

    @abstractmethod
    def generate(self, prompt: str, references: list[ImageInput], aspect_ratio: str) -> GeneratedImage: ...

    def cost_usd(self, images: int = 1) -> float:
        override = _env("YT_IMAGE_USD")
        return images * (float(override) if override else self.usd_per_image)

    def _check(self, prompt: str, references: list[ImageInput], aspect_ratio: str) -> None:
        if not prompt.strip():
            raise ValueError("An image prompt is required")
        if aspect_ratio not in ASPECT_RATIOS:
            raise ValueError(f"aspect_ratio must be one of {ASPECT_RATIOS}")
        if len(references) > self.max_references:
            raise ValueError(f"{self.name} accepts at most {self.max_references} reference images")


class NanoBananaImage(ImagePort):
    """Gemini 3.1 Flash Image on Vertex AI, using the project's Application Default Credentials.

    Keeps up to 4 characters consistent from references in one image.
    Price per 1K image from the Gemini API price list (2026-09-14); set
    YT_IMAGE_USD if Vertex bills your project differently.
    """

    name = "nano_banana"
    required_env = ("PROJECT_ID",)
    max_references = 14
    max_characters = 4
    usd_per_image = 0.067
    DEFAULT_MODEL = "gemini-3.1-flash-image"

    def __init__(self, client=None):
        self._client = client
        self._model = _env("YT_IMAGE_MODEL") or self.DEFAULT_MODEL
        self._size = _env("YT_IMAGE_SIZE") or "1K"

    def _get_client(self):
        if self._client is None:
            from google import genai
            self._client = genai.Client(vertexai=True, project=_env("PROJECT_ID"),
                                        location=_env("YT_IMAGE_LOCATION") or "global")
        return self._client

    def generate(self, prompt: str, references: list[ImageInput], aspect_ratio: str) -> GeneratedImage:
        from google.genai import types

        self._check(prompt, references, aspect_ratio)
        contents: list = []
        for ref in references:
            if ref.label:
                contents.append(ref.label)
            contents.append(types.Part.from_bytes(data=ref.data, mime_type=ref.mime_type))
        contents.append(prompt)

        config = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(aspect_ratio=aspect_ratio, image_size=self._size),
        )
        response = with_rate_limit_retry(
            lambda: self._get_client().models.generate_content(model=self._model, contents=contents, config=config),
            lambda e: getattr(e, "code", None) == 429 or "RESOURCE_EXHAUSTED" in str(e),
        )
        for candidate in response.candidates or []:
            for part in (candidate.content.parts if candidate.content else None) or []:
                inline = getattr(part, "inline_data", None)
                if inline and inline.data:
                    width, height = _dimensions(inline.data)
                    return GeneratedImage(inline.data, inline.mime_type or "image/png", width, height)
        reasons = [str(c.finish_reason) for c in response.candidates or []]
        feedback = getattr(response, "prompt_feedback", None)
        raise ImageError(f"No image returned (finish: {', '.join(reasons) or 'none'}; feedback: {feedback})")


class SeedreamImage(ImagePort):
    """ByteDance Seedream 4 through fal.ai (FAL_KEY). Accepts up to 10 reference images."""

    name = "seedream"
    required_env = ("FAL_KEY",)
    max_references = 10
    max_characters = 4
    usd_per_image = 0.03
    EDIT_URL = "https://fal.run/fal-ai/bytedance/seedream/v4/edit"
    TEXT_URL = "https://fal.run/fal-ai/bytedance/seedream/v4/text-to-image"
    SIZES = {"1:1": "square_hd", "16:9": "landscape_16_9", "9:16": "portrait_16_9"}

    def __init__(self, http=None):
        self._http = http

    def _get_http(self):
        if self._http is None:
            import httpx
            self._http = httpx.Client(timeout=180)
        return self._http

    def generate(self, prompt: str, references: list[ImageInput], aspect_ratio: str) -> GeneratedImage:
        self._check(prompt, references, aspect_ratio)
        key = _env("FAL_KEY")
        if not key:
            raise ImageError("FAL_KEY is not set")

        # Seedream takes images without captions, so the labels go into the prompt by position.
        labels = [f"Image {i + 1}: {ref.label}" for i, ref in enumerate(references) if ref.label]
        body = {
            "prompt": "\n".join(labels + [prompt]) if labels else prompt,
            "image_size": self.SIZES[aspect_ratio],
            "num_images": 1,
            "enable_safety_checker": True,
            "sync_mode": True,
        }
        if references:
            body["image_urls"] = [
                f"data:{ref.mime_type};base64,{base64.b64encode(ref.data).decode()}" for ref in references
            ]
        url = self.EDIT_URL if references else self.TEXT_URL

        http = self._get_http()

        def post():
            result = http.post(url, json=body, headers={"Authorization": f"Key {key}"})
            if result.status_code == 429:
                raise RateLimited("Seedream rate limit")
            return result

        response = with_rate_limit_retry(post, lambda e: isinstance(e, RateLimited))
        if response.status_code >= 400:
            raise ImageError(f"Seedream request failed ({response.status_code}): {response.text[:300]}")
        images = response.json().get("images") or []
        if not images:
            raise ImageError("Seedream returned no image")
        data, mime = self._fetch(http, images[0])
        width, height = _dimensions(data)
        return GeneratedImage(data, mime, width, height)

    @staticmethod
    def _fetch(http, image: dict) -> tuple[bytes, str]:
        url = image.get("url") or ""
        if url.startswith("data:"):
            header, encoded = url.split(",", 1)
            return base64.b64decode(encoded), header[5:].split(";")[0] or "image/png"
        downloaded = http.get(url)
        if downloaded.status_code >= 400:
            raise ImageError(f"Could not download the Seedream image ({downloaded.status_code})")
        return downloaded.content, image.get("content_type") or downloaded.headers.get("content-type", "image/png")


class ImageRegistry:
    """One shared instance per image provider; the default is YT_IMAGE_PROVIDER."""

    PROVIDERS: dict[str, type[ImagePort]] = {
        NanoBananaImage.name: NanoBananaImage,
        SeedreamImage.name: SeedreamImage,
    }
    DEFAULT_PROVIDER = NanoBananaImage.name

    _instances: dict[str, ImagePort] = {}
    _lock = threading.Lock()

    @classmethod
    def default_provider(cls) -> str:
        name = _env("YT_IMAGE_PROVIDER").lower() or cls.DEFAULT_PROVIDER
        if name not in cls.PROVIDERS:
            raise ValueError(f"YT_IMAGE_PROVIDER must be one of {sorted(cls.PROVIDERS)}, got {name!r}")
        return name

    @classmethod
    def get(cls, provider: Optional[str] = None) -> ImagePort:
        name = (provider or cls.default_provider()).lower()
        if name not in cls.PROVIDERS:
            raise ValueError(f"Unknown image provider {name!r}. Known: {sorted(cls.PROVIDERS)}")
        if name not in cls._instances:
            with cls._lock:
                if name not in cls._instances:
                    cls._instances[name] = cls.PROVIDERS[name]()
        return cls._instances[name]
