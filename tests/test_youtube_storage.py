"""The storage port: key safety, the local adapter, the GCS adapter and the switch."""
from datetime import timedelta

import pytest

from youtube.storage import (
    GCSStorage,
    LocalStorage,
    StoragePort,
    StorageSingleton,
    business_key,
    validate_key,
)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(StorageSingleton, "_instance", None)

    def configure(**values):
        for var in ("YT_STORAGE_PROVIDER", "YT_GCS_BUCKET", "YT_LOCAL_STORAGE_PATH"):
            monkeypatch.delenv(var, raising=False)
        for key, value in values.items():
            monkeypatch.setenv(key, value)

    configure()
    return configure


class TestKeys:
    @pytest.mark.parametrize("key", [
        "businesses/b1/projects/12/shots/3.png",
        "a.txt",
        "businesses/0c9f-uuid/characters/4/face_01.webp",
    ])
    def test_valid(self, key):
        assert validate_key(key) == key

    @pytest.mark.parametrize("key", [
        "", "/etc/passwd", "../secret", "a/../../b", "a//b", "a/./b",
        "a\\b", "a/b c.png", "a/b?.png",
    ])
    def test_invalid(self, key):
        with pytest.raises(ValueError):
            validate_key(key)

    def test_business_key_scopes_to_the_business(self):
        assert business_key("b1", "projects", "7", "render.mp4") == "businesses/b1/projects/7/render.mp4"
        with pytest.raises(ValueError):
            business_key("b1", "..", "b2", "render.mp4")


class TestLocalStorage:
    def test_round_trip_overwrite_and_delete(self, tmp_path):
        store = LocalStorage(root=str(tmp_path))
        key = "businesses/b1/a/file.bin"
        assert not store.exists(key)
        assert store.put(key, b"one") == key
        assert store.get(key) == b"one"
        store.put(key, b"two")
        assert store.get(key) == b"two"
        assert not list(tmp_path.rglob("*.tmp")), "no partial files left behind"
        store.delete(key)
        assert not store.exists(key)
        store.delete(key)  # deleting a missing key is fine

    def test_missing_key_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            LocalStorage(root=str(tmp_path)).get("nope.bin")

    def test_rejects_traversal(self, tmp_path):
        with pytest.raises(ValueError):
            LocalStorage(root=str(tmp_path)).put("../outside.bin", b"x")

    def test_has_no_public_url(self, tmp_path):
        assert LocalStorage(root=str(tmp_path)).url("a.bin") is None


class _NotFound(Exception):
    pass


class _FakeBlob:
    def __init__(self, bucket, name):
        self.bucket, self.name = bucket, name

    def upload_from_string(self, data, content_type=None):
        self.bucket.objects[self.name] = (data, content_type)

    def download_as_bytes(self):
        if self.name not in self.bucket.objects:
            from google.api_core.exceptions import NotFound
            raise NotFound("missing")
        return self.bucket.objects[self.name][0]

    def exists(self):
        return self.name in self.bucket.objects

    def delete(self):
        if self.name not in self.bucket.objects:
            from google.api_core.exceptions import NotFound
            raise NotFound("missing")
        del self.bucket.objects[self.name]

    def generate_signed_url(self, **kwargs):
        self.bucket.signed.append((self.name, kwargs))
        return f"https://signed.example/{self.name}"


class _FakeBucket:
    def __init__(self):
        self.objects, self.signed = {}, []

    def blob(self, name):
        return _FakeBlob(self, name)


class _FakeClient:
    def __init__(self):
        self.buckets = {}

    def bucket(self, name):
        return self.buckets.setdefault(name, _FakeBucket())


class TestGCSStorage:
    def test_round_trip_and_not_found_mapping(self):
        client = _FakeClient()
        store = GCSStorage(bucket="yt-assets", client=client)
        key = "businesses/b1/x.png"
        store.put(key, b"img", content_type="image/png")
        assert client.buckets["yt-assets"].objects[key] == (b"img", "image/png")
        assert store.exists(key) and store.get(key) == b"img"
        store.delete(key)
        store.delete(key)
        with pytest.raises(FileNotFoundError):
            store.get(key)

    def test_signed_url_is_v4_get_with_expiry(self):
        client = _FakeClient()
        store = GCSStorage(bucket="yt-assets", client=client)
        url = store.url("businesses/b1/x.png", expires=timedelta(minutes=15))
        assert url == "https://signed.example/businesses/b1/x.png"
        _, kwargs = client.buckets["yt-assets"].signed[0]
        assert kwargs == {"version": "v4", "expiration": timedelta(minutes=15), "method": "GET"}

    def test_rejects_invalid_keys_before_calling_gcs(self):
        store = GCSStorage(bucket="yt-assets", client=_FakeClient())
        with pytest.raises(ValueError):
            store.put("../x", b"")

    def test_requires_a_bucket(self, env):
        with pytest.raises(ValueError, match="YT_GCS_BUCKET"):
            GCSStorage()


class TestSwitch:
    def test_adapters_implement_the_port(self):
        for adapter in StorageSingleton.PROVIDERS.values():
            assert issubclass(adapter, StoragePort)

    def test_defaults_to_local_without_a_bucket(self, env):
        assert StorageSingleton.provider_class() is LocalStorage

    def test_defaults_to_gcs_when_a_bucket_is_set(self, env):
        env(YT_GCS_BUCKET="yt-assets")
        assert StorageSingleton.provider_class() is GCSStorage

    def test_explicit_choice_wins(self, env):
        env(YT_GCS_BUCKET="yt-assets", YT_STORAGE_PROVIDER="local")
        assert StorageSingleton.provider_class() is LocalStorage

    def test_unknown_provider_is_a_clear_error(self, env):
        env(YT_STORAGE_PROVIDER="s3")
        with pytest.raises(ValueError, match="YT_STORAGE_PROVIDER must be one of"):
            StorageSingleton.provider_class()

    def test_missing_env(self, env):
        assert GCSStorage.missing_env() == ["YT_GCS_BUCKET"]
        assert LocalStorage.missing_env() == []

    def test_get_shares_one_instance(self, env, tmp_path):
        env(YT_LOCAL_STORAGE_PATH=str(tmp_path))
        assert StorageSingleton.get() is StorageSingleton.get()
