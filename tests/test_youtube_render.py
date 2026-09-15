"""Phase 3: captions, avatar adapters, the ffmpeg composer and the render service."""
import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image

import youtube.projects as projects
import youtube.render as render
import youtube.storyboard as storyboard
from tests.test_youtube_storyboard import env, ready_project  # noqa: F401  (fixture)
from youtube.audio import Audio
from youtube.avatar import (
    AvatarClip,
    AvatarError,
    AvatarPort,
    AvatarRegistry,
    InfiniteTalkAvatar,
    KlingAvatar,
    StillAvatar,
)
from youtube.captions import Cue, TimedWord, build_cues, to_ass, word_timings
from youtube.media import RenderSettings, clip_segment, ffmpeg_available, media_info, still_segment, thumbnail
from youtube.models import YTCost, YTRender

needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg is not installed")


# ---------------------------------------------------------------------------
#  Captions
# ---------------------------------------------------------------------------
class TestCaptions:
    def test_words_fill_the_line_in_order(self):
        words = word_timings("Hello there, my old friend.", start=10.0, duration=3.0)
        assert [w.text for w in words] == ["Hello", "there,", "my", "old", "friend."]
        assert words[0].start >= 10.0 and words[-1].end <= 13.0
        assert all(a.end <= b.start + 1e-9 for a, b in zip(words, words[1:]))
        assert all(w.end > w.start for w in words)

    def test_longer_words_take_longer_and_punctuation_adds_a_pause(self):
        words = word_timings("a extraordinary, b", start=0.0, duration=4.0)
        assert (words[1].end - words[1].start) > (words[0].end - words[0].start) * 3
        pause_after_comma = words[2].start - words[1].end
        pause_after_a = words[1].start - words[0].end
        assert pause_after_comma > pause_after_a + 0.1

    def test_empty_text_or_no_time_gives_no_words(self):
        assert word_timings("", 0, 2) == []
        assert word_timings("Hello", 0, 0) == []

    def test_cues_break_at_sentences_word_limit_and_width(self):
        words = word_timings("One more try. You said that yesterday and the day before that too", 0, 6)
        cues = build_cues(words, max_words=5, max_chars=32)
        assert cues[0].text == "One more try."
        assert all(len(c.text.split()) <= 5 and len(c.text) <= 32 for c in cues)
        assert " ".join(c.text for c in cues) == "One more try. You said that yesterday and the day before that too"
        assert all(a.end <= b.start + 1e-9 for a, b in zip(cues, cues[1:]))

    def test_a_single_long_word_still_gets_a_cue(self):
        cues = build_cues([TimedWord("Supercalifragilisticexpialidocious-indeed", 0, 1)], max_chars=10)
        assert len(cues) == 1

    def test_ass_file_escapes_override_characters_and_formats_times(self):
        ass = to_ass([Cue("a {b} \\c", 3661.237, 3662.5)], 1920, 1080, font="Test Font")
        assert "PlayResX: 1920" in ass and "PlayResY: 1080" in ass
        assert "Style: Caption,Test Font," in ass
        line = ass.strip().splitlines()[-1]
        assert line.startswith("Dialogue: 0,1:01:01.24,1:01:02.50,Caption,")
        assert "{" not in line and "\\" not in line

    def test_shorts_captions_sit_higher_than_long_form(self):
        def margin(ass):
            style = next(l for l in ass.splitlines() if l.startswith("Style:"))
            return int(style.split(",")[-2])
        assert margin(to_ass([], 1080, 1920)) / 1920 > margin(to_ass([], 1920, 1080)) / 1080


# ---------------------------------------------------------------------------
#  Avatar adapters
# ---------------------------------------------------------------------------
class Response:
    def __init__(self, status_code=200, payload=None, content=b""):
        self.status_code = status_code
        self._payload = payload
        self.content = content
        self.text = json.dumps(payload) if payload is not None else content.decode(errors="ignore")

    def json(self):
        return self._payload


