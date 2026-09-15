"""Brand assets: product references in storyboard shots, and the end card."""
import io

import pytest
from PIL import Image

import youtube.brand_assets as assets
import youtube.projects as projects
import youtube.render as render
import youtube.storyboard as storyboard
from tests.test_youtube_quality import ScriptedChecker
from tests.test_youtube_render import approved_project, needs_ffmpeg
from tests.test_youtube_storyboard import env, photo, ready_project  # noqa: F401  (fixture)
from tests.test_youtube_storyboard_routes import api  # noqa: F401  (fixture)
from youtube.audio import Audio


def logo_png() -> bytes:
    image = Image.new("RGBA", (400, 200), (0, 0, 0, 0))
    image.paste((230, 30, 30, 255), (100, 50, 300, 150))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def product(env, name="Crumb Loaf"):
    return assets.create_asset(env.db, "biz", name, "product", photo(), env.storage)


class TestAssets:
    def test_create_validates_and_lists_per_business(self, env):
        loaf = product(env)
        logo = assets.create_asset(env.db, "biz", "Crumb logo", "logo", logo_png(), env.storage)
        assert [a.name for a in assets.list_assets(env.db, "biz")] == ["Crumb Loaf", "Crumb logo"]
        assert assets.list_assets(env.db, "other") == []
        with pytest.raises(projects.ProjectError, match="already exists"):
            product(env)
        with pytest.raises(projects.ProjectError, match="kind must be"):
            assets.create_asset(env.db, "biz", "X", "banner", photo(), env.storage)
        with pytest.raises(projects.ProjectError, match="readable image"):
            assets.create_asset(env.db, "biz", "Y", "product", b"nope", env.storage)
        assert env.storage.exists(loaf.storage_key) and logo.kind == "logo"

    def test_transparent_images_are_put_on_white(self):
        with Image.open(io.BytesIO(assets.flatten_on_white(logo_png()))) as image:
            assert image.mode == "RGB" and image.getpixel((5, 5)) == (255, 255, 255)
        assert assets.flatten_on_white(photo()) == photo()

    def test_deleting_an_asset_removes_it_everywhere_and_clears_approval(self, env):
        project, _, _ = ready_project(env)
        loaf = product(env)
        logo = assets.create_asset(env.db, "biz", "Crumb logo", "logo", logo_png(), env.storage)
        assets.set_project_products(env.db, project, [loaf.id])
        shot = projects._shots(env.db, project)[1]
        assets.set_shot_products(env.db, project, shot.id, [loaf.id])
        assets.set_end_card(env.db, project, {"enabled": True, "logo_asset_id": logo.id, "headline": "Order now"})
        storyboard.generate_storyboard(env.db, project, env.storage, port=env.images)
        project = storyboard.approve_storyboard(env.db, project)

        assets.delete_asset(env.db, loaf, env.storage)
        assets.delete_asset(env.db, logo, env.storage)
        env.db.refresh(project)
        env.db.refresh(shot)
        assert project.asset_ids == [] and shot.asset_ids == [] and project.end_card["logo_asset_id"] is None
        assert project.storyboard_approved_at is None
        assert not env.storage.exists(loaf.storage_key)


