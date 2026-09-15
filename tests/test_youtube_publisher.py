"""Phase 4 building blocks: the YouTube adapter (fake HTTP), quota ledger, token encryption and metadata."""
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tests.test_youtube_render import FakeHTTP, Response
from youtube import metadata, quota
from youtube.models import init_youtube_tables
from youtube.projects import ProjectError
from youtube.publisher import (
    SCOPES,
    AuthError,
    PublishError,
    QuotaExhausted,
    VideoMetadata,
    YouTubePublisher,
)
from youtube.token_crypto import SecretError, decrypt, encrypt


@pytest.fixture
def google(monkeypatch):
    monkeypatch.setenv("YT_GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setenv("YT_GOOGLE_CLIENT_SECRET", "client-secret")


def token_response(access="access-1", refresh=None, scope=" ".join(SCOPES)):
    body = {"access_token": access, "expires_in": 3599, "scope": scope}
    if refresh:
        body["refresh_token"] = refresh
    return Response(payload=body)


class Headers(Response):
    def __init__(self, status_code=200, payload=None, headers=None):
        super().__init__(status_code, payload)
        self.headers = headers or {}


def google_error(status, reason, message="nope"):
    return Headers(status, {"error": {"code": status, "message": message, "errors": [{"reason": reason}]}})


# ---------------------------------------------------------------------------
#  Adapter
# ---------------------------------------------------------------------------
class TestSignIn:
    def test_authorization_url_asks_for_offline_upload_and_manage_access(self, google):
        url = YouTubePublisher(http=FakeHTTP([])).authorization_url("st", "https://app/youtube/channel/callback")
        query = parse_qs(urlparse(url).query)
        assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
        assert query["scope"] == [" ".join(SCOPES)] and query["access_type"] == ["offline"]
        assert query["prompt"] == ["consent"] and query["state"] == ["st"]
        assert query["redirect_uri"] == ["https://app/youtube/channel/callback"]

    def test_exchange_returns_the_refresh_token_and_caches_access(self, google):
        http = FakeHTTP([
            ("POST", "oauth2.googleapis.com/token", token_response(refresh="refresh-1")),
            ("GET", "/youtube/v3/channels", Headers(payload={"items": [{"id": "UC1", "snippet": {"title": "Bakery Tales"}}]})),
        ])
        publisher = YouTubePublisher(http=http)
        assert publisher.exchange_code("code-1", "https://app/cb") == "refresh-1"
        assert http.requests[0].data["grant_type"] == "authorization_code"
        info = publisher.channel("refresh-1")
        assert (info.channel_id, info.title) == ("UC1", "Bakery Tales")
        assert http.requests[1].headers == {"Authorization": "Bearer access-1"}
        assert len(http.requests) == 2, "the access token from the exchange is reused"

    def test_exchange_without_refresh_token_or_scopes_is_refused(self, google):
        publisher = YouTubePublisher(http=FakeHTTP([("POST", "token", token_response())]))
        with pytest.raises(AuthError, match="no refresh token"):
            publisher.exchange_code("c", "u")
        publisher = YouTubePublisher(http=FakeHTTP([
            ("POST", "token", token_response(refresh="r", scope="https://www.googleapis.com/auth/youtube.upload"))]))
        with pytest.raises(AuthError, match="not fully granted"):
            publisher.exchange_code("c", "u")

    def test_refused_refresh_is_an_auth_error(self, google):
        http = FakeHTTP([("POST", "token", Headers(400, {"error": "invalid_grant", "error_description": "expired"}))])
        with pytest.raises(AuthError, match="invalid_grant"):
            YouTubePublisher(http=http).channel("old-refresh")

    def test_access_tokens_are_refreshed_when_they_expire(self, google):
        clock = SimpleNamespace(t=0.0)
        http = FakeHTTP([
            ("POST", "token", [token_response("a1"), token_response("a2")]),
            ("GET", "/channels", Headers(payload={"items": [{"id": "UC1", "snippet": {}}]})),
        ])
        publisher = YouTubePublisher(http=http, clock=lambda: clock.t)
        publisher.channel("r")
        publisher.channel("r")
        clock.t = 4000
        publisher.channel("r")
        auths = [r.headers["Authorization"] for r in http.requests if r.method == "GET"]
        assert auths == ["Bearer a1", "Bearer a1", "Bearer a2"]


class TestUpload:
    VIDEO = bytes(range(256)) * 2500  # 640 KB

    def test_uploads_in_chunks_and_returns_the_video_id(self, google, monkeypatch):
        monkeypatch.setenv("YT_UPLOAD_CHUNK_MB", "0.25")
        size = len(self.VIDEO)
        http = FakeHTTP([
            ("POST", "token", token_response()),
            ("POST", "upload/youtube/v3/videos", Headers(headers={"Location": "https://upload/session-1"})),
            ("PUT", "https://upload/session-1", [
                Headers(308, headers={"Range": "bytes=0-262143"}),
                Headers(308, headers={"Range": "bytes=0-524287"}),
                Headers(201, {"id": "vid123"}),
            ]),
        ])
        sessions, progress = [], []
        meta = VideoMetadata("A bakery", "Story", ["bread"], made_for_kids=False)
        video_id = YouTubePublisher(http=http).upload_private("r", self.VIDEO, meta, on_session=sessions.append,
                                                              on_progress=lambda s, t: progress.append(s))
        assert video_id == "vid123" and sessions == ["https://upload/session-1"]
        start = http.requests[1]
        assert start.params == {"uploadType": "resumable", "part": "snippet,status"}
        assert start.headers["X-Upload-Content-Length"] == str(size)
        assert start.json["status"] == {"privacyStatus": "private", "selfDeclaredMadeForKids": False,
                                        "containsSyntheticMedia": True, "embeddable": True, "license": "youtube"}
        assert start.json["snippet"]["title"] == "A bakery" and start.json["snippet"]["categoryId"] == "24"
        ranges = [r.headers["Content-Range"] for r in http.requests[2:]]
        assert ranges == [f"bytes 0-262143/{size}", f"bytes 262144-524287/{size}", f"bytes 524288-{size - 1}/{size}"]
        assert sum(len(r.content) for r in http.requests[2:]) == size
        assert progress[-1] == size

    def test_resumes_an_open_session_from_where_youtube_stopped(self, google, monkeypatch):
        monkeypatch.setenv("YT_UPLOAD_CHUNK_MB", "1")
        size = len(self.VIDEO)
        http = FakeHTTP([
            ("POST", "token", token_response()),
            ("PUT", "https://upload/s", [Headers(308, headers={"Range": "bytes=0-299999"}), Headers(200, {"id": "v9"})]),
        ])
        video_id = YouTubePublisher(http=http).upload_private("r", self.VIDEO, VideoMetadata("t"), session_url="https://upload/s")
        assert video_id == "v9"
        assert http.requests[1].headers["Content-Range"] == f"bytes */{size}"
        assert http.requests[2].headers["Content-Range"] == f"bytes 300000-{size - 1}/{size}"
        assert not any(r.method == "POST" and "upload/youtube" in r.url for r in http.requests)

    def test_an_expired_session_starts_a_new_upload(self, google):
        http = FakeHTTP([
            ("POST", "token", token_response()),
            ("PUT", "https://upload/old", Headers(404, {})),
            ("POST", "upload/youtube/v3/videos", Headers(headers={"Location": "https://upload/new"})),
            ("PUT", "https://upload/new", Headers(201, {"id": "v2"})),
        ])
        assert YouTubePublisher(http=http).upload_private("r", b"x" * 10, VideoMetadata("t"), session_url="https://upload/old") == "v2"

    def test_server_errors_are_retried_after_asking_for_the_offset(self, google):
        sleeps = []
        http = FakeHTTP([
            ("POST", "token", token_response()),
            ("POST", "upload/youtube/v3/videos", Headers(headers={"Location": "https://upload/s"})),
            ("PUT", "https://upload/s", [Headers(503, {}), Headers(308, headers={}), Headers(201, {"id": "v3"})]),
        ])
        assert YouTubePublisher(http=http, sleep=sleeps.append).upload_private("r", b"abc", VideoMetadata("t")) == "v3"
        assert sleeps == [2]
        assert http.requests[3].headers["Content-Range"] == "bytes */3"
        assert http.requests[4].headers["Content-Range"] == "bytes 0-2/3"

    def test_quota_and_auth_errors_are_told_apart(self, google):
        def publisher_answering(response):
            return YouTubePublisher(http=FakeHTTP([("POST", "token", token_response()),
                                                   ("POST", "upload/youtube/v3/videos", response)]))
        with pytest.raises(QuotaExhausted):
            publisher_answering(google_error(403, "quotaExceeded")).upload_private("r", b"a", VideoMetadata("t"))
        with pytest.raises(QuotaExhausted):
            publisher_answering(google_error(400, "uploadLimitExceeded")).upload_private("r", b"a", VideoMetadata("t"))
        with pytest.raises(AuthError):
            publisher_answering(google_error(401, "authError")).upload_private("r", b"a", VideoMetadata("t"))
        with pytest.raises(PublishError, match="invalidTitle"):
            publisher_answering(google_error(400, "invalidTitle")).upload_private("r", b"a", VideoMetadata("t"))


class TestAfterUpload:
    def test_publishing_sends_every_status_field(self, google):
        http = FakeHTTP([("POST", "token", token_response()), ("PUT", "/youtube/v3/videos", Headers(payload={}))])
        meta = VideoMetadata("t", made_for_kids=True)
        YouTubePublisher(http=http).set_privacy("r", "vid", meta, "public")
        request = http.requests[1]
        assert request.params == {"part": "status"}
        assert request.json == {"id": "vid", "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": True,
                                                        "containsSyntheticMedia": True, "embeddable": True,
                                                        "license": "youtube"}}

    def test_scheduling_sets_private_with_publish_at_in_utc(self):
        when = datetime(2026, 9, 20, 18, 30, tzinfo=timezone.utc)
        status = VideoMetadata("t").status("public", publish_at=when)
        assert status["privacyStatus"] == "private" and status["publishAt"] == "2026-09-20T18:30:00Z"

    def test_video_status_and_thumbnail(self, google):
        http = FakeHTTP([
            ("POST", "oauth2.googleapis.com/token", token_response()),
            ("GET", "/youtube/v3/videos", [
                Headers(payload={"items": [{"status": {"privacyStatus": "private", "uploadStatus": "processed",
                                                       "publishAt": "2026-09-20T18:30:00Z"}}]}),
                Headers(payload={"items": []})]),
            ("POST", "/thumbnails/set", google_error(403, "forbidden", "The authenticated user doesnt have permissions")),
        ])
        publisher = YouTubePublisher(http=http)
        status = publisher.video_status("r", "vid")
        assert (status.privacy, status.upload_status, status.publish_at) == ("private", "processed", "2026-09-20T18:30:00Z")
        assert publisher.video_status("r", "gone").upload_status == "deleted"
        with pytest.raises(PublishError, match="thumbnail"):
            publisher.set_thumbnail("r", "vid", b"jpeg")

    def test_revoke_never_raises(self, google):
        class Broken:
            def post(self, *a, **k):
                raise OSError("offline")
        YouTubePublisher(http=Broken()).revoke("r")


