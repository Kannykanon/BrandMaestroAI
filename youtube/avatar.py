"""Talking characters for dialogue shots, with the same plug-and-adapter design as model.py.

    AvatarPort          the abstract adapter: a still image and an audio clip in, an MP4 out
    StillAvatar         no animation: the scene image held for the clip. Free; for drafts
    KlingAvatar         Kling AI Avatar v2 Standard through fal.ai (FAL_KEY)
    InfiniteTalkAvatar  InfiniteTalk through WaveSpeed (WAVESPEED_API_KEY)
    AvatarRegistry      one shared instance per provider

    YT_AVATAR_PROVIDER        still | kling_standard | infinitetalk   (default still)
    YT_AVATAR_TIMEOUT_S       how long to wait for one clip (default 900)
    YT_INFINITETALK_RESOLUTION  480p | 720p (default 480p)

Only the talking part of a dialogue shot is animated (see youtube/render.py),
so these are called for a few seconds of audio at a time.
"""
from __future__ import annotations

import base64
import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from youtube.images import RateLimited, with_rate_limit_retry

logger = logging.getLogger(__name__)


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


class AvatarError(RuntimeError):
    """The provider did not return a clip."""


@dataclass(frozen=True)
class AvatarClip:
    video: Optional[bytes]  # MP4 bytes, or None when the composer should hold the still image
    duration_s: float


class AvatarPort(ABC):
    """Adapter between YouTube Automation and one talking-head provider."""

    #: Value of YT_AVATAR_PROVIDER that selects this adapter.
    name: str = ""
    #: Environment variables that must be set before this provider can be used.
    required_env: tuple[str, ...] = ()
    #: Price per second of generated video, for estimates and cost records.
    usd_per_second: float = 0.0
    #: Shortest clip the provider accepts; shorter audio is padded with silence.
    min_seconds: float = 0.0
    #: Longest clip one request may produce.
    max_seconds: float = 60.0
    #: Whether the clip actually moves the character's lips.
    lip_sync: bool = True

    @classmethod
    def missing_env(cls) -> list[str]:
        return [var for var in cls.required_env if not _env(var)]

    def cost_usd(self, seconds: float) -> float:
        return round(max(seconds, self.min_seconds) * self.usd_per_second, 4) if seconds > 0 else 0.0

    @abstractmethod
    def animate(self, image: bytes, image_mime: str, audio_wav: bytes, duration_s: float,
                prompt: str = "") -> AvatarClip:
        """Animate the character in image speaking audio_wav (duration_s long)."""


class StillAvatar(AvatarPort):
    """Holds the scene image for the clip. No lip movement, no cost, no API key."""

    name = "still"
    lip_sync = False
    max_seconds = 3600.0

    def animate(self, image, image_mime, audio_wav, duration_s, prompt=""):
        return AvatarClip(video=None, duration_s=duration_s)


def _poll(fetch_status, is_done, timeout_s: float, interval_s: float = 5.0, sleep=time.sleep):
    deadline = time.monotonic() + timeout_s
    while True:
        status = fetch_status()
        if is_done(status):
            return status
        if time.monotonic() > deadline:
            raise AvatarError(f"The avatar provider did not finish within {int(timeout_s)}s")
        sleep(interval_s)


class KlingAvatar(AvatarPort):
    """Kling AI Avatar v2 Standard on fal.ai's queue API. Handles realistic and stylized characters."""

    name = "kling_standard"
    required_env = ("FAL_KEY",)
    usd_per_second = 0.0562  # fal.ai price, checked 2026-09-14
    min_seconds = 2.0
    max_seconds = 60.0
    MODEL = "fal-ai/kling-video/ai-avatar/v2/standard"

    def __init__(self, http=None, sleep=time.sleep):
        self._http = http
        self._sleep = sleep

    def _get_http(self):
        if self._http is None:
            import httpx
            self._http = httpx.Client(timeout=120)
        return self._http

    def _headers(self):
        key = _env("FAL_KEY")
        if not key:
            raise AvatarError("FAL_KEY is not set")
        return {"Authorization": f"Key {key}"}

    def animate(self, image, image_mime, audio_wav, duration_s, prompt=""):
        http, headers = self._get_http(), self._headers()
        body = {
            "image_url": f"data:{image_mime};base64,{base64.b64encode(image).decode()}",
            "audio_url": f"data:audio/wav;base64,{base64.b64encode(audio_wav).decode()}",
            "prompt": prompt or ".",
        }

        def submit():
            response = http.post(f"https://queue.fal.run/{self.MODEL}", json=body, headers=headers)
            if response.status_code == 429:
                raise RateLimited("fal.ai rate limit")
            if response.status_code >= 400:
                raise AvatarError(f"Kling request failed ({response.status_code}): {response.text[:300]}")
            return response.json()

        submitted = with_rate_limit_retry(submit, lambda e: isinstance(e, RateLimited), sleep=self._sleep)
        status_url, response_url = submitted["status_url"], submitted["response_url"]

        def fetch_status():
            response = http.get(status_url, headers=headers)
            if response.status_code >= 400:
                raise AvatarError(f"Kling status check failed ({response.status_code})")
            return response.json()

        final = _poll(fetch_status, lambda s: s.get("status") == "COMPLETED",
                      float(_env("YT_AVATAR_TIMEOUT_S") or 900), sleep=self._sleep)
        if final.get("error"):
            raise AvatarError(f"Kling failed: {final['error']}")
        result = http.get(response_url, headers=headers)
        if result.status_code >= 400:
            raise AvatarError(f"Kling result unavailable ({result.status_code}): {result.text[:300]}")
        payload = result.json()
        video_url = (payload.get("video") or {}).get("url")
        if not video_url:
            raise AvatarError("Kling returned no video")
        video = http.get(video_url)
        if video.status_code >= 400:
            raise AvatarError(f"Could not download the Kling video ({video.status_code})")
        return AvatarClip(video=video.content, duration_s=float(payload.get("duration") or duration_s))