class TestProductsInShots:
    def test_shots_show_products_they_name_unless_a_person_chooses(self, env):
        project, _, _ = ready_project(env)
        loaf = product(env, "One more try")  # a product name that appears in MAYA's line
        muffin = product(env, "Muffin")
        assets.set_project_products(env.db, project, [loaf.id, muffin.id])
        shots = projects._shots(env.db, project)
        assert [p.name for p in assets.shot_products(env.db, project, shots[1])] == ["One more try"]
        assert assets.shot_products(env.db, project, shots[2]) == []

        assets.set_shot_products(env.db, project, shots[2].id, [muffin.id])
        assert [p.name for p in assets.shot_products(env.db, project, shots[2])] == ["Muffin"]
        assets.set_shot_products(env.db, project, shots[2].id, None)
        assert assets.shot_products(env.db, project, shots[2]) == []

        other = assets.create_asset(env.db, "biz", "Scone", "product", photo(), env.storage)
        with pytest.raises(projects.ProjectError, match="Add the product to the project first"):
            assets.set_shot_products(env.db, project, shots[2].id, [other.id])
        with pytest.raises(projects.ProjectError, match="product assets"):
            assets.set_project_products(env.db, project, [999])

    def test_a_product_shot_sends_its_photo_and_tells_the_model_and_the_checker(self, env):
        project, _, _ = ready_project(env)
        loaf = product(env)
        assets.set_project_products(env.db, project, [loaf.id])
        shot = projects._shots(env.db, project)[1]
        assets.set_shot_products(env.db, project, shot.id, [loaf.id])
        seen = {}

        class Checker(ScriptedChecker):
            def check(self, image_bytes, mime_type, characters, products=()):
                seen["products"] = list(products)
                return []

        storyboard.draw_shot(env.db, project, shot, env.storage, env.images, checker=Checker([]))
        call = env.images.calls[-1]
        assert "Product reference for Crumb Loaf (reproduce this exact product):" in call.labels
        assert "PRODUCTS (reproduce each exactly" in call.prompt and "- Crumb Loaf" in call.prompt
        assert "other than what is printed on the products themselves" in call.prompt
        assert seen["products"] == ["Crumb Loaf"]

    def test_references_never_exceed_the_provider_limit(self, env, monkeypatch):
        project, _, _ = ready_project(env)
        ids = [product(env, f"Item {i}").id for i in range(6)]
        assets.set_project_products(env.db, project, ids)
        shot = projects._shots(env.db, project)[1]
        assets.set_shot_products(env.db, project, shot.id, ids)
        products = assets.shot_products(env.db, project, shot)
        refs, _ = storyboard.scene_references(env.db, project, shot, env.storage, env.images, products)
        assert len(refs) == env.images.max_references


class TestEndCard:
    def test_validation(self, env):
        with pytest.raises(projects.ProjectError, match="colour"):
            assets.normalize_end_card(env.db, "biz", {"background": "red"})
        with pytest.raises(projects.ProjectError, match="needs a logo"):
            assets.normalize_end_card(env.db, "biz", {"enabled": True})
        loaf = product(env)
        with pytest.raises(projects.ProjectError, match="logo assets"):
            assets.normalize_end_card(env.db, "biz", {"enabled": True, "logo_asset_id": loaf.id})
        card = assets.normalize_end_card(env.db, "biz", {"enabled": True, "headline": "  Order   now ", "seconds": 30})
        assert card["headline"] == "Order now" and card["seconds"] == 6.0

    def test_image_is_the_frame_size_on_the_brand_colour_with_the_logo(self):
        card = {"enabled": True, "headline": "Order yours today", "url": "crumb.example", "background": "#0A3D62",
                "seconds": 3, "logo_asset_id": 1}
        with Image.open(io.BytesIO(assets.end_card_image(card, logo_png(), 540, 960))) as image:
            assert image.size == (540, 960)
            assert image.getpixel((5, 5)) == (10, 61, 98)
            colours = image.convert("RGB").getcolors(maxcolors=1_000_000)
            assert any(rgb == (230, 30, 30) for _, rgb in colours), "logo drawn"
            assert any(rgb == (255, 255, 255) for _, rgb in colours), "light text on a dark background"

    def test_changing_the_card_makes_a_render_out_of_date(self, env):
        project = approved_project(env)
        from datetime import datetime, timedelta, timezone
        from youtube.models import YTRender
        env.db.add(YTRender(project_id=project.id, format=project.format, status="completed", video_key="v.mp4",
                            end_card=None, created_at=datetime.now(timezone.utc) + timedelta(seconds=1)))
        projects.settle_status(env.db, project)
        env.db.commit()
        assert project.status == "rendered"
        assets.set_end_card(env.db, project, {"enabled": True, "headline": "Order now"})
        assert project.status == "storyboard_approved"
        assets.set_end_card(env.db, project, {"enabled": False, "headline": "Order now"})
        assert project.status == "rendered"


