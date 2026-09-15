"""Sound design: a music bed and per-shot ambience, lowered automatically under speech.

    music      a track the business uploads (asset kind "music"); looped or trimmed to the
               video, faded out at the end. Licensing is the uploader's responsibility.
    ambience   a short sound description per shot ("heavy rain, distant thunder"), written
               by the planner and editable; generated once per distinct description by a
               SoundPort adapter and cached, so re-renders do not pay again
    mix        both beds are ducked under the voice with a sidechain compressor (media.mix_audio)

    YT_SOUND_PROVIDER        off | elevenlabs   (default off: music only, no generated ambience)
    ELEVENLABS_API_KEY       for elevenlabs
    YT_SOUND_USD_PER_CLIP    price per generated ambience clip, for cost records (default 0)
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from youtube import projects
from youtube.images import RateLimited, with_rate_limit_retry
from youtube.models import YTCost, YTProject
from youtube.projects import ProjectError
from youtube.storage import StoragePort, business_key

logger = logging.getLogger(__name__)

AUDIO_DEFAULTS = {"music_asset_id": None, "music_volume": 0.15, "ambience_volume": 0.35}
MAX_CLIP_SECONDS = 30.0


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


class SoundError(RuntimeError):
    """The provider did not return a sound."""


class SoundPort(ABC):
    name: str = ""
    required_env: tuple[str, ...] = ()

    @classmethod
    def missing_env(cls) -> list[str]:
        return [v for v in cls.required_env if not _env(v)]

    @property
    def enabled(self) -> bool:
        return True

    def cost_usd(self) -> float:
        value = _env("YT_SOUND_USD_PER_CLIP")
        return float(value) if value else 0.0

    @abstractmethod
    def generate(self, description: str, seconds: float, loop: bool = True) -> bytes:
        """An MP3 of the described sound, about `seconds` long."""


class NoSound(SoundPort):
    name = "off"

    @property
    def enabled(self) -> bool:
        return False

    def generate(self, description, seconds, loop=True):
        raise SoundError("Sound effects are off (set YT_SOUND_PROVIDER)")


class ElevenLabsSound(SoundPort):
    """ElevenLabs text to sound effects (POST /v1/sound-generation). Checked 2026-09-15."""

    name = "elevenlabs"
    required_env = ("ELEVENLABS_API_KEY",)
    URL = "https://api.elevenlabs.io/v1/sound-generation"

    def __init__(self, http=None, sleep=time.sleep):
        self._http = http
        self._sleep = sleep

    def _get_http(self):
        if self._http is None:
            import httpx
            self._http = httpx.Client(timeout=180)
        return self._http

    def generate(self, description, seconds, loop=True):
        key = _env("ELEVENLABS_API_KEY")
        if not key:
            raise SoundError("ELEVENLABS_API_KEY is not set")
        body = {"text": description, "duration_seconds": round(min(max(seconds, 0.5), MAX_CLIP_SECONDS), 1),
                "loop": loop, "prompt_influence": 0.4}

        def call():
            response = self._get_http().post(self.URL, params={"output_format": "mp3_44100_128"}, json=body,
                                             headers={"xi-api-key": key})
            if response.status_code == 429:
                raise RateLimited("ElevenLabs rate limit")
            if response.status_code >= 400:
                raise SoundError(f"ElevenLabs sound failed ({response.status_code}): {response.text[:300]}")
            return response.content

        return with_rate_limit_retry(call, lambda e: isinstance(e, RateLimited), sleep=self._sleep)


class SoundRegistry:
    PROVIDERS: dict[str, type[SoundPort]] = {NoSound.name: NoSound, ElevenLabsSound.name: ElevenLabsSound}
    _instances: dict[str, SoundPort] = {}
    _lock = threading.Lock()

    @classmethod
    def get(cls, provider: Optional[str] = None) -> SoundPort:
        name = (provider or _env("YT_SOUND_PROVIDER") or NoSound.name).lower()
        if name not in cls.PROVIDERS:
            raise ValueError(f"YT_SOUND_PROVIDER must be one of {sorted(cls.PROVIDERS)}, got {name!r}")
        with cls._lock:
            if name not in cls._instances:
                cls._instances[name] = cls.PROVIDERS[name]()
        return cls._instances[name]


# ---------------------------------------------------------------------------
#  Project settings
# ---------------------------------------------------------------------------
def audio_settings(project: YTProject) -> dict:
    return {**AUDIO_DEFAULTS, **(project.audio or {})}


def set_project_audio(db: Session, project: YTProject, data: dict) -> YTProject:
    from youtube.brand_assets import get_asset

    projects._require_not_busy(project)
    settings = {**audio_settings(project), **{k: v for k, v in (data or {}).items() if k in AUDIO_DEFAULTS}}
    for key in ("music_volume", "ambience_volume"):
        try:
            settings[key] = round(min(max(float(settings[key]), 0.0), 1.0), 2)
        except (TypeError, ValueError) as e:
            raise ProjectError(f"{key} must be a number from 0 to 1") from e
    if settings["music_asset_id"] is not None:
        music = get_asset(db, project.business_id, int(settings["music_asset_id"]))
        if music is None or music.kind != "music":
            raise ProjectError("Choose one of this business's music tracks")
        settings["music_asset_id"] = music.id
    project.audio = settings
    projects.settle_status(db, project)  # a different mix makes the last render out of date
    db.commit()
    db.refresh(project)
    return project


def set_shot_sound(db: Session, project: YTProject, shot_id: int, sound: str) -> None:
    """Describe the ambience under a shot ('' for none). The picture is unchanged, so approval stays."""
    from sqlalchemy import select
    from youtube.models import YTShot

    projects._require_not_busy(project)
    shot = db.execute(select(YTShot).where(YTShot.id == shot_id, YTShot.project_id == project.id)).scalar_one_or_none()
    if shot is None:
        raise ProjectError("No such shot in this project")
    shot.sound = " ".join((sound or "").split())[:120] or None
    projects.settle_status(db, project)
    db.commit()


def mix_snapshot(db: Session, project: YTProject, port: Optional[SoundPort] = None) -> dict:
    """Everything the audio mix depends on, stored on each render to tell when it is out of date."""
    port = port or SoundRegistry.get()
    settings = audio_settings(project)
    shots = projects._shots(db, project)
    return {**settings, "provider": port.name if port.enabled else "off",
            "sounds": [[s.position, s.sound] for s in shots if s.sound] if port.enabled else []}


# ---------------------------------------------------------------------------
#  Ambience clips
# ---------------------------------------------------------------------------
@dataclass
class Bed:
    key: str
    start_s: float
    length_s: float


def _clip_key(project: YTProject, description: str) -> str:
    digest = hashlib.sha1(description.lower().encode("utf-8")).hexdigest()[:16]
    return business_key(project.business_id, "projects", str(project.id), "sound", f"ambience-{digest}.mp3")


def ambience_beds(db: Session, project: YTProject, timeline_items, storage: StoragePort,
                  port: Optional[SoundPort] = None) -> list[Bed]:
    """One bed per shot with a sound description. Each distinct description is generated once and cached."""
    port = port or SoundRegistry.get()
    if not port.enabled:
        return []
    if port.missing_env():
        raise ProjectError(f"Sound provider {port.name} is not configured (missing {', '.join(port.missing_env())})")
    longest: dict[str, float] = {}
    for item in timeline_items:
        if item.shot.sound:
            longest[item.shot.sound] = max(longest.get(item.shot.sound, 0.0), item.length_s)
    for description, seconds in longest.items():
        key = _clip_key(project, description)
        if storage.exists(key):
            continue
        try:
            data = port.generate(description, min(seconds, MAX_CLIP_SECONDS), loop=True)
        except Exception as e:
            raise ProjectError(f"Generating the sound \"{description}\" failed: {e}") from e
        storage.put(key, data, content_type="audio/mpeg")
        db.add(YTCost(project_id=project.id, stage="sound", provider=port.name, units=1.0, unit="clip",
                      cost_usd=port.cost_usd()))
        db.commit()
    return [Bed(_clip_key(project, i.shot.sound), i.start, i.length_s) for i in timeline_items if i.shot.sound]