# ---------------------------------------------------------------------------
#  Quota
# ---------------------------------------------------------------------------
@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    init_youtube_tables(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


class TestQuota:
    def test_days_follow_pacific_time(self):
        assert quota.pacific_day(datetime(2026, 9, 15, 6, 59, tzinfo=timezone.utc)) == "2026-09-14"
        assert quota.pacific_day(datetime(2026, 9, 15, 7, 0, tzinfo=timezone.utc)) == "2026-09-15"
        assert quota.next_reset(datetime(2026, 9, 15, 6, 0, tzinfo=timezone.utc)) == \
            datetime(2026, 9, 15, 7, 1, tzinfo=timezone.utc)
        # Winter: Pacific standard time is UTC-8.
        assert quota.next_reset(datetime(2026, 12, 1, 20, 0, tzinfo=timezone.utc)) == \
            datetime(2026, 12, 2, 8, 1, tzinfo=timezone.utc)

    def test_reserve_counts_until_the_limit(self, db, monkeypatch):
        monkeypatch.setenv("YT_DAILY_UPLOADS", "2")
        monkeypatch.setenv("YT_DAILY_QUOTA_UNITS", "120")
        now = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)
        quota.reserve(db, "upload", now)
        quota.reserve(db, "upload", now)
        with pytest.raises(quota.QuotaExceeded) as exceeded:
            quota.reserve(db, "upload", now)
        assert exceeded.value.retry_at == quota.next_reset(now)
        quota.reserve(db, "update", now)
        quota.reserve(db, "update", now)
        with pytest.raises(quota.QuotaExceeded):
            quota.reserve(db, "thumbnail", now)
        quota.reserve(db, "list", now)
        assert quota.usage(db, now)["uploads"] == {"used": 2, "limit": 2}
        assert quota.usage(db, now)["units"] == {"used": 101, "limit": 120}
        tomorrow = datetime(2026, 9, 16, 12, tzinfo=timezone.utc)
        quota.reserve(db, "upload", tomorrow)
        assert quota.usage(db, tomorrow)["uploads"]["used"] == 1

    def test_exhaust_blocks_the_rest_of_the_day(self, db):
        now = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)
        assert quota.exhaust(db, "upload", now) == quota.next_reset(now)
        with pytest.raises(quota.QuotaExceeded):
            quota.reserve(db, "upload", now)


