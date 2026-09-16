"""Series: episodes that continue one story, with inherited setup, recaps and shared locations."""
from types import SimpleNamespace

import pytest

import youtube.brand_assets as assets
import youtube.projects as projects
import youtube.series as series
import youtube.storyboard as storyboard
from tests.test_youtube_projects import FakeLLM
from tests.test_youtube_storyboard import env, photo, ready_project  # noqa: F401  (fixture)
from tests.test_youtube_storyboard_routes import api  # noqa: F401  (fixture)


class Recapper:
    """A planner that also answers the recap prompt."""

    def __init__(self):
        self.prompts = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        if prompt.startswith("Summarise this episode"):
            return SimpleNamespace(content="Maya kept the bakery open one more day. Leo is still doubtful.")
        return SimpleNamespace(content='{"shots": [], "speakers": {}}')


def second_project(env, name="gen-1"):
    """A second project from the same script, standing in for the next episode's script."""
    return projects.create_project(env.db, "biz", name, "long_form")


class TestSeries:
    def test_created_named_and_numbered(self, env):
        show = series.create_series(env.db, "biz", "  The Bakery ", "Two bakers, one oven, no money.")
        assert show.name == "The Bakery" and show.logline.startswith("Two bakers")
        with pytest.raises(projects.ProjectError, match="already exists"):
            series.create_series(env.db, "biz", "The Bakery")
        with pytest.raises(projects.ProjectError, match="Give the series a name"):
            series.create_series(env.db, "biz", "   ")
        assert series.get_series(env.db, "someone-else", show.id) is None

        first, _, _ = ready_project(env)
        series.add_to_series(env.db, first, show)
        second = second_project(env)
        series.add_to_series(env.db, second, show)
        assert (first.episode, second.episode) == (1, 2)
        assert [p.id for p in series.episodes(env.db, show)] == [first.id, second.id]
        assert series.previous_episode(env.db, second).id == first.id

    def test_deleting_a_series_keeps_its_episodes(self, env):
        show = series.create_series(env.db, "biz", "The Bakery")
        project, _, _ = ready_project(env)
        series.add_to_series(env.db, project, show)
        series.delete_series(env.db, show)
        assert project.series_id is None and project.episode is None
        assert projects.get_project(env.db, "biz", project.id) is not None


class TestInheritance:
    def test_a_new_episode_starts_from_the_last_one(self, env):
        show = series.create_series(env.db, "biz", "The Bakery")
        first, maya, leo = ready_project(env)
        style = storyboard.create_style(env.db, "biz", "Warm film", "Warm morning light.")
        storyboard.set_project_style(env.db, first, style.id)
        loaf = assets.create_asset(env.db, "biz", "Crumb Loaf", "product", photo(), env.storage)
        assets.set_project_products(env.db, first, [loaf.id])
        assets.set_end_card(env.db, first, {"enabled": True, "headline": "Order today"})
        series.add_to_series(env.db, first, show)

        second = second_project(env)
        series.add_to_series(env.db, second, show)
        assert second.style_id == style.id and second.asset_ids == [loaf.id]
        assert second.end_card["headline"] == "Order today"

        # The cast follows once the new episode has been planned and has speakers.
        projects.plan_project(env.db, second, llm=FakeLLM())
        cast = {row.speaker_label: row.character_id for row in projects._cast(env.db, second)}
        assert cast["MAYA"] == maya.id and cast["LEO"] == leo.id
        assert second.status in ("cast", "voiced", "storyboard_ready")

    def test_a_first_episode_inherits_nothing_and_keeps_its_own_choices(self, env):
        show = series.create_series(env.db, "biz", "The Bakery")
        first, _, _ = ready_project(env)
        style = storyboard.create_style(env.db, "biz", "Warm film", "Warm light.")
        storyboard.set_project_style(env.db, first, style.id)
        series.add_to_series(env.db, first, show)
        assert first.episode == 1 and first.style_id == style.id

        second = second_project(env)
        other = storyboard.create_style(env.db, "biz", "Noir", "Black and white.")
        storyboard.set_project_style(env.db, second, other.id)
        series.add_to_series(env.db, second, show)
        assert second.style_id == other.id, "a style already chosen is not overwritten"