class FakeHTTP:
    """Answers requests from a list of (method, url fragment, response) rules, in order of first match."""

    def __init__(self, rules):
        self.rules = rules
        self.requests = []

    def _answer(self, method, url, **kwargs):
        self.requests.append(SimpleNamespace(method=method, url=url, **kwargs))
        for rule_method, fragment, response in self.rules:
            if rule_method == method and fragment in url:
                return response.pop(0) if isinstance(response, list) else response
        raise AssertionError(f"unexpected {method} {url}")

    def post(self, url, **kwargs):
        return self._answer("POST", url, **kwargs)

    def get(self, url, **kwargs):
        return self._answer("GET", url, **kwargs)

    def put(self, url, **kwargs):
        return self._answer("PUT", url, **kwargs)


class TestAvatars:
    def test_still_avatar_is_free_and_returns_no_video(self):
        port = StillAvatar()
        assert port.animate(b"img", "image/png", b"wav", 2.5) == AvatarClip(None, 2.5)
        assert port.cost_usd(10) == 0 and port.missing_env() == [] and not port.lip_sync

    def test_cost_bills_at_least_the_minimum(self):
        port = KlingAvatar()
        assert port.cost_usd(1.0) == round(2.0 * 0.0562, 4)
        assert port.cost_usd(5.0) == round(5.0 * 0.0562, 4)
        assert port.cost_usd(0) == 0.0

    def test_kling_submits_polls_and_downloads(self, monkeypatch):
        monkeypatch.setenv("FAL_KEY", "k")
        http = FakeHTTP([
            ("POST", "queue.fal.run/fal-ai/kling-video/ai-avatar/v2/standard",
             Response(payload={"status_url": "https://q/status", "response_url": "https://q/result"})),
            ("GET", "https://q/status", [Response(payload={"status": "IN_QUEUE"}),
                                         Response(payload={"status": "IN_PROGRESS"}),
                                         Response(payload={"status": "COMPLETED"})]),
            ("GET", "https://q/result", Response(payload={"video": {"url": "https://cdn/v.mp4"}, "duration": 2.4})),
            ("GET", "https://cdn/v.mp4", Response(content=b"MP4")),
        ])
        sleeps = []
        clip = KlingAvatar(http=http, sleep=sleeps.append).animate(b"\xff\xd8img", "image/jpeg", b"RIFFwav", 2.4, "Maya speaks.")
        assert clip == AvatarClip(b"MP4", 2.4)
        submit = http.requests[0]
        assert submit.headers == {"Authorization": "Key k"}
        assert submit.json["image_url"].startswith("data:image/jpeg;base64,")
        assert submit.json["audio_url"].startswith("data:audio/wav;base64,")
        assert submit.json["prompt"] == "Maya speaks."
        assert len(sleeps) == 2

    def test_kling_errors_are_reported(self, monkeypatch):
        monkeypatch.setenv("FAL_KEY", "k")
        http = FakeHTTP([("POST", "queue.fal.run", Response(422, {"detail": "bad image"}))])
        with pytest.raises(AvatarError, match="422"):
            KlingAvatar(http=http, sleep=lambda s: None).animate(b"i", "image/png", b"w", 2)

    def test_kling_needs_its_key(self, monkeypatch):
        monkeypatch.delenv("FAL_KEY", raising=False)
        assert KlingAvatar.missing_env() == ["FAL_KEY"]
        with pytest.raises(AvatarError, match="FAL_KEY"):
            KlingAvatar(http=FakeHTTP([])).animate(b"i", "image/png", b"w", 2)

    def test_polling_gives_up_after_the_timeout(self, monkeypatch):
        monkeypatch.setenv("FAL_KEY", "k")
        monkeypatch.setenv("YT_AVATAR_TIMEOUT_S", "0")
        http = FakeHTTP([
            ("POST", "queue.fal.run", Response(payload={"status_url": "https://q/s", "response_url": "https://q/r"})),
            ("GET", "https://q/s", Response(payload={"status": "IN_PROGRESS"})),
        ])
        with pytest.raises(AvatarError, match="did not finish"):
            KlingAvatar(http=http, sleep=lambda s: None).animate(b"i", "image/png", b"w", 2)

    def test_infinitetalk_uploads_submits_polls_and_downloads(self, monkeypatch):
        monkeypatch.setenv("WAVESPEED_API_KEY", "w")
        monkeypatch.setenv("YT_INFINITETALK_RESOLUTION", "720p")
        uploads = [Response(payload={"data": {"download_url": "https://ws/frame.png"}}),
                   Response(payload={"data": {"download_url": "https://ws/line.wav"}})]
        http = FakeHTTP([
            ("POST", "/media/upload/binary", uploads),
            ("POST", "/wavespeed-ai/infinitetalk", Response(payload={"data": {"id": "p1"}})),
            ("GET", "/predictions/p1/result", [Response(payload={"data": {"status": "processing"}}),
                                               Response(payload={"data": {"status": "completed",
                                                                          "outputs": ["https://ws/out.mp4"]}})]),
            ("GET", "https://ws/out.mp4", Response(content=b"MP4")),
        ])
        port = InfiniteTalkAvatar(http=http, sleep=lambda s: None)
        assert port.usd_per_second == 0.06
        assert port.animate(b"png", "image/png", b"wav", 3.2, "Leo speaks.") == AvatarClip(b"MP4", 3.2)
        assert http.requests[0].files["file"][0] == "frame.png"
        assert http.requests[1].files["file"][2] == "audio/wav"
        submit = http.requests[2]
        assert submit.headers == {"Authorization": "Bearer w"}
        assert submit.json == {"image": "https://ws/frame.png", "audio": "https://ws/line.wav",
                               "resolution": "720p", "seed": -1, "prompt": "Leo speaks."}

    def test_infinitetalk_failure_is_reported(self, monkeypatch):
        monkeypatch.setenv("WAVESPEED_API_KEY", "w")
        http = FakeHTTP([
            ("POST", "/media/upload/binary", Response(payload={"data": {"download_url": "https://ws/x"}})),
            ("POST", "/wavespeed-ai/infinitetalk", Response(payload={"data": {"id": "p1"}})),
            ("GET", "/predictions/p1/result", Response(payload={"data": {"status": "failed", "error": "no face"}})),
        ])
        with pytest.raises(AvatarError, match="failed: no face"):
            InfiniteTalkAvatar(http=http, sleep=lambda s: None).animate(b"i", "image/jpeg", b"w", 3)

    def test_unknown_resolution_falls_back_to_480p(self, monkeypatch):
        monkeypatch.setenv("YT_INFINITETALK_RESOLUTION", "4k")
        assert InfiniteTalkAvatar().resolution == "480p" and InfiniteTalkAvatar().usd_per_second == 0.03

    def test_registry_defaults_to_still_and_rejects_unknown(self, monkeypatch):
        monkeypatch.setattr(AvatarRegistry, "_instances", {})
        monkeypatch.delenv("YT_AVATAR_PROVIDER", raising=False)
        assert isinstance(AvatarRegistry.get(), StillAvatar)
        monkeypatch.setenv("YT_AVATAR_PROVIDER", "puppets")
        with pytest.raises(ValueError, match="YT_AVATAR_PROVIDER"):
            AvatarRegistry.get()


