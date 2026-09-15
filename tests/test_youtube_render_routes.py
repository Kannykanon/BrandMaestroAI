"""Phase 3 routes: render estimate, start, and the video, thumbnail and clip files."""
from types import SimpleNamespace

import pytest

import youtube.projects as projects
import youtube.render as render
import youtube.storyboard as storyboard
import youtube.tasks as tasks
from tests.test_youtube_projects import FakeLLM
from tests.test_youtube_render import PaidAvatar, needs_ffmpeg
from tests.test_youtube_storyboard_routes import api, upload  # noqa: F401  (fixture)
from youtube.avatar import AvatarRegistry


@pytest.fixture
def paid(api, monkeypatch):
    port = PaidAvatar()
    monkeypatch.setattr(AvatarRegistry, "_instances", {"paid": port})
    monkeypatch.setitem(AvatarRegistry.PROVIDERS, "paid", PaidAvatar)
    monkeypatch.setenv("YT_AVATAR_PROVIDER", "paid")

    def delay(*args, **kwargs):
        api.queued.append(("render_project", args, kwargs))
        return SimpleNamespace(id="task")

    monkeypatch.setattr(tasks, "render_project", SimpleNamespace(delay=delay))
    return port


def approved(api, fmt="long_form"):
    c = api.client
    pid = c.post("/youtube/projects", json={"generation_id": "gen-1", "format": fmt}).json()["id"]
    ids = {}
    for label, name, voice in (("NARRATOR", "Narrator", "deep"), ("MAYA", "Maya", "warm"), ("LEO", "Leo", "bright")):
        ids[label] = c.post("/youtube/characters", json={"name": name, "voice_id": voice, "voice_provider": "fake"}).json()["id"]
        if label != "NARRATOR":
            c.post(f"/youtube/characters/{ids[label]}/rights", json={"confirmed": True})
            upload(c, ids[label])
            with api.Session() as db:
                character = projects.get_character(db, "biz", ids[label])
                storyboard.start_sheet(db, character)
                storyboard.generate_sheet(db, character, api.storage, port=api.images)
            c.post(f"/youtube/characters/{ids[label]}/sheet/approve")
    with api.Session() as db:
        projects.plan_project(db, projects.get_project(db, "biz", pid), llm=FakeLLM())
    c.put(f"/youtube/projects/{pid}/cast", json={"assignments": ids})
    with api.Session() as db:
        p = projects.get_project(db, "biz", pid)
        projects.voice_project(db, p, api.storage)
        storyboard.generate_storyboard(db, p, api.storage, port=api.images)
    return pid


def test_estimate_lists_problems_until_approved(api, paid):
    pid = approved(api)
    body = api.client.get(f"/youtube/projects/{pid}/render/estimate").json()
    assert body["avatar_provider"] == "paid" and body["talking_shots"] == 2
    assert body["problems"] == ["Approve the storyboard first"]

    refused = api.client.post(f"/youtube/projects/{pid}/render", json={})
    assert refused.status_code == 400 and "Approve the storyboard first" in refused.json()["detail"]
    assert not any(name == "render_project" for name, _, _ in api.queued)


def test_over_budget_needs_confirmation_then_queues(api, paid, monkeypatch):
    monkeypatch.setenv("YT_AVATAR_BUDGET_USD", "1")
    pid = approved(api)
    c = api.client
    assert c.post(f"/youtube/projects/{pid}/storyboard/approve").json()["status"] == "storyboard_approved"

    refused = c.post(f"/youtube/projects/{pid}/render", json={})
    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert "over the $1.00 budget" in detail["message"] and detail["estimate"]["avatar_cost_usd"] == 3.5

    started = c.post(f"/youtube/projects/{pid}/render", json={"confirm_over_budget": True})
    assert started.status_code == 202 and started.json()["status"] == "rendering"
    assert api.queued[-1] == ("render_project", (pid, "biz"), {"confirm_over_budget": True})
    assert c.get(f"/youtube/projects/{pid}").json()["status"] == "rendering"
    assert c.post(f"/youtube/projects/{pid}/render", json={"confirm_over_budget": True}).status_code == 409


def test_status_reports_the_avatar_provider(api, paid):
    body = api.client.get("/youtube/status").json()["avatar"]
    assert body["provider"] == "paid" and body["configured"] and body["usd_per_second"] == 0.5


@needs_ffmpeg
def test_rendered_video_thumbnail_and_clip_are_served(api, paid, tmp_path, monkeypatch):
    from tests.test_youtube_render import tiny_clip

    monkeypatch.setenv("YT_RENDER_FPS", "10")
    monkeypatch.setenv("YT_RENDER_PRESET", "ultrafast")
    paid.video = tiny_clip(tmp_path)
    pid = approved(api)
    c = api.client
    c.post(f"/youtube/projects/{pid}/storyboard/approve")
    with api.Session() as db:
        result = render.render_project(db, projects.get_project(db, "biz", pid), api.storage,
                                       confirm_over_budget=True, scale=0.1)
        rid = result.id

    detail = c.get(f"/youtube/projects/{pid}").json()
    assert detail["status"] == "rendered"
    assert detail["video"]["renders"][0]["id"] == rid and detail["video"]["renders"][0]["current"]

    video = c.get(f"/youtube/projects/{pid}/renders/{rid}/video")
    assert video.status_code == 200 and video.headers["content-type"] == "video/mp4"
    assert video.headers["content-disposition"].startswith("attachment")
    assert c.get(f"/youtube/projects/{pid}/renders/{rid}/video", params={"as_link": "true"}).json() == {"url": None}

    thumb = c.get(f"/youtube/projects/{pid}/renders/{rid}/thumbnail")
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"

    talking = next(s for s in detail["shots"] if s["speaker"] == "MAYA")
    assert c.get(f"/youtube/projects/{pid}/shots/{talking['id']}/clip").status_code == 200
    narration = detail["shots"][0]
    assert c.get(f"/youtube/projects/{pid}/shots/{narration['id']}/clip").status_code == 404

    assert c.get(f"/youtube/projects/{pid}/renders/{rid + 99}/video").status_code == 404
    api.user.business_id = "someone-else"
    assert c.get(f"/youtube/projects/{pid}/renders/{rid}/video").status_code == 404