# ---------------------------------------------------------------------------
#  Token encryption
# ---------------------------------------------------------------------------
class TestTokenCrypto:
    def test_round_trip_with_derived_key(self, monkeypatch):
        monkeypatch.delenv("YT_TOKEN_KEY", raising=False)
        monkeypatch.setenv("SECRET_KEY", "one")
        token = encrypt("refresh-token")
        assert token != "refresh-token" and decrypt(token) == "refresh-token"
        monkeypatch.setenv("SECRET_KEY", "two")
        with pytest.raises(SecretError, match="connect the channel again"):
            decrypt(token)

    def test_explicit_key_wins(self, monkeypatch):
        key = Fernet.generate_key().decode()
        monkeypatch.setenv("YT_TOKEN_KEY", key)
        token = encrypt("x")
        assert Fernet(key.encode()).decrypt(token.encode()) == b"x"


# ---------------------------------------------------------------------------
#  Metadata
# ---------------------------------------------------------------------------
class TestMetadataCleaning:
    def test_title_limits_and_forbidden_characters(self):
        assert metadata.clean_title("  A <new>   dawn \n") == "A ‹new› dawn"
        assert len(metadata.clean_title("x" * 150)) == 100

    def test_description_is_cut_by_bytes_not_characters(self):
        text = "é" * 3000  # 6000 bytes
        cleaned = metadata.clean_description(text)
        assert len(cleaned.encode("utf-8")) <= 5000 and cleaned == "é" * 2500

    def test_tags_are_deduplicated_and_fit_youtube_counting(self):
        tags = metadata.clean_tags(["#bakery", "Bakery", 'sour "dough"', "a,b", "x" * 31, "", 5])
        assert tags == ["bakery", "sour dough", "a b"]
        assert metadata.tags_length(["one", "two words"]) == 3 + 11 + 1
        many = metadata.clean_tags([f"tag number {i}" for i in range(100)])
        assert metadata.tags_length(many) <= 500 and len(many) > 20

    def test_comma_separated_string_is_accepted(self):
        assert metadata.clean_tags("bread, small business ,bread") == ["bread", "small business"]


