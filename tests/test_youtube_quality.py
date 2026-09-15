"""Anti-slop checks: YouTube details, storyboard images, and review tracking."""
import io
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

import youtube.metadata as metadata
import youtube.storyboard as storyboard
from tests.test_youtube_publisher import FakeWriter, project
from tests.test_youtube_storyboard import env, ready_project  # noqa: F401  (fixture)
from youtube.quality import GeminiSceneChecker, SceneChecker, border_issues, metadata_issues, scene_checker

SCRIPT = "MAYA: One more try.\nLEO: You said that yesterday. We opened in 2019."


class TestMetadataIssues:
    def test_clean_listing_has_no_issues(self):
        assert metadata_issues("One more try at the bakery", "Maya and Leo open in 2019.", ["bakery"], SCRIPT) == []

    def test_hype_promises_numbers_shouting_and_emoji_are_flagged(self):
        issues = metadata_issues(
            "WITNESS the EPIC showdown!", "Don't miss it 🔥. New shorts every week. Sold 10,000 loaves.",
            [], SCRIPT)
        text = " | ".join(issues)
        assert '"witness"' in text and '"don\'t miss"' in text
        assert "every week" in text
        assert "Numbers not in the script: 10,000" in text
        assert "Title shouts" in text and "Emoji" in text

    def test_invented_contact_details_are_flagged(self):
        issues = metadata_issues("One more try", "Order at hello@crumb.example today.", [], SCRIPT)
        assert any("Contact detail" in i for i in issues)

    def test_hype_words_inside_other_words_are_not_flagged(self):
        assert metadata_issues("Unwitnessed", "A delvery van.", [], SCRIPT) == []


class TestMetadataRewrite:
    def test_a_flagged_draft_is_rewritten_once_with_the_problems_named(self):
        llm = FakeWriter(None)
        replies = iter(['{"title": "Witness the epic showdown", "description": "Don\'t miss it.", "tags": []}',
                        '{"title": "One more try", "description": "Maya tries again.", "tags": ["bakery"]}'])
        llm.invoke = lambda prompt: (llm.prompts.append(prompt), SimpleNamespace(content=next(replies)))[1]
        draft = metadata.draft_metadata(project(), llm=llm, brand_context=lambda b: ("", ""))
        assert draft["title"] == "One more try" and draft["issues"] == []
        assert len(llm.prompts) == 2 and '"witness"' in llm.prompts[1]

    def test_a_clean_draft_costs_one_call_and_a_worse_rewrite_is_not_kept(self):
        clean = FakeWriter('{"title": "One more try", "description": "Maya tries again.", "tags": []}')
        metadata.draft_metadata(project(), llm=clean, brand_context=lambda b: ("", ""))
        assert len(clean.prompts) == 1

        worse = FakeWriter(None)
        replies = iter(['{"title": "Witness it", "description": "", "tags": []}',
                        '{"title": "WITNESS it!", "description": "Don\'t miss it 🔥", "tags": []}'])
        worse.invoke = lambda prompt: SimpleNamespace(content=next(replies))
        draft = metadata.draft_metadata(project(), llm=worse, brand_context=lambda b: ("", ""))
        assert draft["title"] == "Witness it" and len(draft["issues"]) == 1

    def test_saved_details_are_checked_on_every_read(self):
        p = project()
        p.video_title, p.video_description = "Witness this", "Plain."
        assert metadata.serialize_metadata(p)["issues"] == ['Hype phrases: "witness"']


def picture(width=400, height=700, bars=False) -> bytes:
    # Photo-like noise everywhere, so no edge is flat unless bars are drawn.
    image = Image.effect_noise((width, height), 60).convert("RGB")
    draw = ImageDraw.Draw(image)
    if bars:
        draw.rectangle((0, 0, width, 60), fill=(0, 0, 0))
        draw.rectangle((0, height - 60, width, height), fill=(0, 0, 0))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


