"""Screens: app screenshots a shot shows exactly as uploaded, framed in code, for step-by-step tutorials."""
import io

import pytest
from PIL import Image

import youtube.brand_assets as assets
import youtube.projects as projects
import youtube.render as render
import youtube.storyboard as storyboard
from tests.test_youtube_brand_assets import product
from tests.test_youtube_render import PaidAvatar, approved_project
from tests.test_youtube_storyboard import env, ready_project  # noqa: F401  (fixture)
from tests.test_youtube_storyboard_routes import api  # noqa: F401  (fixture)

SCREEN_BLUE = (20, 90, 220)


def screenshot(width=500, height=1000) -> bytes:
    """A phone screenshot: blue, with a white button across the middle."""
    image = Image.new("RGB", (width, height), SCREEN_BLUE)
    image.paste((255, 255, 255), (width // 4, height // 2 - 40, width * 3 // 4, height // 2 + 40))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def screen(env, name="Fund with USDC"):
    return assets.create_asset(env.db, "biz", name, "screen", screenshot(), env.storage)


class TestFrame:
    def test_the_whole_screenshot_is_shown_clear_of_the_captions(self):
        with Image.open(io.BytesIO(assets.screen_frame(screenshot(), 1080, 1920, "short"))) as image:
            image = image.convert("RGB")
            assert image.size == (1080, 1920)
            # The screenshot fits the box above the captions: 5% to 64% of the height, centred.
            top, bottom = int(1920 * 0.05), int(1920 * 0.64)
            assert image.getpixel((540, (top + bottom) // 2)) == (255, 255, 255), "the button is kept"
            assert image.getpixel((540, top + 40)) == SCREEN_BLUE, "the top of the screen is not cropped"
            assert image.getpixel((540, bottom - 40)) == SCREEN_BLUE, "nor the bottom"
            # Where the captions go, only the dark, blurred background.
            assert max(image.getpixel((540, int(1920 * 0.75)))) < 120

    def test_a_wide_screenshot_fits_a_horizontal_video(self):
        with Image.open(io.BytesIO(assets.screen_frame(screenshot(1600, 900), 1920, 1080, "long_form"))) as image:
            image = image.convert("RGB")
            assert image.size == (1920, 1080)
            middle = int(1080 * (0.05 + 0.80) / 2)
            assert image.getpixel((960, middle)) == (255, 255, 255)
            assert image.getpixel((300, middle)) == SCREEN_BLUE


class TestScreenShots:
    def test_framed_at_once_without_a_model(self, env):
        project, _, _ = ready_project(env)
        storyboard.generate_storyboard(env.db, project, env.storage, port=env.images)
        project = storyboard.approve_storyboard(env.db, project)
        shot = projects._shots(env.db, project)[1]
        drawn_key = shot.image_key
        env.images.calls.clear()

        usdc = screen(env)
        shot = assets.set_shot_screen(env.db, project, shot.id, usdc.id, env.storage)
        assert env.images.calls == [], "no image model is asked to draw a screen"
        assert shot.screen_asset_id == usdc.id and shot.image_key and shot.image_key != drawn_key
        assert not env.storage.exists(drawn_key)
        assert project.storyboard_approved_at is None
        with Image.open(io.BytesIO(env.storage.get(shot.image_key))) as image:
            assert image.size == (1920, 1080)

        # Redrawing it frames it again, still without the model.
        storyboard.generate_storyboard(env.db, project, env.storage, port=env.images, shot_ids=[shot.id])
        assert env.images.calls == [] and shot.image_key

        shot = assets.set_shot_screen(env.db, project, shot.id, None, env.storage)
        assert shot.screen_asset_id is None and shot.image_key is None, "back to a scene that needs drawing"

    def test_only_this_business_screens_can_be_shown(self, env):
        project, _, _ = ready_project(env)
        shot = projects._shots(env.db, project)[1]
        with pytest.raises(projects.ProjectError, match="screen assets"):
            assets.set_shot_screen(env.db, project, shot.id, product(env).id, env.storage)
        theirs = assets.create_asset(env.db, "other", "Theirs", "screen", screenshot(), env.storage)
        with pytest.raises(projects.ProjectError, match="screen assets"):
            assets.set_shot_screen(env.db, project, shot.id, theirs.id, env.storage)

    def test_need_no_cast_on_screen_and_cost_nothing(self, env):
        project, maya, leo = ready_project(env)
        usdc = screen(env)
        for shot in projects._shots(env.db, project):
            assets.set_shot_screen(env.db, project, shot.id, usdc.id, env.storage)
        for character in (maya, leo):
            for sheet in storyboard.character_images(env.db, character, "sheet"):
                sheet.approved = False
        env.db.commit()
        assert storyboard.storyboard_problems(env.db, project, env.images) == []
        summary = storyboard.storyboard_summary(env.db, project, projects._shots(env.db, project))
        assert summary["estimate_remaining_usd"] == 0 and summary["spent_usd"] == 0
        assert storyboard.approve_storyboard(env.db, project).storyboard_approved_at

    def test_a_character_line_over_a_screen_is_heard_not_animated(self, env):
        project = approved_project(env)
        shot = projects._shots(env.db, project)[1]
        assert shot.shot_type == "dialogue"
        assets.set_shot_screen(env.db, project, shot.id, screen(env).id, env.storage)
        items = render.timeline(projects._shots(env.db, project), PaidAvatar())
        assert items[1].talk_s == 0.0 and items[2].talk_s > 0

    def test_held_still_in_the_render(self, env, monkeypatch):
        from youtube.avatar import StillAvatar

        project = approved_project(env)
        first = projects._shots(env.db, project)[0]
        assets.set_shot_screen(env.db, project, first.id, screen(env).id, env.storage)
        stills = []

        def still(image, frames, out, settings, direction=0, pan=True):
            stills.append(pan)

        monkeypatch.setattr(render, "still_segment", still)
        monkeypatch.setattr(render, "join_and_finish", lambda segments, audio, captions, final, *a: final.write_bytes(b"mp4"))
        monkeypatch.setattr(render, "media_info", lambda path: {"duration_s": 1.0})
        monkeypatch.setattr(render, "thumbnail", lambda data, fmt: b"jpg")
        render.compose(env.db, project, env.storage, StillAvatar(), scale=0.1)
        assert stills[0] is False, "the screen is not panned, so none of it is cropped away"
        assert all(stills[1:]), "drawn scenes still are"

    def test_deleting_a_screen_takes_it_out_of_its_shots(self, env):
        project = approved_project(env)
        usdc = screen(env)
        shot = assets.set_shot_screen(env.db, project, projects._shots(env.db, project)[1].id, usdc.id, env.storage)
        project = storyboard.approve_storyboard(env.db, project)
        frame = shot.image_key

        assets.delete_asset(env.db, usdc, env.storage)
        env.db.refresh(shot)
        env.db.refresh(project)
        assert shot.screen_asset_id is None and shot.image_key is None
        assert not env.storage.exists(frame) and project.storyboard_approved_at is None


def test_screen_routes(api):
    from tests.test_youtube_render_routes import approved as approved_via_api
    c = api.client
    pid = approved_via_api(api)
    created = c.post("/youtube/assets", data={"name": "Fund with USDT", "kind": "screen"},
                     files={"file": ("wallet.png", screenshot(), "image/png")})
    assert created.status_code == 201 and created.json()["kind"] == "screen"
    usdt = created.json()

    def shot_in(detail, shot_id):
        return next(s for s in detail["shots"] if s["id"] == shot_id)

    shot = c.get(f"/youtube/projects/{pid}").json()["shots"][0]
    assert shot["screen_asset_id"] is None
    detail = c.patch(f"/youtube/projects/{pid}/shots/{shot['id']}", json={"screen_asset_id": usdt["id"]}).json()
    assert shot_in(detail, shot["id"])["screen_asset_id"] == usdt["id"] and shot_in(detail, shot["id"])["has_image"]
    assert not detail["storyboard"]["approved"]
    assert c.get(f"/youtube/projects/{pid}/shots/{shot['id']}/image").status_code == 200

    # Leaving the field out changes nothing; null goes back to a drawn scene.
    detail = c.patch(f"/youtube/projects/{pid}/shots/{shot['id']}", json={"sound": "soft hum"}).json()
    assert shot_in(detail, shot["id"])["screen_asset_id"] == usdt["id"]
    detail = c.patch(f"/youtube/projects/{pid}/shots/{shot['id']}", json={"screen_asset_id": None}).json()
    assert shot_in(detail, shot["id"])["screen_asset_id"] is None
    assert c.patch(f"/youtube/projects/{pid}/shots/{shot['id']}", json={"screen_asset_id": 999}).status_code == 400
