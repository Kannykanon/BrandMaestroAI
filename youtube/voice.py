"""Character voices, with the same plug-and-adapter design as model.py.

    VoicePort       the abstract adapter
    KokoroVoice     Kokoro-82M run locally on CPU, no API key, no per-use cost
    GoogleTTSVoice  Google Cloud Text-to-Speech, using Application Default Credentials
    VoiceRegistry   one shared instance per provider

    YT_VOICE_PROVIDER   kokoro | google_tts   (default kokoro): the provider new
                        characters use unless they name one
    YT_KOKORO_MODEL_DIR where Kokoro's model files are downloaded (default .cache/kokoro)
    YT_KOKORO_MODEL     model file (default kokoro-v1.0.onnx; the int8 build is
                        about 10x slower on older CPUs without VNNI)
    YT_GOOGLE_TTS_LANGUAGE  language whose voices are listed (default en-US)

Characters store their provider with their voice, so a story can mix voices
from different providers. Every adapter returns youtube.audio.SAMPLE_RATE mono.
"""
from __future__ import annotations

import logging
import os
import threading
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from youtube.audio import SAMPLE_RATE, Audio

logger = logging.getLogger(__name__)


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


@dataclass(frozen=True)
class Voice:
    id: str
    name: str
    language: str
    gender: Optional[str]  # "female" | "male" | None
    provider: str

    def to_dict(self) -> dict:
        return asdict(self)


class VoicePort(ABC):
    """Adapter between YouTube Automation and one text-to-speech provider."""

    #: Value of YT_VOICE_PROVIDER / characters.voice_provider that selects this adapter.
    name: str = ""
    #: Environment variables that must be set before this provider can be used.
    required_env: tuple[str, ...] = ()

    @classmethod
    def missing_env(cls) -> list[str]:
        return [var for var in cls.required_env if not _env(var)]

    @abstractmethod
    def list_voices(self) -> list[Voice]: ...

    @abstractmethod
    def synthesize(self, text: str, voice_id: str, speed: float = 1.0) -> Audio:
        """Speak text in the voice. Returns SAMPLE_RATE mono audio."""

    def cost_usd(self, text: str) -> float:
        """What synthesizing text costs with this provider."""
        return 0.0

    def has_voice(self, voice_id: str) -> bool:
        return any(v.id == voice_id for v in self.list_voices())


class KokoroVoice(VoicePort):
    """Kokoro-82M (Apache 2.0) through kokoro-onnx, on CPU.

    Model files are downloaded on first use. Voice ids encode language and
    gender: the first letter is the language (a = American English,
    b = British English, ...) and the second is f or m.
    """

    name = "kokoro"
    RELEASE_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1"
    DEFAULT_MODEL = "kokoro-v1.0.onnx"
    VOICES_FILE = "voices-v1.0.bin"
    LANGUAGES = {
        "a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi",
        "i": "it", "j": "ja", "p": "pt-br", "z": "cmn",
    }

    def __init__(self, model_dir: str | None = None, model_file: str | None = None):
        self._dir = Path(model_dir or _env("YT_KOKORO_MODEL_DIR") or ".cache/kokoro")
        self._model_file = model_file or _env("YT_KOKORO_MODEL") or self.DEFAULT_MODEL
        self._engine = None
        self._voice_ids: Optional[list[str]] = None
        self._lock = threading.Lock()

    def _ensure_file(self, filename: str) -> Path:
        path = self._dir / filename
        if path.is_file():
            return path
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".part")
        logger.info("Downloading Kokoro file %s", filename)
        urllib.request.urlretrieve(f"{self.RELEASE_URL}/{filename}", tmp)
        tmp.replace(path)  # never leave a half-downloaded file under the real name
        return path

    def _get_engine(self):
        with self._lock:
            if self._engine is None:
                from kokoro_onnx import Kokoro

                self._engine = Kokoro(
                    str(self._ensure_file(self._model_file)),
                    str(self._ensure_file(self.VOICES_FILE)),
                )
            return self._engine

    def _list_voice_ids(self) -> list[str]:
        if self._voice_ids is None:
            with self._lock:
                if self._voice_ids is None:
                    # The voice table is a plain numpy archive; reading it does
                    # not need the 300 MB model to be loaded.
                    with np.load(self._ensure_file(self.VOICES_FILE)) as table:
                        self._voice_ids = sorted(table.files)
        return self._voice_ids

    @classmethod
    def describe(cls, voice_id: str) -> Voice:
        language = cls.LANGUAGES.get(voice_id[:1], "unknown")
        gender = {"f": "female", "m": "male"}.get(voice_id[1:2])
        label = voice_id.split("_", 1)[-1].replace("_", " ").title()
        return Voice(id=voice_id, name=label, language=language, gender=gender, provider=cls.name)

    def list_voices(self) -> list[Voice]:
        return [self.describe(v) for v in self._list_voice_ids()]

    def synthesize(self, text: str, voice_id: str, speed: float = 1.0) -> Audio:
        if not text.strip():
            raise ValueError("Nothing to synthesize")
        if not self.has_voice(voice_id):
            raise ValueError(f"Unknown Kokoro voice {voice_id!r}")
        lang = self.LANGUAGES.get(voice_id[:1], "en-us")
        samples, rate = self._get_engine().create(text, voice=voice_id, speed=speed, lang=lang)
        if rate != SAMPLE_RATE:
            raise RuntimeError(f"Kokoro returned {rate} Hz; expected {SAMPLE_RATE}")
        return Audio(samples=samples, sample_rate=rate)


