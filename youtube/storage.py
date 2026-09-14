"""Where YouTube Automation keeps its files: uploads, images, audio, clips and renders.

Same plug-and-adapter design as model.py and embedding_stategy.py:

    StoragePort       the abstract adapter
    LocalStorage      a directory on disk, for development
    GCSStorage        a Google Cloud Storage bucket, for production
    StorageSingleton  picks the adapter

    YT_STORAGE_PROVIDER     local | gcs  (default: gcs when YT_GCS_BUCKET is set, else local)
    YT_GCS_BUCKET           bucket name, for gcs
    YT_LOCAL_STORAGE_PATH   directory, for local (default ./.yt_storage)

Video files do not belong on the production VM's disk (see
docs/youtube-automation.md §12), so production should use gcs.

Keys are relative paths such as "businesses/<id>/projects/12/shots/3.png".
business_key() builds them so every file is scoped to one business.
"""
from __future__ import annotations

import os
import re
import threading
from abc import ABC, abstractmethod
from datetime import timedelta
from pathlib import Path
from typing import Optional

_KEY_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def validate_key(key: str) -> str:
    """Reject keys that could escape the storage root or are otherwise malformed."""
    if not key or key.startswith("/") or "\\" in key:
        raise ValueError(f"Invalid storage key: {key!r}")
    segments = key.split("/")
    for segment in segments:
        if segment in ("", ".", "..") or not _KEY_SEGMENT.match(segment):
            raise ValueError(f"Invalid storage key: {key!r}")
    return key


def business_key(business_id: str, *parts: str) -> str:
    """A key scoped to one business, e.g. business_key(bid, "characters", "4", "face.png")."""
    return validate_key("/".join(["businesses", business_id, *parts]))


class StoragePort(ABC):
    """Adapter between YouTube Automation and one file store."""

    #: Value of YT_STORAGE_PROVIDER that selects this adapter.
    name: str = ""
    #: Environment variables that must be set before this store can be used.
    required_env: tuple[str, ...] = ()

    @classmethod
    def missing_env(cls) -> list[str]:
        return [var for var in cls.required_env if not _env(var)]

    @abstractmethod
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        """Store data under key, replacing anything already there. Returns the key."""

    @abstractmethod
    def get(self, key: str) -> bytes:
        """Return the stored bytes. Raises FileNotFoundError if absent."""

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def delete(self, key: str) -> None:
        """Remove the object. Deleting a missing key is not an error."""

    @abstractmethod
    def url(self, key: str, expires: timedelta = timedelta(hours=1)) -> Optional[str]:
        """A time-limited URL a browser can fetch, or None if this store cannot make one."""


class LocalStorage(StoragePort):
    """Files in a local directory. For development and tests."""

    name = "local"

    def __init__(self, root: str | None = None):
        self._root = Path(root or _env("YT_LOCAL_STORAGE_PATH") or ".yt_storage").resolve()

    def _path(self, key: str) -> Path:
        path = (self._root / validate_key(key)).resolve()
        if self._root not in path.parents:
            raise ValueError(f"Invalid storage key: {key!r}")
        return path

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)  # never leave a half-written file under the real key
        return key

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise FileNotFoundError(key)
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def url(self, key: str, expires: timedelta = timedelta(hours=1)) -> Optional[str]:
        # Served through the API in development; there is no public URL.
        return None


class GCSStorage(StoragePort):
    """A Google Cloud Storage bucket (YT_GCS_BUCKET), using Application Default Credentials.

    Signed URLs need credentials that can sign: a service account key file, or
    a service account with the Service Account Token Creator role on itself
    when running on Compute Engine.
    """

    name = "gcs"
    required_env = ("YT_GCS_BUCKET",)

    def __init__(self, bucket: str | None = None, client=None):
        self._bucket_name = bucket or _env("YT_GCS_BUCKET")
        if not self._bucket_name:
            raise ValueError("YT_GCS_BUCKET is required for GCS storage.")
        self._client = client
        self._bucket = None

    def _get_bucket(self):
        if self._bucket is None:
            if self._client is None:
                from google.cloud import storage
                project = _env("PROJECT_ID") or None
                self._client = storage.Client(project=project)
            self._bucket = self._client.bucket(self._bucket_name)
        return self._bucket

    def _blob(self, key: str):
        return self._get_bucket().blob(validate_key(key))

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        self._blob(key).upload_from_string(data, content_type=content_type)
        return key

    def get(self, key: str) -> bytes:
        from google.api_core.exceptions import NotFound

        try:
            return self._blob(key).download_as_bytes()
        except NotFound as e:
            raise FileNotFoundError(key) from e

    def exists(self, key: str) -> bool:
        return self._blob(key).exists()

    def delete(self, key: str) -> None:
        from google.api_core.exceptions import NotFound

        try:
            self._blob(key).delete()
        except NotFound:
            pass

    def url(self, key: str, expires: timedelta = timedelta(hours=1)) -> Optional[str]:
        return self._blob(key).generate_signed_url(version="v4", expiration=expires, method="GET")


class StorageSingleton:
    """Selects the storage adapter and shares one instance per process."""

    PROVIDERS: dict[str, type[StoragePort]] = {
        LocalStorage.name: LocalStorage,
        GCSStorage.name: GCSStorage,
    }

    _instance: StoragePort | None = None
    _lock = threading.Lock()

    @classmethod
    def provider_name(cls) -> str:
        name = _env("YT_STORAGE_PROVIDER").lower()
        if not name:
            return GCSStorage.name if _env("YT_GCS_BUCKET") else LocalStorage.name
        if name not in cls.PROVIDERS:
            raise ValueError(f"YT_STORAGE_PROVIDER must be one of {sorted(cls.PROVIDERS)}, got {name!r}")
        return name

    @classmethod
    def provider_class(cls) -> type[StoragePort]:
        return cls.PROVIDERS[cls.provider_name()]

    @classmethod
    def get(cls) -> StoragePort:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls.provider_class()()
        return cls._instance