class TestStorySoFar:
    def test_recaps_are_written_and_given_to_the_next_planner(self, env):
        show = series.create_series(env.db, "biz", "The Bakery", "Two bakers, one oven.")
        first, _, _ = ready_project(env)
        series.add_to_series(env.db, first, show)
        llm = Recapper()
        projects.plan_project(env.db, first, llm=llm)
        assert first.recap.startswith("Maya kept the bakery open")
        assert any(p.startswith("Summarise this episode") for p in llm.prompts)

        second = second_project(env)
        series.add_to_series(env.db, second, show)
        story = series.story_so_far(env.db, second)
        assert "Two bakers, one oven." in story and "Episode 1: Maya kept the bakery open" in story

        planner = Recapper()
        projects.plan_project(env.db, second, llm=planner)
        planning = next(p for p in planner.prompts if "STORY SO FAR" in p)
        assert "Episode 1: Maya kept the bakery open" in planning

    def test_a_stand_alone_project_has_no_story_and_no_recap(self, env):
        project, _, _ = ready_project(env)
        llm = Recapper()
        projects.plan_project(env.db, project, llm=llm)
        assert series.story_so_far(env.db, project) == "" and project.recap is None
        assert not any(p.startswith("Summarise this episode") for p in llm.prompts)

    def test_a_failed_recap_does_not_fail_the_episode(self, env):
        show = series.create_series(env.db, "biz", "The Bakery")
        project, _, _ = ready_project(env)
        series.add_to_series(env.db, project, show)

        class Broken(FakeLLM):
            def invoke(self, prompt):
                if prompt.startswith("Summarise this episode"):
                    raise RuntimeError("model down")
                return super().invoke(prompt)

        projects.plan_project(env.db, project, llm=Broken())
        assert project.recap is None and project.status in ("planned", "cast", "voiced", "storyboard_ready")


class TestLocations:
    def test_a_location_is_drawn_and_used_as_a_reference(self, env):
        project, _, _ = ready_project(env)
        place = assets.generate_asset_image(env.db, "biz", "The bakery", "location",
                                            "A small corner bakery at dawn", env.storage, env.images)
        assert place.kind == "location" and env.storage.exists(place.storage_key)
        assert "empty of people" in env.images.calls[-1].prompt
        assets.set_project_products(env.db, project, [place.id])
        shot = projects._shots(env.db, project)[0]
        assets.set_shot_products(env.db, project, shot.id, [place.id])

        storyboard.draw_shot(env.db, project, shot, env.storage, env.images, checker=None)
        call = env.images.calls[-1]
        assert "Location reference for The bakery (the same place):" in call.labels
        assert "LOCATION (the same place as in its reference photo" in call.prompt
        assert "PRODUCTS" not in call.prompt

    def test_drawing_needs_a_description(self, env):
        with pytest.raises(projects.ProjectError, match="Describe the place"):
            assets.generate_asset_image(env.db, "biz", "Nowhere", "location", "  ", env.storage, env.images)


def test_routes(api):
    c = api.client
    show = c.post("/youtube/series", json={"name": "The Bakery", "logline": "Two bakers, one oven."})
    assert show.status_code == 201
    sid = show.json()["id"]
    assert c.post("/youtube/series", json={"name": "The Bakery"}).status_code == 400
    assert c.get("/youtube/series").json()["series"][0]["episode_count"] == 0

    project = c.post("/youtube/projects", json={"generation_id": "gen-1", "format": "short", "series_id": sid}).json()
    assert project["series_id"] == sid and project["episode"] == 1
    detail = c.get(f"/youtube/series/{sid}").json()
    assert [e["episode"] for e in detail["episodes"]] == [1]

    second = c.post("/youtube/projects", json={"generation_id": "gen-1", "format": "short"}).json()
    moved = c.patch(f"/youtube/projects/{second['id']}", json={"series_id": sid}).json()
    assert moved["episode"] == 2
    assert c.patch(f"/youtube/projects/{second['id']}", json={"series_id": None}).json()["series_id"] is None

    drawn = c.post("/youtube/assets/draw", json={"name": "Storm Peak", "kind": "location",
                                                 "description": "A jagged black mountain summit above the clouds"})
    assert drawn.status_code == 201 and drawn.json()["kind"] == "location"
    assert c.post("/youtube/assets/draw", json={"name": "X", "kind": "location", "description": ""}).status_code == 422

    assert c.patch(f"/youtube/series/{sid}", json={"logline": "Two bakers, no money."}).json()["logline"] == "Two bakers, no money."
    assert c.delete(f"/youtube/series/{sid}").status_code == 204
    assert c.get(f"/youtube/projects/{project['id']}").json()["series_id"] is None
    api.user.business_id = "someone-else"
    assert c.get(f"/youtube/series/{sid}").status_code == 404