class GoogleTTSVoice(VoicePort):
    """Google Cloud Text-to-Speech. Authenticates with Application Default Credentials.

    Set YT_GOOGLE_TTS_USD_PER_MILLION_CHARS to record costs; otherwise usage is
    recorded at $0 because prices differ by voice type.
    """

    name = "google_tts"

    def __init__(self, client=None, language: str | None = None):
        self._client = client
        self._language = language or _env("YT_GOOGLE_TTS_LANGUAGE") or "en-US"
        self._voices: Optional[list[Voice]] = None

    def _get_client(self):
        if self._client is None:
            from google.cloud import texttospeech
            self._client = texttospeech.TextToSpeechClient()
        return self._client

    def list_voices(self) -> list[Voice]:
        if self._voices is None:
            response = self._get_client().list_voices(language_code=self._language)
            genders = {1: "male", 2: "female"}
            self._voices = sorted(
                (
                    Voice(
                        id=v.name,
                        name=v.name,
                        language=(list(v.language_codes) or [self._language])[0],
                        gender=genders.get(int(v.ssml_gender)),
                        provider=self.name,
                    )
                    for v in response.voices
                ),
                key=lambda voice: voice.id,
            )
        return self._voices

    def synthesize(self, text: str, voice_id: str, speed: float = 1.0) -> Audio:
        from google.cloud import texttospeech

        if not text.strip():
            raise ValueError("Nothing to synthesize")
        # Voice names start with their language code, e.g. en-US-Chirp3-HD-Charon.
        language = "-".join(voice_id.split("-")[:2])
        response = self._get_client().synthesize_speech(
            input=texttospeech.SynthesisInput(text=text),
            voice=texttospeech.VoiceSelectionParams(language_code=language, name=voice_id),
            audio_config=texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.LINEAR16,
                sample_rate_hertz=SAMPLE_RATE,
                speaking_rate=speed,
            ),
        )
        audio = Audio.from_wav(response.audio_content)
        if audio.sample_rate != SAMPLE_RATE:
            raise RuntimeError(f"Google TTS returned {audio.sample_rate} Hz; expected {SAMPLE_RATE}")
        return audio

    def cost_usd(self, text: str) -> float:
        rate = _env("YT_GOOGLE_TTS_USD_PER_MILLION_CHARS")
        return len(text) * float(rate) / 1_000_000 if rate else 0.0


class VoiceRegistry:
    """One shared instance per voice provider. The switch for new characters is YT_VOICE_PROVIDER."""

    PROVIDERS: dict[str, type[VoicePort]] = {
        KokoroVoice.name: KokoroVoice,
        GoogleTTSVoice.name: GoogleTTSVoice,
    }
    DEFAULT_PROVIDER = KokoroVoice.name

    _instances: dict[str, VoicePort] = {}
    _lock = threading.Lock()

    @classmethod
    def default_provider(cls) -> str:
        name = _env("YT_VOICE_PROVIDER").lower() or cls.DEFAULT_PROVIDER
        if name not in cls.PROVIDERS:
            raise ValueError(f"YT_VOICE_PROVIDER must be one of {sorted(cls.PROVIDERS)}, got {name!r}")
        return name

    @classmethod
    def get(cls, provider: str | None = None) -> VoicePort:
        name = (provider or cls.default_provider()).lower()
        if name not in cls.PROVIDERS:
            raise ValueError(f"Unknown voice provider {name!r}. Known: {sorted(cls.PROVIDERS)}")
        if name not in cls._instances:
            with cls._lock:
                if name not in cls._instances:
                    cls._instances[name] = cls.PROVIDERS[name]()
        return cls._instances[name]
