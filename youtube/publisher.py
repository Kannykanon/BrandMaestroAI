"""Publishing to YouTube, with the same plug-and-adapter design as model.py.

    PublisherPort      the abstract adapter: sign-in, private upload, publish, status
    YouTubePublisher   the YouTube Data API v3 over HTTPS (OAuth 2.0 web flow, resumable upload)
    PublisherSingleton the shared instance

    YT_GOOGLE_CLIENT_ID      OAuth client (type "Web application") in the Google Cloud project
    YT_GOOGLE_CLIENT_SECRET  that client's secret
    YT_UPLOAD_CHUNK_MB       upload chunk size in MB (default 8)

The OAuth consent screen must be "In production": in "Testing", Google expires
refresh tokens after 7 days. Videos uploaded from an API project that has not
passed YouTube's compliance audit are locked private by YouTube, and cannot be
made public even in YouTube Studio; publish() detects this.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD_API = "https://www.googleapis.com/upload/youtube/v3"
# youtube.upload for videos.insert; youtube for videos.update, channels.list and thumbnails.set.
SCOPES = ("https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube")
RETRYABLE_STATUS = {500, 502, 503, 504}
CHUNK_MULTIPLE = 256 * 1024


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


class PublishError(RuntimeError):
    """YouTube refused or failed a request."""


class AuthError(PublishError):
    """Google refused the saved sign-in; the channel must be connected again."""


class QuotaExhausted(PublishError):
    """YouTube reported the quota as used up."""


@dataclass(frozen=True)
class ChannelInfo:
    channel_id: str
    title: str


@dataclass
class VideoMetadata:
    title: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    category_id: str = "24"
    made_for_kids: bool = False
    # Realistic altered or synthetic content. Always true for these videos (design decision D13).
    synthetic_media: bool = True
    language: str = "en"

    def snippet(self) -> dict:
        return {"title": self.title, "description": self.description, "tags": list(self.tags),
                "categoryId": self.category_id, "defaultLanguage": self.language,
                "defaultAudioLanguage": self.language}

    def status(self, privacy: str = "private", publish_at: Optional[datetime] = None) -> dict:
        # videos.update deletes any status property it is not sent, so every one is always sent.
        status = {"privacyStatus": privacy, "selfDeclaredMadeForKids": self.made_for_kids,
                  "containsSyntheticMedia": self.synthetic_media, "embeddable": True, "license": "youtube"}
        if publish_at is not None:
            status["privacyStatus"] = "private"
            status["publishAt"] = publish_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return status


@dataclass(frozen=True)
class VideoStatus:
    video_id: str
    privacy: Optional[str]
    upload_status: Optional[str]
    publish_at: Optional[str] = None
    failure_reason: Optional[str] = None
    rejection_reason: Optional[str] = None


class PublisherPort(ABC):
    """Adapter between YouTube Automation and a video platform account."""

    name: str = ""
    required_env: tuple[str, ...] = ()

    @classmethod
    def missing_env(cls) -> list[str]:
        return [var for var in cls.required_env if not _env(var)]

    @abstractmethod
    def authorization_url(self, state: str, redirect_uri: str) -> str:
        """Where to send the owner to grant access."""

    @abstractmethod
    def exchange_code(self, code: str, redirect_uri: str) -> str:
        """Trade the authorization code for a refresh token."""

    @abstractmethod
    def channel(self, refresh_token: str) -> ChannelInfo:
        """The channel the refresh token belongs to."""

    @abstractmethod
    def upload_private(self, refresh_token: str, video: bytes, metadata: VideoMetadata,
                       session_url: Optional[str] = None,
                       on_session: Callable[[str], None] = lambda url: None,
                       on_progress: Callable[[int, int], None] = lambda sent, total: None) -> str:
        """Upload as private and return the video id. Continues session_url if it is still open."""

    @abstractmethod
    def set_thumbnail(self, refresh_token: str, video_id: str, jpeg: bytes) -> None:
        """Set the video's thumbnail. Needs a channel verified by phone."""

    @abstractmethod
    def set_privacy(self, refresh_token: str, video_id: str, metadata: VideoMetadata, privacy: str,
                    publish_at: Optional[datetime] = None) -> None:
        """Make the video public, or schedule it by setting publish_at."""

    @abstractmethod
    def video_status(self, refresh_token: str, video_id: str) -> VideoStatus:
        """The video's privacy and processing state as YouTube reports it."""

    @abstractmethod
    def revoke(self, refresh_token: str) -> None:
        """Withdraw the access the owner granted."""


