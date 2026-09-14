"""Short audio samples of every voice, generated once so characters can be cast by ear.

Samples are shared by all businesses: they contain no business data. A small
index file records which voices have a sample, so listing voices does not
need one storage lookup per voice.
"""
from __future__ import annotations

import json
import logging

from youtube.storage import StoragePort, validate_key
from youtube.voice import VoiceRegistry

logger = logging.getLogger(__name__)

PREVIEW_TEXT = "Every story starts somewhere. This is how I would tell yours."


def _safe(value: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in value)


def preview_key(provider: str, voice_id: str) -> str:
    return validate_key(f"voice-previews/{_safe(provider)}/{_safe(voice_id)}.wav")


def index_key(provider: str) -> str:
    return validate_key(f"voice-previews/{_safe(provider)}/index.json")


def available_previews(storage: StoragePort, provider: str) -> set[str]:
    try:
        return set(json.loads(storage.get(index_key(provider))).get("voices", []))
    except FileNotFoundError:
        return set()
    except Exception as e:
        logger.warning("Voice preview index unreadable for %s: %s", provider, e)
        return set()


def generate_previews(storage: StoragePort, provider_name: str | None = None, force: bool = False) -> dict:
    """Synthesize a sample for every voice that lacks one. Returns counts."""
    provider = VoiceRegistry.get(provider_name)
    done = set() if force else available_previews(storage, provider.name)
    created, failed = 0, []
    for voice in provider.list_voices():
        if voice.id in done:
            continue
        try:
            audio = provider.synthesize(PREVIEW_TEXT, voice.id)
            storage.put(preview_key(provider.name, voice.id), audio.to_wav(), content_type="audio/wav")
            done.add(voice.id)
            created += 1
        except Exception as e:
            logger.warning("Preview failed for %s/%s: %s", provider.name, voice.id, e)
            failed.append(voice.id)
        # Written after each voice, so an interrupted run keeps what it finished.
        storage.put(index_key(provider.name), json.dumps({"voices": sorted(done)}).encode(),
                    content_type="application/json")
    return {"provider": provider.name, "created": created, "available": len(done), "failed": failed}
