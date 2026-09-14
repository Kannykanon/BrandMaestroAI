"""Voice adapters, the registry, and the shared audio format."""
import io
import os
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from youtube.audio import SAMPLE_RATE, Audio, concatenate, silence
from youtube.voice import GoogleTTSVoice, KokoroVoice, VoicePort, VoiceRegistry


class TestAudio:
    def test_wav_round_trip(self):
        audio = Audio(np.linspace(-1, 1, SAMPLE_RATE, dtype=np.float32))
        back = Audio.from_wav(audio.to_wav())
        assert back.sample_rate == SAMPLE_RATE
        assert back.duration_s == pytest.approx(1.0)
        assert np.allclose(back.samples, audio.samples, atol=1e-4)

    def test_stereo_wav_is_mixed_to_mono(self):
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as out:
            out.setnchannels(2)
            out.setsampwidth(2)
            out.setframerate(SAMPLE_RATE)
            out.writeframes(np.array([1000, 3000] * 10, dtype="<i2").tobytes())
        audio = Audio.from_wav(buffer.getvalue())
        assert len(audio.samples) == 10
        assert audio.samples[0] == pytest.approx(2000 / 32767)

    def test_concatenate_inserts_gaps_between_clips_only(self):
        one = Audio(np.ones(SAMPLE_RATE, dtype=np.float32))
        track = concatenate([one, one, one], gaps=[0.5, 0.25])
        assert track.duration_s == pytest.approx(3.75)
        assert track.samples[SAMPLE_RATE: SAMPLE_RATE + SAMPLE_RATE // 2].max() == 0

    def test_concatenate_rejects_mixed_rates_and_wrong_gap_counts(self):
        with pytest.raises(ValueError):
            concatenate([silence(1), Audio(np.zeros(10), sample_rate=16000)])
        with pytest.raises(ValueError):
            concatenate([silence(1), silence(1)], gaps=[0.1, 0.1])

    def test_only_mono(self):
        with pytest.raises(ValueError):
            Audio(np.zeros((2, 10)))


@pytest.fixture
def kokoro_dir(tmp_path):
    np.savez(tmp_path / KokoroVoice.VOICES_FILE, af_heart=np.zeros(3), bm_george=np.zeros(3), jf_alpha=np.zeros(3))
    # np.savez appends .npz; Kokoro's file has no extension.
    (tmp_path / (KokoroVoice.VOICES_FILE + ".npz")).rename(tmp_path / KokoroVoice.VOICES_FILE)
    (tmp_path / KokoroVoice.DEFAULT_MODEL).write_bytes(b"model")
    return tmp_path


class TestKokoro:
    def test_voice_ids_describe_language_and_gender(self):
        assert KokoroVoice.describe("bm_george") == KokoroVoice.describe("bm_george")
        voice = KokoroVoice.describe("af_heart")
        assert (voice.name, voice.language, voice.gender, voice.provider) == ("Heart", "en-us", "female", "kokoro")
        assert KokoroVoice.describe("bm_george").language == "en-gb"
        assert KokoroVoice.describe("jf_alpha").language == "ja"

    def test_lists_voices_without_loading_the_model(self, kokoro_dir):
        port = KokoroVoice(model_dir=str(kokoro_dir))
        assert [v.id for v in port.list_voices()] == ["af_heart", "bm_george", "jf_alpha"]
        assert port._engine is None

    def test_synthesizes_with_the_voice_language(self, kokoro_dir, monkeypatch):
        calls = []

        class _Engine:
            def create(self, text, voice, speed, lang):
                calls.append((text, voice, speed, lang))
                return np.zeros(SAMPLE_RATE // 2, dtype=np.float32), SAMPLE_RATE

        port = KokoroVoice(model_dir=str(kokoro_dir))
        monkeypatch.setattr(port, "_get_engine", lambda: _Engine())
        audio = port.synthesize("Cheerio.", "bm_george", speed=1.1)
        assert calls == [("Cheerio.", "bm_george", 1.1, "en-gb")]
        assert audio.duration_s == pytest.approx(0.5)

    def test_rejects_unknown_voices_and_empty_text(self, kokoro_dir):
        port = KokoroVoice(model_dir=str(kokoro_dir))
        with pytest.raises(ValueError, match="Unknown Kokoro voice"):
            port.synthesize("Hi.", "zz_nobody")
        with pytest.raises(ValueError, match="Nothing to synthesize"):
            port.synthesize("  ", "af_heart")

    def test_downloads_missing_files_once_without_leaving_partials(self, tmp_path, monkeypatch):
        fetched = []

        def fake_retrieve(url, destination):
            fetched.append(url)
            Path(destination).write_bytes(b"data")

        monkeypatch.setattr("youtube.voice.urllib.request.urlretrieve", fake_retrieve)
        port = KokoroVoice(model_dir=str(tmp_path / "models"))
        path = port._ensure_file("voices-v1.0.bin")
        port._ensure_file("voices-v1.0.bin")
        assert fetched == [f"{KokoroVoice.RELEASE_URL}/voices-v1.0.bin"]
        assert path.read_bytes() == b"data"
        assert not list((tmp_path / "models").glob("*.part"))

    def test_costs_nothing(self, kokoro_dir):
        assert KokoroVoice(model_dir=str(kokoro_dir)).cost_usd("x" * 10_000) == 0.0


class _FakeTTSClient:
    def __init__(self):
        self.requests = []

    def list_voices(self, language_code):
        return SimpleNamespace(voices=[
            SimpleNamespace(name="en-US-Chirp3-HD-Charon", language_codes=["en-US"], ssml_gender=1),
            SimpleNamespace(name="en-US-Chirp3-HD-Aoede", language_codes=["en-US"], ssml_gender=2),
        ])

    def synthesize_speech(self, input, voice, audio_config):
        self.requests.append((input.text, voice.language_code, voice.name, audio_config.sample_rate_hertz))
        return SimpleNamespace(audio_content=Audio(np.zeros(SAMPLE_RATE, dtype=np.float32)).to_wav())


class TestGoogleTTS:
    def test_lists_and_synthesizes(self):
        client = _FakeTTSClient()
        port = GoogleTTSVoice(client=client)
        voices = port.list_voices()
        assert [(v.id, v.gender) for v in voices] == [
            ("en-US-Chirp3-HD-Aoede", "female"), ("en-US-Chirp3-HD-Charon", "male")]
        audio = port.synthesize("Hello there.", "en-US-Chirp3-HD-Charon")
        assert client.requests == [("Hello there.", "en-US", "en-US-Chirp3-HD-Charon", SAMPLE_RATE)]
        assert audio.duration_s == pytest.approx(1.0)

    def test_cost_is_recorded_only_when_a_rate_is_configured(self, monkeypatch):
        port = GoogleTTSVoice(client=_FakeTTSClient())
        monkeypatch.delenv("YT_GOOGLE_TTS_USD_PER_MILLION_CHARS", raising=False)
        assert port.cost_usd("x" * 1000) == 0.0
        monkeypatch.setenv("YT_GOOGLE_TTS_USD_PER_MILLION_CHARS", "30")
        assert port.cost_usd("x" * 1000) == pytest.approx(0.03)


class TestRegistry:
    @pytest.fixture(autouse=True)
    def clean(self, monkeypatch):
        monkeypatch.setattr(VoiceRegistry, "_instances", {})
        monkeypatch.delenv("YT_VOICE_PROVIDER", raising=False)

    def test_adapters_implement_the_port(self):
        for adapter in VoiceRegistry.PROVIDERS.values():
            assert issubclass(adapter, VoicePort)

    def test_defaults_to_kokoro(self):
        assert VoiceRegistry.default_provider() == "kokoro"
        assert isinstance(VoiceRegistry.get(), KokoroVoice)

    def test_env_switches_the_default(self, monkeypatch):
        monkeypatch.setenv("YT_VOICE_PROVIDER", "google_tts")
        assert isinstance(VoiceRegistry.get(), GoogleTTSVoice)

    def test_named_provider_and_shared_instances(self):
        assert VoiceRegistry.get("google_tts") is VoiceRegistry.get("GOOGLE_TTS")

    def test_unknown_provider_is_a_clear_error(self, monkeypatch):
        with pytest.raises(ValueError, match="Unknown voice provider"):
            VoiceRegistry.get("elevenlabs")
        monkeypatch.setenv("YT_VOICE_PROVIDER", "nope")
        with pytest.raises(ValueError, match="YT_VOICE_PROVIDER must be one of"):
            VoiceRegistry.default_provider()


KOKORO_DIR = os.getenv("YT_TEST_KOKORO_MODEL_DIR", "")


@pytest.mark.skipif(not (KOKORO_DIR and Path(KOKORO_DIR, KokoroVoice.DEFAULT_MODEL).is_file()),
                    reason="set YT_TEST_KOKORO_MODEL_DIR to a directory with the Kokoro model to run")
def test_real_kokoro_synthesis():
    port = KokoroVoice(model_dir=KOKORO_DIR)
    audio = port.synthesize("We did it.", "af_heart")
    assert audio.sample_rate == SAMPLE_RATE
    assert 0.3 < audio.duration_s < 5
    assert np.abs(audio.samples).max() > 0.01, "the clip should not be silent"
