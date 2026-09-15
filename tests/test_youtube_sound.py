"""Sound design (music, ambience, ducking) and colour-matched talking clips."""
import io
import wave
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image, ImageStat

import youtube.projects as projects
import youtube.sound as sound
import youtube.storyboard as storyboard
from tests.test_youtube_render import FakeHTTP, Response, approved_project, needs_ffmpeg
from tests.test_youtube_storyboard import env, ready_project  # noqa: F401  (fixture)
from tests.test_youtube_storyboard_routes import api  # noqa: F401  (fixture)
from youtube.media import RenderSettings, clip_segment, mix_audio, run_ffmpeg, still_segment


def wav_bytes(seconds, freq=440.0, amplitude=0.3, rate=48000, silence_ranges=()):
    t = np.arange(int(seconds * rate)) / rate
    signal = amplitude * np.sin(2 * np.pi * freq * t)
    for start, end in silence_ranges:
        signal[int(start * rate):int(end * rate)] = 0
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(rate)
        w.writeframes((signal * 32767).astype(np.int16).tobytes())
    return out.getvalue()


def read_wav(path):
    with wave.open(str(path)) as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        return data.reshape(-1, w.getnchannels()).mean(axis=1), w.getframerate()


class FakeSound(sound.SoundPort):
    name = "fake_sound"

    def __init__(self):
        self.calls = []

    def generate(self, description, seconds, loop=True):
        self.calls.append((description, round(seconds, 2), loop))
        return wav_bytes(1.0, freq=150)


class TestAdapter:
    def test_elevenlabs_request(self, monkeypatch):
        monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
        http = FakeHTTP([("POST", "api.elevenlabs.io/v1/sound-generation", Response(content=b"MP3"))])
        assert sound.ElevenLabsSound(http=http).generate("heavy rain", 45, loop=True) == b"MP3"
        request = http.requests[0]
        assert request.headers == {"xi-api-key": "k"} and request.params == {"output_format": "mp3_44100_128"}
        assert request.json == {"text": "heavy rain", "duration_seconds": 30.0, "loop": True, "prompt_influence": 0.4}

    def test_errors_and_missing_key(self, monkeypatch):
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        with pytest.raises(sound.SoundError, match="ELEVENLABS_API_KEY"):
            sound.ElevenLabsSound(http=FakeHTTP([])).generate("rain", 3)
        monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
        http = FakeHTTP([("POST", "sound-generation", Response(401, {"detail": "invalid key"}))])
        with pytest.raises(sound.SoundError, match="401"):
            sound.ElevenLabsSound(http=http, sleep=lambda s: None).generate("rain", 3)

    def test_registry_defaults_to_off(self, monkeypatch):
        monkeypatch.delenv("YT_SOUND_PROVIDER", raising=False)
        assert sound.SoundRegistry.get().enabled is False


class TestProjectSound:
    def test_planner_writes_a_sound_line(self):
        from tests.test_youtube_projects import SCRIPT
        from youtube.planner import annotate_shots
        from youtube.script_parser import plan_shots

        _, shots = plan_shots(SCRIPT)
        llm = SimpleNamespace(invoke=lambda prompt: SimpleNamespace(
            content='{"shots": [{"position": 1, "sound": "  busy   cafe chatter "}], "speakers": {}}'))
        plans = annotate_shots(shots, "short", llm=llm).shots
        assert plans[1].sound == "busy cafe chatter" and plans[2].sound == ""

    def test_audio_settings_validate_and_shot_sound_keeps_approval(self, env):
        from youtube import brand_assets

        project = approved_project(env)
        with pytest.raises(projects.ProjectError, match="music tracks"):
            sound.set_project_audio(env.db, project, {"music_asset_id": 999})
        track = brand_assets.create_asset(env.db, "biz", "Storm theme", "music", wav_bytes(2), env.storage)
        project = sound.set_project_audio(env.db, project, {"music_asset_id": track.id, "music_volume": 3})
        assert project.audio["music_volume"] == 1.0 and project.audio["music_asset_id"] == track.id
        shot = projects._shots(env.db, project)[0]
        sound.set_shot_sound(env.db, project, shot.id, "  heavy   rain ")
        assert shot.sound == "heavy rain" and project.storyboard_approved_at is not None

    def test_music_assets_must_be_audio(self, env):
        from youtube import brand_assets
        from tests.test_youtube_storyboard import photo

        with pytest.raises(projects.ProjectError, match="MP3"):
            brand_assets.create_asset(env.db, "biz", "Not music", "music", photo(), env.storage)
        assert brand_assets.audio_type(b"ID3\x04" + bytes(8)) == "mp3"

    def test_ambience_is_generated_once_per_description_and_cached(self, env, monkeypatch):
        import youtube.render as render
        from youtube.avatar import StillAvatar

        project = approved_project(env)
        shots = projects._shots(env.db, project)
        for shot in shots:
            sound.set_shot_sound(env.db, project, shot.id, "heavy rain")
        port = FakeSound()
        items = render.timeline(shots, StillAvatar())
        beds = sound.ambience_beds(env.db, project, items, env.storage, port)
        assert len(port.calls) == 1 and len(beds) == len(shots)
        assert beds[1].start_s == pytest.approx(items[1].start)
        sound.ambience_beds(env.db, project, items, env.storage, port)
        assert len(port.calls) == 1, "cached"
        assert sound.ambience_beds(env.db, project, items, env.storage, sound.NoSound()) == []

    def test_changing_the_mix_makes_a_render_out_of_date(self, env):
        from datetime import datetime, timedelta, timezone
        import youtube.render as render
        from youtube.models import YTRender

        project = approved_project(env)
        env.db.add(YTRender(project_id=project.id, format=project.format, status="completed", video_key="v.mp4",
                            mix=sound.mix_snapshot(env.db, project), created_at=datetime.now(timezone.utc) + timedelta(seconds=1)))
        projects.settle_status(env.db, project)
        env.db.commit()
        assert project.status == "rendered"
        sound.set_project_audio(env.db, project, {"ambience_volume": 0.5})
        assert project.status == "storyboard_approved"