# ---------------------------------------------------------------------------
#  ffmpeg building blocks
# ---------------------------------------------------------------------------
def png(width=320, height=180, color=(200, 80, 40)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, format="PNG")
    return out.getvalue()


SMALL = RenderSettings(160, 90, fps=10, preset="ultrafast")


@needs_ffmpeg
class TestMedia:
    def test_still_segment_has_the_exact_frame_count(self, tmp_path):
        image = tmp_path / "a.png"
        image.write_bytes(png())
        out = tmp_path / "a.mp4"
        still_segment(image, 15, out, SMALL, direction=1)
        info = media_info(out)
        assert info["has_video"] and not info["has_audio"]
        assert (info["width"], info["height"]) == (160, 90)
        assert info["duration_s"] == pytest.approx(1.5, abs=0.05)

    def test_clip_segment_fits_the_frame_and_holds_the_last_frame(self, tmp_path):
        image = tmp_path / "tall.png"
        image.write_bytes(png(90, 160))
        short_clip = tmp_path / "clip.mp4"
        still_segment(image, 5, short_clip, RenderSettings(90, 160, fps=10, preset="ultrafast"))
        out = tmp_path / "fitted.mp4"
        clip_segment(short_clip, 20, out, SMALL)
        info = media_info(out)
        assert (info["width"], info["height"]) == (160, 90)
        assert info["duration_s"] == pytest.approx(2.0, abs=0.05)

    def test_thumbnail_is_youtube_sized(self):
        for fmt, size in (("long_form", (1280, 720)), ("short", (720, 1280))):
            with Image.open(io.BytesIO(thumbnail(png(1344, 768), fmt))) as image:
                assert image.size == size and image.format == "JPEG"

    def test_settings_scale_keeps_even_dimensions(self):
        settings = RenderSettings.for_format("short", scale=0.333)
        assert settings.width % 2 == 0 and settings.height % 2 == 0 and settings.height > settings.width


