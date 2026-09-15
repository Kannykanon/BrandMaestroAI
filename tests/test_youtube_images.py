"""Image adapters, upload checks and the image registry. No network: clients are fakes."""
import base64
import io
from types import SimpleNamespace

import pytest
from PIL import Image

from youtube.images import (
    GeneratedImage,
    ImageError,
    ImageInput,
    ImagePort,
    ImageRegistry,
    NanoBananaImage,
    SeedreamImage,
    inspect_image,
    prepare_reference,
)


def png(width=512, height=512, color=(200, 120, 40), fmt="PNG") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, format=fmt)
    return out.getvalue()


class TestUploads:
    @pytest.mark.parametrize("fmt, mime", [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")])
    def test_accepts_common_formats(self, fmt, mime):
        assert inspect_image(png(fmt=fmt)) == (mime, 512, 512)

    @pytest.mark.parametrize("data, message", [
        (b"", "empty"),
        (b"not an image at all", "not a readable image"),
        (png(100, 400), "at least 256px"),
        (png(fmt="GIF"), "JPEG, PNG or WebP"),
    ])
    def test_rejects_bad_uploads(self, data, message):
        with pytest.raises(ValueError, match=message):
            inspect_image(data)

    def test_rejects_oversized_files(self, monkeypatch):
        monkeypatch.setattr("youtube.images.MAX_UPLOAD_BYTES", 100)
        with pytest.raises(ValueError, match="at most"):
            inspect_image(png())

    def test_references_are_shrunk_and_reencoded_without_metadata(self):
        source = io.BytesIO()
        image = Image.new("RGBA", (3000, 1500), (10, 20, 30, 255))
        exif = Image.Exif()
        exif[0x010F] = "SecretCam"  # camera make
        image.convert("RGB").save(source, format="JPEG", exif=exif)
        ref = prepare_reference(source.getvalue(), "Reference photo 1 of Maya")
        with Image.open(io.BytesIO(ref.data)) as out:
            assert out.format == "JPEG" and max(out.size) == 1024 and out.size == (1024, 512)
            assert not out.getexif(), "metadata must not leave the server"
        assert (ref.mime_type, ref.label) == ("image/jpeg", "Reference photo 1 of Maya")


class _FakeGenAI:
    def __init__(self, parts=None, finish="STOP"):
        self.calls = []
        self.parts = parts
        self.finish = finish

    @property
    def models(self):
        return self

    def generate_content(self, model, contents, config):
        self.calls.append((model, contents, config))
        parts = self.parts if self.parts is not None else [
            SimpleNamespace(inline_data=SimpleNamespace(data=png(1344, 768), mime_type="image/png"))]
        return SimpleNamespace(
            candidates=[SimpleNamespace(content=SimpleNamespace(parts=parts), finish_reason=self.finish)],
            prompt_feedback=None)


class TestNanoBanana:
    def test_sends_labelled_references_then_the_prompt(self, monkeypatch):
        monkeypatch.delenv("YT_IMAGE_MODEL", raising=False)
        client = _FakeGenAI()
        port = NanoBananaImage(client=client)
        refs = [ImageInput(png(), "image/png", "Reference sheet for MAYA:"), ImageInput(png(), "image/png")]
        image = port.generate("Draw the bakery.", refs, "16:9")

        model, contents, config = client.calls[0]
        assert model == "gemini-3.1-flash-image"
        assert contents[0] == "Reference sheet for MAYA:"
        assert contents[-1] == "Draw the bakery."
        assert len(contents) == 4  # label, image, image (no label), prompt
        assert config.image_config.aspect_ratio == "16:9"
        assert config.response_modalities == ["IMAGE"]
        assert (image.width, image.height, image.mime_type, image.extension) == (1344, 768, "image/png", "png")

    def test_a_blocked_request_is_a_clear_error(self):
        port = NanoBananaImage(client=_FakeGenAI(parts=[SimpleNamespace(inline_data=None, text="no")],
                                                 finish="IMAGE_SAFETY"))
        with pytest.raises(ImageError, match="IMAGE_SAFETY"):
            port.generate("Draw.", [], "1:1")

    def test_validates_inputs_before_calling(self):
        client = _FakeGenAI()
        port = NanoBananaImage(client=client)
        with pytest.raises(ValueError):
            port.generate("", [], "16:9")
        with pytest.raises(ValueError):
            port.generate("Draw.", [], "4:3")
        with pytest.raises(ValueError):
            port.generate("Draw.", [ImageInput(b"x", "image/png")] * 15, "16:9")
        assert client.calls == []

    def test_price_can_be_overridden(self, monkeypatch):
        port = NanoBananaImage(client=_FakeGenAI())
        monkeypatch.delenv("YT_IMAGE_USD", raising=False)
        assert port.cost_usd(10) == pytest.approx(0.67)
        monkeypatch.setenv("YT_IMAGE_USD", "0.039")
        assert port.cost_usd(10) == pytest.approx(0.39)


class _FakeHTTP:
    def __init__(self, image_url):
        self.posts, self.gets = [], []
        self.image_url = image_url

    def post(self, url, json, headers):
        self.posts.append((url, json, headers))
        return SimpleNamespace(status_code=200, json=lambda: {"images": [{"url": self.image_url, "content_type": "image/png"}]},
                               text="")

    def get(self, url):
        self.gets.append(url)
        return SimpleNamespace(status_code=200, content=png(768, 1344), headers={"content-type": "image/png"})


class TestSeedream:
    def test_references_become_data_uris_and_labels_go_into_the_prompt(self, monkeypatch):
        monkeypatch.setenv("FAL_KEY", "fal-test")
        data_uri = "data:image/png;base64," + base64.b64encode(png(768, 1344)).decode()
        http = _FakeHTTP(data_uri)
        image = SeedreamImage(http=http).generate(
            "Draw Maya.", [ImageInput(b"abc", "image/jpeg", "Reference sheet for MAYA:")], "9:16")
        url, body, headers = http.posts[0]
        assert url == SeedreamImage.EDIT_URL
        assert headers == {"Authorization": "Key fal-test"}
        assert body["image_urls"] == ["data:image/jpeg;base64," + base64.b64encode(b"abc").decode()]
        assert body["prompt"].startswith("Image 1: Reference sheet for MAYA:")
        assert body["image_size"] == "portrait_16_9"
        assert (image.width, image.height) == (768, 1344)
        assert http.gets == []

    def test_text_only_uses_text_to_image_and_downloads_urls(self, monkeypatch):
        monkeypatch.setenv("FAL_KEY", "fal-test")
        http = _FakeHTTP("https://cdn.example/image.png")
        SeedreamImage(http=http).generate("A street at dawn.", [], "16:9")
        assert http.posts[0][0] == SeedreamImage.TEXT_URL
        assert "image_urls" not in http.posts[0][1]
        assert http.gets == ["https://cdn.example/image.png"]

    def test_errors(self, monkeypatch):
        monkeypatch.delenv("FAL_KEY", raising=False)
        with pytest.raises(ImageError, match="FAL_KEY"):
            SeedreamImage(http=_FakeHTTP("x")).generate("Draw.", [], "1:1")
        monkeypatch.setenv("FAL_KEY", "k")
        failing = _FakeHTTP("x")
        failing.post = lambda url, json, headers: SimpleNamespace(status_code=422, text="bad input", json=lambda: {})
        with pytest.raises(ImageError, match="422"):
            SeedreamImage(http=failing).generate("Draw.", [], "1:1")


class TestRegistry:
    @pytest.fixture(autouse=True)
    def clean(self, monkeypatch):
        monkeypatch.setattr(ImageRegistry, "_instances", {})
        monkeypatch.delenv("YT_IMAGE_PROVIDER", raising=False)

    def test_adapters_implement_the_port(self):
        for adapter in ImageRegistry.PROVIDERS.values():
            assert issubclass(adapter, ImagePort)
            assert adapter.max_characters <= adapter.max_references

    def test_defaults_to_nano_banana_and_switches_by_env(self, monkeypatch):
        assert isinstance(ImageRegistry.get(), NanoBananaImage)
        monkeypatch.setattr(ImageRegistry, "_instances", {})
        monkeypatch.setenv("YT_IMAGE_PROVIDER", "seedream")
        assert isinstance(ImageRegistry.get(), SeedreamImage)

    def test_unknown_provider(self, monkeypatch):
        with pytest.raises(ValueError, match="Unknown image provider"):
            ImageRegistry.get("midjourney")
        monkeypatch.setenv("YT_IMAGE_PROVIDER", "nope")
        with pytest.raises(ValueError, match="YT_IMAGE_PROVIDER"):
            ImageRegistry.default_provider()

    def test_missing_credentials(self, monkeypatch):
        monkeypatch.delenv("FAL_KEY", raising=False)
        assert SeedreamImage.missing_env() == ["FAL_KEY"]


def test_generated_image_extension():
    assert GeneratedImage(b"", "image/jpeg", 1, 1).extension == "jpg"


class TestRateLimits:
    def test_retries_rate_limits_with_increasing_waits_then_succeeds(self, monkeypatch):
        from youtube.images import with_rate_limit_retry

        monkeypatch.delenv("YT_IMAGE_MAX_RETRIES", raising=False)
        attempts, waits = [], []

        def call():
            attempts.append(1)
            if len(attempts) < 3:
                raise RuntimeError("429 RESOURCE_EXHAUSTED")
            return "image"

        assert with_rate_limit_retry(call, lambda e: "429" in str(e), sleep=waits.append) == "image"
        assert waits == [15, 30]

    def test_gives_up_after_the_retry_limit_with_a_clear_error(self, monkeypatch):
        from youtube.images import RateLimited, with_rate_limit_retry

        monkeypatch.setenv("YT_IMAGE_MAX_RETRIES", "2")
        waits = []

        def always_limited():
            raise RuntimeError("429")

        with pytest.raises(RateLimited, match="after 2 retries"):
            with_rate_limit_retry(always_limited, lambda e: True, sleep=waits.append)
        assert waits == [15, 30]

    def test_other_errors_are_not_retried(self):
        from youtube.images import with_rate_limit_retry

        waits = []
        with pytest.raises(ValueError):
            with_rate_limit_retry(lambda: (_ for _ in ()).throw(ValueError("bad prompt")),
                                  lambda e: False, sleep=waits.append)
        assert waits == []

    def test_nano_banana_retries_a_429_from_vertex(self, monkeypatch):
        import youtube.images as images

        monkeypatch.setattr(images.time, "sleep", lambda s: None)
        client = _FakeGenAI()
        real = client.generate_content
        failures = []

        def flaky(model, contents, config):
            if not failures:
                failures.append(1)
                error = RuntimeError("Resource has been exhausted")
                error.code = 429
                raise error
            return real(model, contents, config)

        client.generate_content = flaky
        image = NanoBananaImage(client=client).generate("Draw.", [], "16:9")
        assert image.width == 1344 and failures == [1]