class FakeWriter:
    def __init__(self, content):
        self.content, self.prompts = content, []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return SimpleNamespace(content=self.content)


def project(fmt="long_form"):
    return SimpleNamespace(business_id="biz", format=fmt, script_snapshot="MAYA: One more try.",
                           video_title=None, video_description=None, video_tags=None,
                           video_category_id=None, made_for_kids=None)


class TestMetadataDraft:
    def test_draft_uses_brand_and_script_and_cleans_output(self):
        llm = FakeWriter('```json\n{"title": "One More <Try>", "description": "Maya bakes.", "tags": ["bakery", "bakery", "grit"]}\n```')
        draft = metadata.draft_metadata(project(), llm=llm, brand_context=lambda b: ("Crumb & Co", "# VOICE\nWarm"))
        assert draft == {"title": "One More ‹Try›", "description": "Maya bakes.", "tags": ["bakery", "grit"]}
        assert "Crumb & Co" in llm.prompts[0] and "# VOICE" in llm.prompts[0] and "MAYA: One more try." in llm.prompts[0]

    def test_shorts_get_the_hashtag(self):
        llm = FakeWriter('{"title": "T", "description": "D", "tags": []}')
        draft = metadata.draft_metadata(project("short"), llm=llm, brand_context=lambda b: ("", ""))
        assert draft["description"].endswith("#Shorts") and "#Shorts" in llm.prompts[0]

    def test_unusable_output_is_an_error(self):
        with pytest.raises(ProjectError, match="no usable title"):
            metadata.draft_metadata(project(), llm=FakeWriter('{"title": ""}'), brand_context=lambda b: ("", ""))
        with pytest.raises(ProjectError, match="failed"):
            metadata.draft_metadata(project(), llm=FakeWriter("not json"), brand_context=lambda b: ("", ""))

    def test_problems_and_video_metadata(self, monkeypatch):
        p = project()
        assert metadata.metadata_problems(p) == ["Give the video a title", "Say whether the video is made for kids"]
        p.video_title, p.made_for_kids, p.video_tags = "T", False, ["a"]
        monkeypatch.setenv("YT_VIDEO_LANGUAGE", "en-GB")
        meta = metadata.video_metadata(p)
        assert metadata.metadata_problems(p) == []
        assert (meta.category_id, meta.language, meta.synthetic_media) == ("24", "en-GB", True)