class InfiniteTalkAvatar(AvatarPort):
    """InfiniteTalk on WaveSpeed. Inputs are uploaded to WaveSpeed's media store first (kept 7 days)."""

    name = "infinitetalk"
    required_env = ("WAVESPEED_API_KEY",)
    min_seconds = 3.0  # WaveSpeed bills at least 3 seconds
    max_seconds = 600.0
    API = "https://api.wavespeed.ai/api/v3"
    PRICES = {"480p": 0.03, "720p": 0.06}  # WaveSpeed price per second, checked 2026-09-14

    def __init__(self, http=None, sleep=time.sleep):
        self._http = http
        self._sleep = sleep

    @property
    def resolution(self) -> str:
        value = _env("YT_INFINITETALK_RESOLUTION") or "480p"
        return value if value in self.PRICES else "480p"

    @property
    def usd_per_second(self) -> float:  # type: ignore[override]
        return self.PRICES[self.resolution]

    def _get_http(self):
        if self._http is None:
            import httpx
            self._http = httpx.Client(timeout=120)
        return self._http

    def _headers(self):
        key = _env("WAVESPEED_API_KEY")
        if not key:
            raise AvatarError("WAVESPEED_API_KEY is not set")
        return {"Authorization": f"Bearer {key}"}

    def _upload(self, http, headers, data: bytes, filename: str, mime: str) -> str:
        response = http.post(f"{self.API}/media/upload/binary", headers=headers,
                             files={"file": (filename, data, mime)})
        if response.status_code >= 400:
            raise AvatarError(f"WaveSpeed upload failed ({response.status_code}): {response.text[:300]}")
        url = (response.json().get("data") or {}).get("download_url")
        if not url:
            raise AvatarError("WaveSpeed upload returned no URL")
        return url

    def animate(self, image, image_mime, audio_wav, duration_s, prompt=""):
        http, headers = self._get_http(), self._headers()
        ext = {"image/png": "png", "image/webp": "webp"}.get(image_mime, "jpg")
        image_url = self._upload(http, headers, image, f"frame.{ext}", image_mime)
        audio_url = self._upload(http, headers, audio_wav, "line.wav", "audio/wav")
        body = {"image": image_url, "audio": audio_url, "resolution": self.resolution, "seed": -1}
        if prompt:
            body["prompt"] = prompt

        def submit():
            response = http.post(f"{self.API}/wavespeed-ai/infinitetalk", json=body, headers=headers)
            if response.status_code == 429:
                raise RateLimited("WaveSpeed rate limit")
            if response.status_code >= 400:
                raise AvatarError(f"InfiniteTalk request failed ({response.status_code}): {response.text[:300]}")
            return response.json()

        submitted = with_rate_limit_retry(submit, lambda e: isinstance(e, RateLimited), sleep=self._sleep)
        prediction_id = (submitted.get("data") or {}).get("id")
        if not prediction_id:
            raise AvatarError("InfiniteTalk returned no prediction id")

        def fetch_status():
            response = http.get(f"{self.API}/predictions/{prediction_id}/result", headers=headers)
            if response.status_code >= 400:
                raise AvatarError(f"InfiniteTalk status check failed ({response.status_code})")
            return response.json().get("data") or {}

        terminal = {"completed", "failed", "cancelled", "timeout", "deleted"}
        final = _poll(fetch_status, lambda s: s.get("status") in terminal,
                      float(_env("YT_AVATAR_TIMEOUT_S") or 900), sleep=self._sleep)
        if final.get("status") != "completed":
            raise AvatarError(f"InfiniteTalk {final.get('status')}: {final.get('error') or 'no detail'}")
        outputs = final.get("outputs") or []
        if not outputs:
            raise AvatarError("InfiniteTalk returned no video")
        video = http.get(outputs[0])
        if video.status_code >= 400:
            raise AvatarError(f"Could not download the InfiniteTalk video ({video.status_code})")
        return AvatarClip(video=video.content, duration_s=duration_s)


class AvatarRegistry:
    """One shared instance per avatar provider; the default is YT_AVATAR_PROVIDER."""

    PROVIDERS: dict[str, type[AvatarPort]] = {
        StillAvatar.name: StillAvatar,
        KlingAvatar.name: KlingAvatar,
        InfiniteTalkAvatar.name: InfiniteTalkAvatar,
    }
    DEFAULT_PROVIDER = StillAvatar.name

    _instances: dict[str, AvatarPort] = {}
    _lock = threading.Lock()

    @classmethod
    def default_provider(cls) -> str:
        name = _env("YT_AVATAR_PROVIDER").lower() or cls.DEFAULT_PROVIDER
        if name not in cls.PROVIDERS:
            raise ValueError(f"YT_AVATAR_PROVIDER must be one of {sorted(cls.PROVIDERS)}, got {name!r}")
        return name

    @classmethod
    def get(cls, provider: Optional[str] = None) -> AvatarPort:
        name = (provider or cls.default_provider()).lower()
        if name not in cls.PROVIDERS:
            raise ValueError(f"Unknown avatar provider {name!r}. Known: {sorted(cls.PROVIDERS)}")
        if name not in cls._instances:
            with cls._lock:
                if name not in cls._instances:
                    cls._instances[name] = cls.PROVIDERS[name]()
        return cls._instances[name]