# ---------------------------------------------------------------------------
#  Render service
# ---------------------------------------------------------------------------
class PaidAvatar(AvatarPort):
    """A talking-head provider that returns a real (tiny) MP4 and charges per second."""

    name = "paid"
    usd_per_second = 0.5
    min_seconds = 2.0
    max_seconds = 60.0

    def __init__(self, video=b"MP4"):
        self.calls = []
        self.video = video
        self.fail = False

    def animate(self, image, image_mime, audio_wav, duration_s, prompt=""):
        self.calls.append(SimpleNamespace(mime=image_mime, duration=duration_s, prompt=prompt,
                                          seconds=Audio.from_wav(audio_wav).duration_s))
        if self.fail:
            raise AvatarError("provider down")
        return AvatarClip(self.video, duration_s)


def approved_project(env, fmt="long_form"):
    project, maya, leo = ready_project(env, fmt)
    storyboard.generate_storyboard(env.db, project, env.storage, port=env.images)
    return storyboard.approve_storyboard(env.db, project)


def shots_of(env, project):
    return projects._shots(env.db, project)


class TestTimeline:
    def test_matches_the_voice_track_and_caps_talking(self, env, monkeypatch):
        monkeypatch.setenv("YT_MAX_TALKING_SECONDS", "3.5")
        project = approved_project(env)
        items = render.timeline(shots_of(env, project), PaidAvatar())
        # Fake voice: one second per word. Narration 4s, MAYA 3s, LEO 4s.
        assert [i.speech_s for i in items] == [4.0, 3.0, 4.0]
        assert [i.shot.shot_type for i in items] == ["narration", "dialogue", "dialogue"]
        assert [i.talk_s for i in items] == [0.0, 3.0, 3.5]
        assert [i.gap_s for i in items] == [projects.GAP_SPEAKER_CHANGE_S, projects.GAP_SPEAKER_CHANGE_S,
                                            render.END_TAIL_S]
        assert items[1].start == pytest.approx(4.0 + projects.GAP_SPEAKER_CHANGE_S)
        track = Audio.from_wav(env.storage.get(project.audio_key)).duration_s
        assert sum(i.length_s for i in items) == pytest.approx(track + render.END_TAIL_S, abs=0.01)

    def test_narrator_never_talks_on_screen(self, env):
        project = approved_project(env)
        for shot in shots_of(env, project):
            shot.shot_type = "dialogue"
        items = render.timeline(shots_of(env, project), PaidAvatar())
        assert items[0].shot.speaker_label == "NARRATOR" and items[0].talk_s == 0


