"""Phase 4 service and routes: channel connection, private upload, publish and schedule, with a fake publisher."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import youtube.projects as projects
import youtube.publishing as publishing
import youtube.storyboard as storyboard
from tests.test_youtube_render import approved_project
from tests.test_youtube_storyboard import env  # noqa: F401  (fixture)
from youtube import quota
from youtube.models import YTRender, YTUpload
from youtube.publisher import (
    AuthError,
    ChannelInfo,
    PublisherPort,
    PublisherSingleton,
    PublishError,
    QuotaExhausted,
    VideoStatus,
)
from youtube.token_crypto import decrypt

NOW = datetime(2026, 9, 15, 18, 0, tzinfo=timezone.utc)


class FakePublisher(PublisherPort):
    name = "fake_youtube"

    def __init__(self):
        self.calls = []
        self.fail_upload = None
        self.thumbnail_error = None
        self.privacy_after_update = None  # None: YouTube applies the change
        self.youtube_privacy = "private"
        self.youtube_publish_at = None

    def authorization_url(self, state, redirect_uri):
        return f"https://accounts.example/auth?state={state}&redirect_uri={redirect_uri}"

    def exchange_code(self, code, redirect_uri):
        self.calls.append(("exchange", code, redirect_uri))
        if code == "bad":
            raise AuthError("invalid_grant")
        return f"refresh-for-{code}"

    def channel(self, refresh_token):
        return ChannelInfo("UC123", "Bakery Tales")

    def upload_private(self, refresh_token, video, metadata, session_url=None, on_session=lambda u: None,
                       on_progress=lambda s, t: None):
        self.calls.append(("upload", refresh_token, len(video), metadata, session_url))
        if not session_url:
            on_session("https://upload/session")
        if self.fail_upload:
            raise self.fail_upload
        on_progress(len(video) // 2, len(video))
        on_progress(len(video), len(video))
        return "vid-1"

    def set_thumbnail(self, refresh_token, video_id, jpeg):
        self.calls.append(("thumbnail", video_id, len(jpeg)))
        if self.thumbnail_error:
            raise PublishError(self.thumbnail_error)

    def set_privacy(self, refresh_token, video_id, metadata, privacy, publish_at=None):
        self.calls.append(("privacy", video_id, privacy, publish_at, metadata.made_for_kids))
        if self.privacy_after_update is None:
            self.youtube_privacy = privacy
            self.youtube_publish_at = publish_at.isoformat() if publish_at else None

    def video_status(self, refresh_token, video_id):
        self.calls.append(("status", video_id))
        return VideoStatus(video_id, self.privacy_after_update or self.youtube_privacy, "processed",
                           self.youtube_publish_at)

    def revoke(self, refresh_token):
        self.calls.append(("revoke", refresh_token))


@pytest.fixture
def pub(env, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.delenv("YT_TOKEN_KEY", raising=False)
    fake = FakePublisher()
    monkeypatch.setattr(PublisherSingleton, "_instance", fake)
    return fake


def connected(env, pub):
    return publishing.connect_channel(env.db, "biz", "code-1", "https://app/youtube/channel/callback", pub)


def rendered_project(env):
    """An approved project with a current render (files faked; no ffmpeg needed)."""
    project = approved_project(env)
    video_key, thumb_key = f"businesses/biz/projects/{project.id}/renders/render-a.mp4", \
        f"businesses/biz/projects/{project.id}/renders/thumbnail-a.jpg"
    env.storage.put(video_key, b"MP4" * 1000, content_type="video/mp4")
    env.storage.put(thumb_key, b"JPEG", content_type="image/jpeg")
    render = YTRender(project_id=project.id, format=project.format, video_key=video_key, thumbnail_key=thumb_key,
                      status="completed", duration_s=12.0, size_bytes=3000, avatar_provider="still",
                      created_at=datetime.now(timezone.utc) + timedelta(seconds=1))
    env.db.add(render)
    projects.settle_status(env.db, project)
    env.db.commit()
    assert project.status == "rendered"
    return project, render


def with_metadata(env, project):
    from youtube import metadata
    return metadata.update_metadata(env.db, project, title="One more try", description="A bakery story.",
                                    tags=["bakery"], made_for_kids=False)


def uploaded(env, pub):
    connected(env, pub)
    project, render = rendered_project(env)
    with_metadata(env, project)
    upload = publishing.queue_upload(env.db, project, reviewed=True, publisher=pub)
    assert publishing.claim_upload(env.db, upload.id, NOW)
    outcome = publishing.run_upload(env.db, upload, env.storage, pub, NOW)
    assert outcome.status == "uploaded"
    return project, render, upload


# ---------------------------------------------------------------------------
#  Channel
# ---------------------------------------------------------------------------
class TestChannel:
    def test_state_is_bound_to_the_business_and_the_browser(self, pub):
        state, nonce = publishing.make_state("biz")
        assert publishing.read_state(state, nonce) == "biz"
        with pytest.raises(ProjectErrorType, match="same browser"):
            publishing.read_state(state, "someone-elses-nonce")
        with pytest.raises(ProjectErrorType, match="same browser"):
            publishing.read_state(state, None)
        with pytest.raises(ProjectErrorType, match="expired or is invalid"):
            publishing.read_state(state + "x", nonce)

    def test_expired_state_is_refused(self, pub, monkeypatch):
        monkeypatch.setattr(publishing, "STATE_TTL", timedelta(seconds=-1))
        state, nonce = publishing.make_state("biz")
        with pytest.raises(ProjectErrorType, match="expired"):
            publishing.read_state(state, nonce)

    def test_connect_stores_the_token_encrypted_and_disconnect_revokes(self, env, pub, monkeypatch):
        monkeypatch.delenv("YT_OAUTH_REDIRECT_URI", raising=False)
        channel = connected(env, pub)
        assert channel.channel_id == "UC123" and channel.channel_title == "Bakery Tales"
        assert channel.refresh_token_encrypted != "refresh-for-code-1"
        assert decrypt(channel.refresh_token_encrypted) == "refresh-for-code-1"
        summary = publishing.serialize_channel(env.db, "biz", "http://localhost:8000", pub)
        assert summary["connected"] and summary["channel_url"] == "https://www.youtube.com/channel/UC123"
        assert summary["redirect_uri"] == "http://localhost:8000/youtube/channel/callback"

        publishing.disconnect_channel(env.db, "biz", pub)
        assert ("revoke", "refresh-for-code-1") in pub.calls
        assert publishing.get_channel(env.db, "biz") is None

    def test_redirect_uri_prefers_explicit_setting(self, monkeypatch):
        monkeypatch.delenv("YT_OAUTH_REDIRECT_URI", raising=False)
        assert publishing.redirect_uri("http://internal/") == "http://internal/youtube/channel/callback"
        monkeypatch.setenv("YT_OAUTH_REDIRECT_URI", "https://brandmaestro.example/youtube/channel/callback")
        assert publishing.redirect_uri("http://internal") == "https://brandmaestro.example/youtube/channel/callback"


ProjectErrorType = projects.ProjectError


# ---------------------------------------------------------------------------
#  Upload
# ---------------------------------------------------------------------------
class TestUpload:
    def test_problems_until_channel_render_and_metadata_are_ready(self, env, pub, monkeypatch):
        project = approved_project(env)
        problems = publishing.upload_problems(env.db, project, pub)
        assert "Connect a YouTube channel first" in problems
        assert "Render the video from the approved storyboard first" in problems
        assert "Give the video a title" in problems and "Say whether the video is made for kids" in problems

    def test_upload_needs_a_person_to_confirm_they_watched_it(self, env, pub):
        connected(env, pub)
        project, _ = rendered_project(env)
        with_metadata(env, project)
        with pytest.raises(ProjectErrorType, match="Watch the rendered video"):
            publishing.queue_upload(env.db, project, reviewed=False, publisher=pub)
        upload = publishing.queue_upload(env.db, project, reviewed=True, publisher=pub)
        assert upload.status == "queued" and upload.title == "One more try"
        assert project.status == "uploading"
        with pytest.raises(ProjectErrorType, match="already being uploaded"):
            publishing.queue_upload(env.db, project, reviewed=True, publisher=pub)

    def test_uploads_private_sets_thumbnail_and_status(self, env, pub):
        project, render, upload = uploaded(env, pub)
        name, token, size, meta, session = pub.calls[1]
        assert (name, token, size, session) == ("upload", "refresh-for-code-1", 3000, None)
        assert meta.title == "One more try" and meta.synthetic_media is True and meta.made_for_kids is False
        assert ("thumbnail", "vid-1", 4) in pub.calls
        assert upload.status == "uploaded" and upload.youtube_video_id == "vid-1" and upload.progress == 1.0
        assert upload.upload_url is None and upload.privacy == "private"
        assert project.status == "uploaded_private"
        assert quota.usage(env.db, NOW)["uploads"]["used"] == 1 and quota.usage(env.db, NOW)["units"]["used"] == 50
        data = publishing.serialize_upload(upload)
        assert data["watch_url"] == "https://youtu.be/vid-1" and data["studio_url"].endswith("/vid-1/edit")
        assert "This render is already on YouTube" in publishing.upload_problems(env.db, project, pub)

    def test_thumbnail_failure_is_only_a_note(self, env, pub):
        pub.thumbnail_error = "channel not verified"
        project, _, upload = uploaded(env, pub)
        assert upload.status == "uploaded" and "channel not verified" in upload.thumbnail_error

    def test_waits_for_quota_and_is_not_claimed_before_the_reset(self, env, pub, monkeypatch):
        monkeypatch.setenv("YT_DAILY_UPLOADS", "0")
        connected(env, pub)
        project, _ = rendered_project(env)
        with_metadata(env, project)
        upload = publishing.queue_upload(env.db, project, reviewed=True, publisher=pub)
        assert publishing.claim_upload(env.db, upload.id, NOW)
        outcome = publishing.run_upload(env.db, upload, env.storage, pub, NOW)
        assert outcome.status == "waiting_quota" and outcome.retry_at == quota.next_reset(NOW)
        assert upload.status == "waiting_quota" and project.status == "uploading"
        assert not any(c[0] == "upload" for c in pub.calls)

        assert not publishing.claim_upload(env.db, upload.id, NOW + timedelta(hours=1))
        monkeypatch.setenv("YT_DAILY_UPLOADS", "100")
        assert publishing.claim_upload(env.db, upload.id, outcome.retry_at)
        env.db.refresh(upload)
        assert publishing.run_upload(env.db, upload, env.storage, pub, outcome.retry_at).status == "uploaded"

    def test_youtube_reporting_quota_used_up_also_waits(self, env, pub):
        connected(env, pub)
        project, _ = rendered_project(env)
        with_metadata(env, project)
        upload = publishing.queue_upload(env.db, project, reviewed=True, publisher=pub)
        publishing.claim_upload(env.db, upload.id, NOW)
        pub.fail_upload = QuotaExhausted("quotaExceeded")
        outcome = publishing.run_upload(env.db, upload, env.storage, pub, NOW)
        assert outcome.status == "waiting_quota"
        assert upload.upload_url == "https://upload/session", "the session is kept to resume"
        with pytest.raises(quota.QuotaExceeded):
            quota.reserve(env.db, "upload", NOW)

    def test_a_resumed_upload_continues_its_session_without_new_quota(self, env, pub):
        connected(env, pub)
        project, _ = rendered_project(env)
        with_metadata(env, project)
        upload = publishing.queue_upload(env.db, project, reviewed=True, publisher=pub)
        upload.upload_url = "https://upload/earlier"
        env.db.commit()
        publishing.claim_upload(env.db, upload.id, NOW)
        publishing.run_upload(env.db, upload, env.storage, pub, NOW)
        assert pub.calls[1][4] == "https://upload/earlier"
        assert quota.usage(env.db, NOW)["uploads"]["used"] == 0

    def test_refused_sign_in_fails_the_upload_and_marks_the_channel(self, env, pub):
        connected(env, pub)
        project, _ = rendered_project(env)
        with_metadata(env, project)
        upload = publishing.queue_upload(env.db, project, reviewed=True, publisher=pub)
        publishing.claim_upload(env.db, upload.id, NOW)
        pub.fail_upload = AuthError("invalid_grant")
        assert publishing.run_upload(env.db, upload, env.storage, pub, NOW).status == "failed"
        assert upload.error == "invalid_grant" and project.status == "rendered"
        assert publishing.get_channel(env.db, "biz").token_error == "invalid_grant"
        assert "Connect the YouTube channel again" in publishing.upload_problems(env.db, project, pub)

    def test_claims_are_exclusive_until_stale(self, env, pub):
        connected(env, pub)
        project, _ = rendered_project(env)
        with_metadata(env, project)
        upload = publishing.queue_upload(env.db, project, reviewed=True, publisher=pub)
        assert publishing.claim_upload(env.db, upload.id, NOW)
        assert not publishing.claim_upload(env.db, upload.id, NOW + timedelta(minutes=30))
        assert publishing.claim_upload(env.db, upload.id, NOW + publishing.STALE_CLAIM + timedelta(minutes=1))

    def test_cancel_and_retry(self, env, pub):
        connected(env, pub)
        project, _ = rendered_project(env)
        with_metadata(env, project)
        upload = publishing.queue_upload(env.db, project, reviewed=True, publisher=pub)
        publishing.cancel_upload(env.db, upload)
        assert upload.status == "cancelled" and project.status == "rendered"
        with pytest.raises(ProjectErrorType):
            publishing.retry_upload(env.db, upload)

        upload = publishing.queue_upload(env.db, project, reviewed=True, publisher=pub)
        publishing.claim_upload(env.db, upload.id, NOW)
        publishing.fail_upload(env.db, upload, "network down")
        assert project.status == "rendered"
        publishing.retry_upload(env.db, upload)
        assert upload.status == "queued" and upload.error is None and project.status == "uploading"
        assert publishing.claim_upload(env.db, upload.id, NOW)
        env.db.refresh(upload)
        with pytest.raises(ProjectErrorType, match="has not started"):
            publishing.cancel_upload(env.db, upload)

    def test_uploaded_renders_survive_render_pruning(self, env, pub, monkeypatch):
        from youtube import render as render_module
        monkeypatch.setenv("YT_KEEP_RENDERS", "1")
        project, first, upload = uploaded(env, pub)
        env.db.add(YTRender(project_id=project.id, format=project.format, status="completed",
                            created_at=datetime.now(timezone.utc)))
        env.db.flush()
        render_module._keep_latest(env.db, project, env.storage)
        env.db.commit()
        assert env.db.get(YTRender, first.id) is not None and env.db.get(YTUpload, upload.id) is not None


# ---------------------------------------------------------------------------
#  Publish and refresh
# ---------------------------------------------------------------------------
class TestPublish:
    def test_publish_now_makes_it_public(self, env, pub):
        project, _, upload = uploaded(env, pub)
        publishing.publish_upload(env.db, upload, publisher=pub, now=NOW)
        assert ("privacy", "vid-1", "public", None, False) in pub.calls
        assert upload.status == "published" and upload.privacy == "public" and upload.published_at == NOW
        assert project.status == "published"
        with pytest.raises(ProjectErrorType, match="not yet public"):
            publishing.publish_upload(env.db, upload, publisher=pub, now=NOW)

    def test_a_video_youtube_keeps_private_is_reported_as_locked(self, env, pub):
        project, _, upload = uploaded(env, pub)
        pub.privacy_after_update = "private"
        with pytest.raises(ProjectErrorType, match="compliance audit"):
            publishing.publish_upload(env.db, upload, publisher=pub, now=NOW)
        assert upload.status == "uploaded" and "locked private" in upload.error
        assert project.status == "uploaded_private"

    def test_schedule_then_refresh_sees_it_go_public(self, env, pub):
        project, _, upload = uploaded(env, pub)
        with pytest.raises(ProjectErrorType, match="5 minutes ahead"):
            publishing.publish_upload(env.db, upload, publish_at=NOW + timedelta(minutes=1), publisher=pub, now=NOW)
        when = NOW + timedelta(days=1)
        publishing.publish_upload(env.db, upload, publish_at=when, publisher=pub, now=NOW)
        assert upload.status == "scheduled" and upload.privacy == "private"
        assert publishing._aware(upload.publish_at) == when and project.status == "scheduled"

        pub.youtube_privacy = "public"
        publishing.refresh_upload(env.db, upload, publisher=pub, now=when + timedelta(hours=1))
        assert upload.status == "published" and project.status == "published"

    def test_refresh_reports_rejection(self, env, pub):
        project, _, upload = uploaded(env, pub)

        def rejected(token, video_id):
            return VideoStatus(video_id, "private", "rejected", rejection_reason="duplicate")
        pub.video_status = rejected
        publishing.refresh_upload(env.db, upload, publisher=pub, now=NOW)
        assert upload.error == "YouTube reports the video rejected (duplicate)" and upload.youtube_status == "rejected"

    def test_publishing_does_not_overwrite_a_running_render(self, env, pub):
        project, _, upload = uploaded(env, pub)
        project.status = "rendering"
        env.db.commit()
        publishing.publish_upload(env.db, upload, publisher=pub, now=NOW)
        assert project.status == "rendering"

    def test_changing_the_storyboard_after_upload_needs_a_new_render(self, env, pub):
        project, _, upload = uploaded(env, pub)
        shot = projects._shots(env.db, project)[1]
        storyboard.update_shot(env.db, project, shot.id, shot_type="two_character")
        assert project.status == "storyboard_ready"
        assert publishing.serialize_upload(upload)["status"] == "uploaded"


# ---------------------------------------------------------------------------
#  Routes
# ---------------------------------------------------------------------------
from tests.test_youtube_render_routes import approved as approved_via_api  # noqa: E402
from tests.test_youtube_storyboard_routes import api, upload as upload_face  # noqa: E402,F401


@pytest.fixture
def routes(api, monkeypatch):
    import youtube.tasks as tasks

    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.delenv("YT_TOKEN_KEY", raising=False)
    monkeypatch.delenv("YT_OAUTH_REDIRECT_URI", raising=False)
    monkeypatch.setenv("YT_GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("YT_GOOGLE_CLIENT_SECRET", "secret")
    fake = FakePublisher()
    monkeypatch.setattr(PublisherSingleton, "_instance", fake)

    def delay(*args, **kwargs):
        api.queued.append(("upload_video", args, kwargs))
        return SimpleNamespace(id="task")

    monkeypatch.setattr(tasks, "upload_video", SimpleNamespace(delay=delay))
    return SimpleNamespace(api=api, publisher=fake)


def test_channel_connect_callback_and_disconnect(routes):
    c = routes.api.client
    status = c.get("/youtube/channel").json()
    assert status["configured"] and not status["connected"]
    assert status["redirect_uri"] == "http://testserver/youtube/channel/callback"

    started = c.post("/youtube/channel/connect")
    assert started.status_code == 200 and "yt_oauth_nonce" in started.cookies
    state = started.json()["url"].split("state=")[1].split("&")[0]

    done = c.get("/youtube/channel/callback", params={"state": state, "code": "abc"}, follow_redirects=False)
    assert done.status_code == 303 and done.headers["location"] == "/?yt_channel=connected"
    assert routes.publisher.calls[-1] == ("exchange", "abc", "http://testserver/youtube/channel/callback")
    assert c.get("/youtube/channel").json()["channel_title"] == "Bakery Tales"

    assert c.delete("/youtube/channel").status_code == 204
    assert not c.get("/youtube/channel").json()["connected"]


def test_callback_refuses_another_browser_and_reports_google_errors(routes):
    c = routes.api.client
    state = c.post("/youtube/channel/connect").json()["url"].split("state=")[1].split("&")[0]
    c.cookies.clear()
    refused = c.get("/youtube/channel/callback", params={"state": state, "code": "abc"}, follow_redirects=False)
    assert refused.status_code == 303 and "yt_channel=error" in refused.headers["location"]
    assert "same+browser" in refused.headers["location"]
    cancelled = c.get("/youtube/channel/callback", params={"error": "access_denied"}, follow_redirects=False)
    assert "cancelled" in cancelled.headers["location"]
    with routes.api.Session() as db:
        assert publishing.get_channel(db, "biz") is None


def test_connect_is_refused_when_sign_in_is_not_configured(routes, monkeypatch):
    monkeypatch.delenv("YT_GOOGLE_CLIENT_SECRET")
    monkeypatch.setattr(FakePublisher, "required_env", ("YT_GOOGLE_CLIENT_ID", "YT_GOOGLE_CLIENT_SECRET"))
    assert routes.api.client.post("/youtube/channel/connect").status_code == 400


def test_metadata_upload_and_publish_routes(routes, monkeypatch):
    from youtube import metadata

    api, c = routes.api, routes.api.client
    pid = approved_via_api(api)
    c.post(f"/youtube/projects/{pid}/storyboard/approve")
    with api.Session() as db:
        project = projects.get_project(db, "biz", pid)
        db.add(YTRender(project_id=pid, format=project.format, video_key="businesses/biz/v.mp4",
                        thumbnail_key=None, status="completed", created_at=datetime.now(timezone.utc) + timedelta(seconds=1)))
        db.commit()
        publishing.connect_channel(db, "biz", "code", "cb", routes.publisher)
    api.storage.put("businesses/biz/v.mp4", b"MP4", content_type="video/mp4")

    monkeypatch.setattr(metadata, "_brand_context", lambda business_id: ("Crumb", ""))

    class Writer:
        def invoke(self, prompt):
            return SimpleNamespace(content='{"title": "Drafted title", "description": "Drafted.", "tags": ["x"]}')

    import model
    monkeypatch.setattr(model.LLMSingleton, "get", classmethod(lambda cls, mode=None: Writer()))
    drafted = c.post(f"/youtube/projects/{pid}/metadata/generate").json()["publishing"]["metadata"]
    assert drafted["title"] == "Drafted title" and drafted["category_id"] == "24"

    detail = c.patch(f"/youtube/projects/{pid}/metadata", json={"made_for_kids": False, "tags": ["a", "b"]}).json()
    assert detail["publishing"]["metadata"]["tags"] == ["a", "b"]
    assert detail["publishing"]["upload_problems"] == []
    assert c.patch(f"/youtube/projects/{pid}/metadata", json={"category_id": "abc"}).status_code == 400

    assert c.post(f"/youtube/projects/{pid}/upload", json={}).status_code == 400
    started = c.post(f"/youtube/projects/{pid}/upload", json={"reviewed": True})
    assert started.status_code == 202
    uid = started.json()["upload"]["id"]
    assert api.queued[-1] == ("upload_video", (uid, "biz"), {})
    assert c.get(f"/youtube/projects/{pid}").json()["status"] == "uploading"

    with api.Session() as db:
        upload = db.get(YTUpload, uid)
        publishing.claim_upload(db, uid)
        publishing.run_upload(db, upload, api.storage, routes.publisher)

    naive = c.post(f"/youtube/projects/{pid}/uploads/{uid}/publish", json={"publish_at": "2030-01-01T10:00:00"})
    assert naive.status_code == 422
    scheduled = c.post(f"/youtube/projects/{pid}/uploads/{uid}/publish", json={"publish_at": "2030-01-01T10:00:00+01:00"})
    assert scheduled.status_code == 200 and scheduled.json()["status"] == "scheduled"
    body = c.post(f"/youtube/projects/{pid}/uploads/{uid}/publish", json={}).json()
    assert body["status"] == "published" and body["publishing"]["uploads"][0]["privacy"] == "public"
    assert c.post(f"/youtube/projects/{pid}/uploads/{uid}/refresh").status_code == 200
    assert c.post(f"/youtube/projects/{pid}/uploads/{uid}/cancel").status_code == 400

    assert c.post(f"/youtube/projects/{pid}/uploads/{uid + 50}/publish", json={}).status_code == 404
    api.user.business_id = "someone-else"
    assert c.post(f"/youtube/projects/{pid}/uploads/{uid}/publish", json={}).status_code == 404
    assert not c.get("/youtube/channel").json()["connected"]