class YouTubePublisher(PublisherPort):
    name = "youtube"
    required_env = ("YT_GOOGLE_CLIENT_ID", "YT_GOOGLE_CLIENT_SECRET")

    def __init__(self, http=None, sleep=time.sleep, clock=time.monotonic):
        self._http = http
        self._sleep = sleep
        self._clock = clock
        self._tokens: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    # --- HTTP and tokens -----------------------------------------------------
    def _get_http(self):
        if self._http is None:
            import httpx
            self._http = httpx.Client(timeout=httpx.Timeout(60.0, write=300.0))
        return self._http

    @staticmethod
    def _error(response) -> tuple[str, str]:
        """(reason, message) from a Google error response."""
        try:
            body = response.json()
        except Exception:
            return "", (response.text or "")[:300]
        error = body.get("error")
        if isinstance(error, dict):
            errors = error.get("errors") or [{}]
            return errors[0].get("reason", "") or error.get("status", ""), error.get("message", "")
        return str(error or ""), body.get("error_description", "")

    def _raise_for(self, response, what: str):
        if response.status_code < 400:
            return
        reason, message = self._error(response)
        if response.status_code == 401 or reason in ("invalid_grant", "unauthorized_client", "authError"):
            raise AuthError(f"Google refused the YouTube sign-in ({reason or response.status_code}): {message}")
        if reason in ("quotaExceeded", "uploadLimitExceeded", "dailyLimitExceeded"):
            raise QuotaExhausted(f"YouTube quota used up while {what} ({reason})")
        raise PublishError(f"YouTube {what} failed ({response.status_code}{', ' + reason if reason else ''}): {message}")

    def _access_token(self, refresh_token: str) -> str:
        with self._lock:
            cached = self._tokens.get(refresh_token)
            if cached and cached[1] > self._clock() + 60:
                return cached[0]
        response = self._get_http().post(TOKEN_URL, data={
            "client_id": _env("YT_GOOGLE_CLIENT_ID"), "client_secret": _env("YT_GOOGLE_CLIENT_SECRET"),
            "refresh_token": refresh_token, "grant_type": "refresh_token",
        })
        if response.status_code >= 400:
            reason, message = self._error(response)
            raise AuthError(f"Google refused the YouTube sign-in ({reason or response.status_code}): {message}")
        body = response.json()
        with self._lock:
            self._tokens[refresh_token] = (body["access_token"], self._clock() + float(body.get("expires_in", 3600)))
        return body["access_token"]

    def _auth(self, refresh_token: str) -> dict:
        return {"Authorization": f"Bearer {self._access_token(refresh_token)}"}

    # --- Sign-in -------------------------------------------------------------
    def authorization_url(self, state, redirect_uri):
        return AUTH_URL + "?" + urlencode({
            "client_id": _env("YT_GOOGLE_CLIENT_ID"), "redirect_uri": redirect_uri, "response_type": "code",
            "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent",
            "include_granted_scopes": "true", "state": state,
        })

    def exchange_code(self, code, redirect_uri):
        response = self._get_http().post(TOKEN_URL, data={
            "client_id": _env("YT_GOOGLE_CLIENT_ID"), "client_secret": _env("YT_GOOGLE_CLIENT_SECRET"),
            "code": code, "redirect_uri": redirect_uri, "grant_type": "authorization_code",
        })
        if response.status_code >= 400:
            reason, message = self._error(response)
            raise AuthError(f"Google did not accept the sign-in ({reason or response.status_code}): {message}")
        body = response.json()
        granted = set((body.get("scope") or "").split())
        missing = [s for s in SCOPES if granted and s not in granted]
        if missing:
            raise AuthError("YouTube access was not fully granted; allow uploading and managing videos")
        if not body.get("refresh_token"):
            raise AuthError("Google returned no refresh token; remove the app's access in your Google account "
                            "settings and connect again")
        with self._lock:
            self._tokens[body["refresh_token"]] = (body["access_token"],
                                                   self._clock() + float(body.get("expires_in", 3600)))
        return body["refresh_token"]

    def channel(self, refresh_token):
        response = self._get_http().get(f"{API}/channels", params={"part": "snippet", "mine": "true"},
                                        headers=self._auth(refresh_token))
        self._raise_for(response, "reading the channel")
        items = response.json().get("items") or []
        if not items:
            raise PublishError("This Google account has no YouTube channel; create one first")
        return ChannelInfo(items[0]["id"], (items[0].get("snippet") or {}).get("title", ""))

    def revoke(self, refresh_token):
        try:
            self._get_http().post(REVOKE_URL, data={"token": refresh_token})
        except Exception as e:  # the token is deleted locally either way
            logger.warning("Revoking the YouTube token failed: %s", e)
        with self._lock:
            self._tokens.pop(refresh_token, None)

    # --- Upload --------------------------------------------------------------
    def _chunk_size(self) -> int:
        mb = float(_env("YT_UPLOAD_CHUNK_MB") or 8)
        return max(int(mb * 1024 * 1024) // CHUNK_MULTIPLE, 1) * CHUNK_MULTIPLE

    def _start_session(self, refresh_token, size, metadata) -> str:
        response = self._get_http().post(
            f"{UPLOAD_API}/videos", params={"uploadType": "resumable", "part": "snippet,status"},
            json={"snippet": metadata.snippet(), "status": metadata.status("private")},
            headers={**self._auth(refresh_token), "X-Upload-Content-Length": str(size),
                     "X-Upload-Content-Type": "video/mp4"})
        self._raise_for(response, "starting the upload")
        url = response.headers.get("Location") or response.headers.get("location")
        if not url:
            raise PublishError("YouTube did not return an upload session")
        return url

    @staticmethod
    def _received(response) -> int:
        """Bytes YouTube holds, from a 308 response's Range header (bytes=0-N)."""
        value = response.headers.get("Range") or response.headers.get("range")
        if not value:
            return 0
        return int(value.rsplit("-", 1)[-1]) + 1

    def _query_offset(self, refresh_token, url, size):
        """Ask an open session how much it has. Returns (offset, video_id or None), or None if it is gone."""
        response = self._get_http().put(url, content=b"",
                                        headers={**self._auth(refresh_token), "Content-Range": f"bytes */{size}"})
        if response.status_code == 308:
            return self._received(response), None
        if response.status_code in (200, 201):
            return size, response.json().get("id")
        if response.status_code in (404, 410):
            return None
        self._raise_for(response, "resuming the upload")
        return None

    def upload_private(self, refresh_token, video, metadata, session_url=None,
                       on_session=lambda url: None, on_progress=lambda sent, total: None):
        size = len(video)
        offset = 0
        if session_url:
            state = self._query_offset(refresh_token, session_url, size)
            if state is None:
                session_url = None
            else:
                offset, video_id = state
                if video_id:
                    on_progress(size, size)
                    return video_id
        if not session_url:
            session_url = self._start_session(refresh_token, size, metadata)
            on_session(session_url)

        chunk, failures = self._chunk_size(), 0
        while True:
            end = min(offset + chunk, size) - 1
            error = None
            try:
                response = self._get_http().put(session_url, content=video[offset:end + 1], headers={
                    **self._auth(refresh_token), "Content-Type": "video/mp4",
                    "Content-Range": f"bytes {offset}-{end}/{size}"})
            except (AuthError, PublishError):
                raise
            except Exception as e:  # network trouble: ask the session where it got to, then continue
                response, error = None, e
            if response is not None and response.status_code in (200, 201):
                on_progress(size, size)
                video_id = response.json().get("id")
                if not video_id:
                    raise PublishError("YouTube finished the upload but returned no video id")
                return video_id
            if response is not None and response.status_code == 308:
                offset, failures = self._received(response), 0
                on_progress(offset, size)
                continue
            if response is not None and response.status_code not in RETRYABLE_STATUS:
                self._raise_for(response, "uploading")
                raise PublishError(f"YouTube upload failed ({response.status_code})")
            failures += 1
            if failures > 6:
                detail = error if response is None else f"HTTP {response.status_code}"
                raise PublishError(f"The upload kept failing ({detail}); it will continue from where it stopped")
            self._sleep(min(2 ** failures, 60))
            state = self._query_offset(refresh_token, session_url, size)
            if state is None:
                raise PublishError("The upload session expired; the upload will start again")
            offset, video_id = state
            if video_id:
                on_progress(size, size)
                return video_id

    # --- After upload --------------------------------------------------------
    def set_thumbnail(self, refresh_token, video_id, jpeg):
        response = self._get_http().post(f"{UPLOAD_API}/thumbnails/set", params={"videoId": video_id},
                                         content=jpeg, headers={**self._auth(refresh_token), "Content-Type": "image/jpeg"})
        self._raise_for(response, "setting the thumbnail")

    def set_privacy(self, refresh_token, video_id, metadata, privacy, publish_at=None):
        response = self._get_http().put(f"{API}/videos", params={"part": "status"},
                                        json={"id": video_id, "status": metadata.status(privacy, publish_at)},
                                        headers=self._auth(refresh_token))
        self._raise_for(response, "publishing")

    def video_status(self, refresh_token, video_id):
        response = self._get_http().get(f"{API}/videos", params={"part": "status", "id": video_id},
                                        headers=self._auth(refresh_token))
        self._raise_for(response, "reading the video")
        items = response.json().get("items") or []
        if not items:
            return VideoStatus(video_id, None, "deleted")
        status = items[0].get("status") or {}
        return VideoStatus(video_id, status.get("privacyStatus"), status.get("uploadStatus"),
                           status.get("publishAt"), status.get("failureReason"), status.get("rejectionReason"))


class PublisherSingleton:
    _instance: Optional[PublisherPort] = None
    _lock = threading.Lock()

    @classmethod
    def get(cls) -> PublisherPort:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = YouTubePublisher()
        return cls._instance