class TestEstimateAndProblems:
    def test_estimate_counts_new_clips_and_budget(self, env, monkeypatch):
        monkeypatch.setenv("YT_AVATAR_BUDGET_USD", "2")
        project = approved_project(env)
        est = render.estimate(env.db, project, PaidAvatar())
        assert est["talking_shots"] == 2 and est["new_clips"] == 2
        assert est["talking_seconds"] == 7.0
        assert est["avatar_cost_usd"] == 3.5 and est["over_budget"] is True
        assert est["video_seconds"] == pytest.approx(4 + 3 + 4 + 2 * projects.GAP_SPEAKER_CHANGE_S + render.END_TAIL_S,
                                                     abs=0.1)
        assert render.estimate(env.db, project, StillAvatar())["avatar_cost_usd"] == 0

    def test_problems_before_approval_and_for_unconfigured_providers(self, env, monkeypatch):
        project, _, _ = ready_project(env)
        problems = render.render_problems(env.db, project, StillAvatar())
        assert "Approve the storyboard first" in problems
        assert "Every shot needs a storyboard image" in problems
        monkeypatch.delenv("FAL_KEY", raising=False)
        assert any("kling_standard is not configured" in p for p in render.render_problems(env.db, project, KlingAvatar()))

    def test_render_refuses_over_budget_without_confirmation(self, env, monkeypatch):
        monkeypatch.setenv("YT_AVATAR_BUDGET_USD", "1")
        project = approved_project(env)
        port = PaidAvatar()
        with pytest.raises(projects.ProjectError, match="over the \\$1.00 budget"):
            render.render_project(env.db, project, env.storage, port)
        assert port.calls == []


class TestAnimate:
    def test_makes_clips_records_costs_and_reuses_them(self, env):
        project = approved_project(env)
        port = PaidAvatar()
        render.animate_shots(env.db, project, env.storage, port)
        assert [round(c.duration, 1) for c in port.calls] == [3.0, 4.0]
        assert port.calls[0].mime == "image/png" and port.calls[0].prompt.startswith("Maya speaks naturally")
        shots = shots_of(env, project)
        assert shots[0].clip_key is None
        assert all(s.clip_provider == "paid" and env.storage.get(s.clip_key) == b"MP4" for s in shots[1:])
        costs = env.db.query(YTCost).filter_by(project_id=project.id, stage="avatar").all()
        assert sorted(c.cost_usd for c in costs) == [1.5, 2.0]

        render.animate_shots(env.db, project, env.storage, port)
        assert len(port.calls) == 2, "current clips are reused"
        assert render.estimate(env.db, project, port)["new_clips"] == 0

    def test_short_lines_are_padded_to_the_provider_minimum(self, env, monkeypatch):
        monkeypatch.setenv("YT_MAX_TALKING_SECONDS", "1")
        project = approved_project(env)
        port = PaidAvatar()
        render.animate_shots(env.db, project, env.storage, port)
        assert all(c.seconds == pytest.approx(2.0, abs=0.01) for c in port.calls)
        assert all(s.clip_duration_s == 1.0 for s in shots_of(env, project)[1:])

    def test_redrawing_or_revoicing_makes_the_clip_stale(self, env):
        project = approved_project(env)
        port = PaidAvatar()
        render.animate_shots(env.db, project, env.storage, port)
        maya = shots_of(env, project)[1]
        storyboard.generate_storyboard(env.db, project, env.storage, port=env.images, shot_ids=[maya.id])
        assert render.estimate(env.db, project, port)["new_clips"] == 1
        render.animate_shots(env.db, project, env.storage, port)
        assert len(port.calls) == 3

        projects.voice_project(env.db, project, env.storage, force=True)
        assert render.estimate(env.db, project, port)["new_clips"] == 2

    def test_changing_provider_or_cap_remakes_clips(self, env, monkeypatch):
        project = approved_project(env)
        render.animate_shots(env.db, project, env.storage, PaidAvatar())
        assert render.estimate(env.db, project, StillAvatar())["new_clips"] == 2
        monkeypatch.setenv("YT_MAX_TALKING_SECONDS", "2")
        assert render.estimate(env.db, project, PaidAvatar())["new_clips"] == 2

    def test_a_failure_keeps_clips_already_paid_for(self, env):
        project = approved_project(env)
        port = PaidAvatar()

        def animate_once(*args, **kwargs):
            if port.calls:
                port.fail = True
            return PaidAvatar.animate(port, *args, **kwargs)

        port.animate = animate_once
        with pytest.raises(projects.ProjectError, match="Animating shot 3 failed: provider down"):
            render.animate_shots(env.db, project, env.storage, port)
        env.db.rollback()
        shots = shots_of(env, project)
        assert shots[1].clip_provider == "paid" and shots[2].clip_provider is None