@needs_ffmpeg
def test_render_appends_the_end_card(env, monkeypatch):
    from youtube.avatar import StillAvatar
    from youtube.media import media_info

    monkeypatch.setenv("YT_RENDER_FPS", "10")
    monkeypatch.setenv("YT_RENDER_PRESET", "ultrafast")
    project = approved_project(env)
    assets.set_end_card(env.db, project, {"enabled": True, "headline": "Order now", "seconds": 2.5})
    assert render.estimate(env.db, project, StillAvatar())["end_card"] is True
    result = render.render_project(env.db, project, env.storage, StillAvatar(), scale=0.1)
    track = Audio.from_wav(env.storage.get(project.audio_key)).duration_s
    assert result.duration_s == pytest.approx(track + render.END_TAIL_S + 2.5, abs=0.2)
    assert result.end_card["headline"] == "Order now" and project.status == "rendered"


def test_brand_asset_routes(api):
    from tests.test_youtube_render_routes import approved as approved_via_api
    c = api.client
    pid = approved_via_api(api)
    style = c.post("/youtube/styles", json={"name": "Warm", "prompt": "Warm."}).json()
    c.patch(f"/youtube/projects/{pid}", json={"style_id": style["id"]})

    created = c.post("/youtube/assets", data={"name": "Crumb Loaf", "kind": "product"},
                     files={"file": ("loaf.jpg", photo(), "image/jpeg")})
    assert created.status_code == 201
    loaf = created.json()
    logo = c.post("/youtube/assets", data={"name": "Crumb", "kind": "logo"},
                  files={"file": ("logo.png", logo_png(), "image/png")}).json()
    assert c.post("/youtube/assets", data={"name": "Bad", "kind": "banner"},
                  files={"file": ("x.jpg", photo(), "image/jpeg")}).status_code == 400
    assert [a["name"] for a in c.get("/youtube/assets").json()["assets"]] == ["Crumb", "Crumb Loaf"]
    assert c.get(f"/youtube/assets/{loaf['id']}/image").status_code == 200

    detail = c.patch(f"/youtube/projects/{pid}", json={"asset_ids": [loaf["id"]]}).json()
    assert detail["asset_ids"] == [loaf["id"]] and detail["storyboard"]["style_id"] == style["id"], "style untouched"
    shot = detail["shots"][1]
    assert shot["asset_ids"] is None
    detail = c.patch(f"/youtube/projects/{pid}/shots/{shot['id']}", json={"asset_ids": [loaf["id"]]}).json()
    assert next(s for s in detail["shots"] if s["id"] == shot["id"])["products"] == [loaf["id"]]

    assert c.get(f"/youtube/projects/{pid}/end-card/preview").status_code == 404
    detail = c.patch(f"/youtube/projects/{pid}", json={"end_card": {
        "enabled": True, "headline": "Order today", "url": "crumb.example", "logo_asset_id": logo["id"],
        "background": "#0A3D62", "seconds": 3}}).json()
    assert detail["end_card"]["enabled"] and detail["storyboard"]["style_id"] == style["id"]
    preview = c.get(f"/youtube/projects/{pid}/end-card/preview")
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/png"
    with Image.open(io.BytesIO(preview.content)) as image:
        assert image.size == (960, 540)
    assert c.patch(f"/youtube/projects/{pid}", json={"end_card": {"enabled": True, "background": "blue"}}).status_code == 400

    api.user.business_id = "someone-else"
    assert c.get(f"/youtube/assets/{loaf['id']}/image").status_code == 404
    assert c.delete(f"/youtube/assets/{loaf['id']}").status_code == 404