@needs_ffmpeg
class TestMedia:
    def test_talking_clip_is_colour_matched_to_its_still(self, tmp_path):
        still = tmp_path / "still.png"
        Image.effect_noise((320, 180), 40).convert("RGB").point(lambda v: min(v + 60, 255)).save(still)
        dull = tmp_path / "dull.png"
        with Image.open(still) as image:
            r, g, b = image.split()
            Image.merge("RGB", (r.point(lambda v: v * 0.6), g.point(lambda v: v * 0.6), b.point(lambda v: v * 0.8))).save(dull)
        settings = RenderSettings(320, 180, fps=10, preset="ultrafast")
        clip = tmp_path / "clip.mp4"
        still_segment(dull, 10, clip, settings, pan=False)

        plain, matched = tmp_path / "plain.mp4", tmp_path / "matched.mp4"
        clip_segment(clip, 10, plain, settings)
        clip_segment(clip, 10, matched, settings, match_to=still)

        def mean(video):
            frame = tmp_path / f"{video.stem}.png"
            run_ffmpeg(["-ss", "0.5", "-i", str(video), "-frames:v", "1", str(frame)])
            with Image.open(frame) as image:
                return ImageStat.Stat(image.convert("RGB")).mean
        with Image.open(still) as image:
            target = ImageStat.Stat(image).mean
        plain_error = sum(abs(a - b) for a, b in zip(mean(plain), target))
        matched_error = sum(abs(a - b) for a, b in zip(mean(matched), target))
        assert matched_error < plain_error / 3

    def test_music_ducks_under_speech_and_the_mix_keeps_the_voice_length(self, tmp_path):
        voice = tmp_path / "voice.wav"
        voice.write_bytes(wav_bytes(4.0, freq=300, amplitude=0.5, silence_ranges=[(2.0, 4.0)]))
        music = tmp_path / "music.wav"
        music.write_bytes(wav_bytes(1.5, freq=1000, amplitude=0.5))
        out = tmp_path / "mixed.wav"
        mix_audio(voice, 4.0, out, music, 0.5, [])
        mixed, rate = read_wav(out)
        assert len(mixed) / rate == pytest.approx(4.0, abs=0.05)

        def music_level(start, end):
            segment = mixed[int(start * rate):int(end * rate)]
            spectrum = np.abs(np.fft.rfft(segment))
            freqs = np.fft.rfftfreq(len(segment), 1 / rate)
            return spectrum[(freqs > 950) & (freqs < 1050)].sum()
        assert music_level(0.6, 1.4) < music_level(2.6, 3.4) / 2, "music is lowered while the voice speaks"


def test_routes(api):
    from tests.test_youtube_render_routes import approved as approved_via_api

    c = api.client
    pid = approved_via_api(api)
    track = c.post("/youtube/assets", data={"name": "Storm theme", "kind": "music"},
                   files={"file": ("storm.wav", wav_bytes(2), "audio/wav")})
    assert track.status_code == 201
    tid = track.json()["id"]
    assert c.get(f"/youtube/assets/{tid}/image").headers["content-type"] == "audio/wav"

    detail = c.patch(f"/youtube/projects/{pid}", json={"audio": {"music_asset_id": tid, "music_volume": 0.2}}).json()
    assert detail["audio"]["music_asset_id"] == tid and detail["audio"]["music_volume"] == 0.2
    shot = detail["shots"][0]
    detail = c.patch(f"/youtube/projects/{pid}/shots/{shot['id']}", json={"sound": "wind over a mountain"}).json()
    assert detail["shots"][0]["sound"] == "wind over a mountain"
    assert c.patch(f"/youtube/projects/{pid}", json={"audio": {"music_volume": "loud"}}).status_code == 400