class TestImageChecks:
    def test_letterbox_bars_are_found_and_flat_pictures_are_not_flagged(self):
        assert border_issues(picture()) == []
        assert border_issues(picture(bars=True)) == ["Flat bar or border along the top, bottom edge"]
        flat = io.BytesIO()
        Image.new("RGB", (300, 300), (20, 40, 60)).save(flat, format="PNG")
        assert border_issues(flat.getvalue()) == []

    def test_vision_verdicts_become_issues_and_failures_never_block(self):
        class Models:
            def __init__(self, text):
                self.text = text

            def generate_content(self, **kwargs):
                if self.text is None:
                    raise RuntimeError("quota")
                return SimpleNamespace(text=self.text)

        flagged = GeminiSceneChecker(client=SimpleNamespace(models=Models('{"text": true, "panels": false, "repeated_person": true}')))
        assert flagged.check(picture(), "image/png", ["MAYA"]) == ["Visible text in the picture",
                                                                    "The same person appears more than once"]
        broken = GeminiSceneChecker(client=SimpleNamespace(models=Models(None)))
        assert broken.check(picture(bars=True), "image/png", []) == ["Flat bar or border along the top, bottom edge"]

    def test_checker_selection(self, monkeypatch):
        monkeypatch.setenv("PROJECT_ID", "p")
        monkeypatch.delenv("YT_IMAGE_CHECK", raising=False)
        assert isinstance(scene_checker("nano_banana"), GeminiSceneChecker)
        assert type(scene_checker("seedream")) is SceneChecker
        monkeypatch.setenv("YT_IMAGE_CHECK", "off")
        assert scene_checker("nano_banana") is None


class ScriptedChecker(SceneChecker):
    def __init__(self, verdicts):
        self.verdicts, self.calls = list(verdicts), 0

    def check(self, image_bytes, mime_type, characters, products=()):
        self.calls += 1
        return self.verdicts.pop(0) if self.verdicts else []


class TestRedraw:
    def _shot(self, env):
        project, _, _ = ready_project(env)
        return project, storyboard.projects._shots(env.db, project)[1]

    def test_a_flagged_image_is_redrawn_once_with_the_problem_named(self, env):
        project, shot = self._shot(env)
        checker = ScriptedChecker([["Visible text in the picture"], []])
        storyboard.draw_shot(env.db, project, shot, env.storage, env.images, checker=checker)
        env.db.commit()
        assert checker.calls == 2 and len(env.images.calls) == 2
        assert "avoid them: Visible text in the picture" in env.images.calls[1].prompt
        assert shot.image_issues is None
        costs = env.db.query(storyboard.YTCost).filter_by(project_id=project.id, stage="image").count()
        assert costs == 2, "both paid attempts are costed"

    def test_issues_that_survive_the_retries_are_kept_on_the_shot(self, env, monkeypatch):
        monkeypatch.setenv("YT_IMAGE_CHECK_RETRIES", "1")
        project, shot = self._shot(env)
        checker = ScriptedChecker([["Blurred or filled strip along an edge"]] * 2)
        storyboard.draw_shot(env.db, project, shot, env.storage, env.images, checker=checker)
        env.db.commit()
        assert len(env.images.calls) == 2 and shot.image_issues == ["Blurred or filled strip along an edge"]
        summary = storyboard.storyboard_summary(env.db, project, storyboard.projects._shots(env.db, project))
        assert summary["images_flagged"] == 1


def test_upload_records_how_much_was_watched(env):
    from tests.test_youtube_publishing import FakePublisher, connected, rendered_project, with_metadata
    import youtube.publishing as publishing

    pub = FakePublisher()
    connected(env, pub)
    p, _ = rendered_project(env)
    with_metadata(env, p)
    upload = publishing.queue_upload(env.db, p, reviewed=True, publisher=pub, watched_seconds=71.46)
    assert upload.watched_seconds == 71.5 and publishing.serialize_upload(upload)["watched_seconds"] == 71.5