def tiny_clip(tmp_path) -> bytes:
    image = tmp_path / "face.png"
    image.write_bytes(png(160, 90, (30, 160, 90)))
    out = tmp_path / "talk.mp4"
    still_segment(image, 10, out, SMALL)
    return out.read_bytes()


@needs_ffmpeg
class TestRenderProject:
    def test_renders_a_synced_video_and_marks_the_project_rendered(self, env, tmp_path, monkeypatch):
        monkeypatch.setenv("YT_RENDER_FPS", "10")
        monkeypatch.setenv("YT_RENDER_PRESET", "ultrafast")
        project = approved_project(env)
        port = PaidAvatar(video=tiny_clip(tmp_path))
        result = render.render_project(env.db, project, env.storage, port, confirm_over_budget=True, scale=0.1)

        assert project.status == "rendered"
        assert result.avatar_provider == "paid" and result.size_bytes > 0
        track = Audio.from_wav(env.storage.get(project.audio_key)).duration_s
        assert result.duration_s == pytest.approx(track + render.END_TAIL_S, abs=0.15)

        video = tmp_path / "out.mp4"
        video.write_bytes(env.storage.get(result.video_key))
        info = media_info(video)
        assert info["has_video"] and info["has_audio"] and (info["width"], info["height"]) == (192, 108)
        with Image.open(io.BytesIO(env.storage.get(result.thumbnail_key))) as thumb:
            assert thumb.size == (1280, 720)

        summary = projects.serialize_project(env.db, project)["video"]
        assert summary["renders"][0]["current"] is True and summary["renders"][0]["id"] == result.id

        # Any change to the storyboard makes the video out of date.
        projects.clear_approval(project)
        projects.settle_status(env.db, project)
        assert project.status == "storyboard_ready"
        project = storyboard.approve_storyboard(env.db, project)
        assert project.status == "storyboard_approved"

    def test_keeps_only_the_latest_renders(self, env, monkeypatch):
        monkeypatch.setenv("YT_RENDER_FPS", "10")
        monkeypatch.setenv("YT_RENDER_PRESET", "ultrafast")
        monkeypatch.setenv("YT_KEEP_RENDERS", "2")
        project = approved_project(env)
        made = [render.render_project(env.db, project, env.storage, StillAvatar(), scale=0.1) for _ in range(3)]
        kept = env.db.query(YTRender).filter_by(project_id=project.id).order_by(YTRender.id).all()
        assert [r.id for r in kept] == [m.id for m in made[1:]]
        assert not env.storage.exists(made[0].video_key) and not env.storage.exists(made[0].thumbnail_key)
        assert render.latest_render(env.db, project).id == made[2].id

    def test_deleting_the_project_removes_clips_and_renders(self, env, tmp_path, monkeypatch):
        monkeypatch.setenv("YT_RENDER_FPS", "10")
        monkeypatch.setenv("YT_RENDER_PRESET", "ultrafast")
        project = approved_project(env)
        result = render.render_project(env.db, project, env.storage, PaidAvatar(video=tiny_clip(tmp_path)),
                                       confirm_over_budget=True, scale=0.1)
        clip_keys = [s.clip_key for s in shots_of(env, project) if s.clip_key]
        assert clip_keys
        projects.delete_project(env.db, project, env.storage)
        assert not any(env.storage.exists(k) for k in clip_keys + [result.video_key, result.thumbnail_key])


def test_render_is_current_compares_approval_time():
    from datetime import datetime, timedelta, timezone
    approved = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
    project = SimpleNamespace(storyboard_approved_at=approved.replace(tzinfo=None), end_card=None)
    assert render.render_is_current(SimpleNamespace(created_at=approved + timedelta(seconds=1), end_card=None), project)
    assert not render.render_is_current(SimpleNamespace(created_at=approved - timedelta(seconds=1), end_card=None), project)
    assert not render.render_is_current(None, project)
