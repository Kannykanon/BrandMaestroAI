"""Faces, character sheets, style locks and storyboards, on SQLite with a fake image provider."""
import io
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from PIL import Image

import youtube.projects as projects
import youtube.storyboard as storyboard
from tests.test_youtube_projects import FakeLLM, FakeVoice
from youtube.eligibility import EligibleScript
from youtube.images import GeneratedImage, ImageError, ImagePort, ImageRegistry
from youtube.models import YTCost, init_youtube_tables
from youtube.storage import LocalStorage
from youtube.voice import VoiceRegistry

SCRIPT = """INT. BAKERY - DAWN
The ovens were cold.

MAYA: One more try.
LEO: You said that yesterday.
"""


def photo(width=600, height=600) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (180, 140, 100)).save(out, format="JPEG")
    return out.getvalue()


class FakeImages(ImagePort):
    name = "fake_images"
    max_references = 5
    max_characters = 2
    usd_per_image = 0.05

    def __init__(self):
        self.calls = []
        self.fail_prompts_containing = None

    def generate(self, prompt, references, aspect_ratio):
        self._check(prompt, references, aspect_ratio)
        self.calls.append(SimpleNamespace(prompt=prompt, labels=[r.label for r in references], aspect=aspect_ratio))
        if self.fail_prompts_containing and self.fail_prompts_containing in prompt:
            raise ImageError("blocked by safety filter")
        width, height = {"16:9": (1344, 768), "9:16": (768, 1344), "1:1": (1024, 1024)}[aspect_ratio]
        out = io.BytesIO()
        Image.new("RGB", (width, height), (20, 40, 60)).save(out, format="PNG")
        return GeneratedImage(out.getvalue(), "image/png", width, height)


@pytest.fixture
def env(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite://")
    assert init_youtube_tables(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()

    monkeypatch.setattr(VoiceRegistry, "_instances", {"fake": FakeVoice()})
    monkeypatch.setitem(VoiceRegistry.PROVIDERS, "fake", FakeVoice)
    images = FakeImages()
    monkeypatch.setattr(ImageRegistry, "_instances", {"fake_images": images})
    monkeypatch.setitem(ImageRegistry.PROVIDERS, "fake_images", FakeImages)
    monkeypatch.setenv("YT_IMAGE_PROVIDER", "fake_images")

    script = EligibleScript("gen-1", "The bakery", SCRIPT, "human", 9.0, datetime(2026, 9, 15, tzinfo=timezone.utc))
    monkeypatch.setattr(projects, "get_eligible_script", lambda db, b, g: script if (b, g) == ("biz", "gen-1") else None)

    storage = LocalStorage(root=str(tmp_path / "store"))
    yield SimpleNamespace(db=db, images=images, storage=storage)
    db.close()


def character_with_sheet(env, name, voice, approve=True):
    character = projects.create_character(env.db, "biz", name, voice, "fake", style_notes=f"{name} notes")
    storyboard.confirm_rights(env.db, character, True)
    storyboard.add_face(env.db, character, photo(), env.storage)
    storyboard.start_sheet(env.db, character)
    storyboard.generate_sheet(env.db, character, env.storage, port=env.images)
    if approve:
        storyboard.approve_sheet(env.db, character)
    return character


def ready_project(env, fmt="long_form", voiced=True):
    project = projects.create_project(env.db, "biz", "gen-1", fmt)
    project = projects.plan_project(env.db, project, llm=FakeLLM(), storage=env.storage)
    narrator = projects.create_character(env.db, "biz", "Narrator", "deep", "fake")
    maya = character_with_sheet(env, "Maya", "warm")
    leo = character_with_sheet(env, "Leo", "bright")
    project = projects.set_cast(env.db, project, {"NARRATOR": narrator.id, "MAYA": maya.id, "LEO": leo.id})
    if voiced:
        project = projects.voice_project(env.db, project, env.storage)
    env.images.calls.clear()
    return project, maya, leo


class TestFaces:
    def test_upload_requires_confirmed_rights(self, env):
        character = projects.create_character(env.db, "biz", "Maya")
        with pytest.raises(projects.ProjectError, match="right to use this face"):
            storyboard.add_face(env.db, character, photo(), env.storage)
        storyboard.confirm_rights(env.db, character, True)
        storyboard.add_face(env.db, character, photo(), env.storage)
        assert len(storyboard.character_images(env.db, character, "upload")) == 1

    def test_bad_images_and_too_many_faces_are_rejected(self, env):
        character = projects.create_character(env.db, "biz", "Maya")
        storyboard.confirm_rights(env.db, character, True)
        with pytest.raises(projects.ProjectError, match="readable image"):
            storyboard.add_face(env.db, character, b"hello", env.storage)
        for _ in range(storyboard.MAX_FACE_UPLOADS):
            storyboard.add_face(env.db, character, photo(), env.storage)
        with pytest.raises(projects.ProjectError, match="at most"):
            storyboard.add_face(env.db, character, photo(), env.storage)

    def test_files_are_stored_under_the_business(self, env):
        character = projects.create_character(env.db, "biz", "Maya")
        storyboard.confirm_rights(env.db, character, True)
        face = storyboard.add_face(env.db, character, photo(), env.storage)
        assert face.storage_key.startswith(f"businesses/biz/characters/{character.id}/face-")
        assert env.storage.exists(face.storage_key)


class TestSheets:
    def test_sheet_is_generated_from_faces_and_needs_approval(self, env):
        character = character_with_sheet(env, "Maya", "warm", approve=False)
        call = env.images.calls[-1]
        assert call.aspect == "16:9"
        assert call.labels == ["Reference photo 1 of Maya"]
        assert "Maya notes" in call.prompt and "No text" in call.prompt
        assert character.sheet_status == "ready"
        assert storyboard.approved_sheet(env.db, character) is None
        storyboard.approve_sheet(env.db, character)
        assert character.sheet_status == "approved"
        assert storyboard.approved_sheet(env.db, character) is not None

    def test_regenerating_replaces_the_old_sheet(self, env):
        character = character_with_sheet(env, "Maya", "warm")
        old_key = storyboard.approved_sheet(env.db, character).storage_key
        storyboard.start_sheet(env.db, character)
        storyboard.generate_sheet(env.db, character, env.storage, port=env.images)
        sheets = storyboard.character_images(env.db, character, "sheet")
        assert len(sheets) == 1 and not sheets[0].approved
        assert not env.storage.exists(old_key)

    def test_a_new_face_makes_the_sheet_need_approval_again(self, env):
        character = character_with_sheet(env, "Maya", "warm")
        storyboard.add_face(env.db, character, photo(), env.storage)
        assert character.sheet_status == "ready"
        assert storyboard.approved_sheet(env.db, character) is None

    def test_sheet_needs_rights_and_a_face_and_is_not_started_twice(self, env):
        character = projects.create_character(env.db, "biz", "Maya")
        with pytest.raises(projects.ProjectError, match="right"):
            storyboard.start_sheet(env.db, character)
        storyboard.confirm_rights(env.db, character, True)
        with pytest.raises(projects.ProjectError, match="face photo"):
            storyboard.start_sheet(env.db, character)
        storyboard.add_face(env.db, character, photo(), env.storage)
        storyboard.start_sheet(env.db, character)
        with pytest.raises(projects.ProjectError, match="already"):
            storyboard.start_sheet(env.db, character)

    def test_failure_is_recorded_on_the_character(self, env):
        character = projects.create_character(env.db, "biz", "Maya")
        storyboard.fail_sheet(env.db, character, "Sheet generation failed: quota")
        data = projects.serialize_character(character, env.db)
        assert (data["sheet_status"], data["sheet_error"]) == ("failed", "Sheet generation failed: quota")


class TestStoryboard:
    def test_problems_are_listed_before_drawing(self, env):
        project = projects.create_project(env.db, "biz", "gen-1", "long_form")
        assert storyboard.storyboard_problems(env.db, project, env.images) == ["Plan the shots first"]
        project = projects.plan_project(env.db, project, llm=FakeLLM(), storage=env.storage)
        maya = character_with_sheet(env, "Maya", "warm", approve=False)
        projects.set_cast(env.db, project, {"MAYA": maya.id})
        problems = storyboard.storyboard_problems(env.db, project, env.images)
        assert "Maya (MAYA) needs an approved character sheet" in problems
        assert "LEO is on screen but has no character" in problems

    def test_withdrawn_rights_block_drawing(self, env):
        project, maya, _ = ready_project(env)
        storyboard.confirm_rights(env.db, maya, False)
        assert any("right to use this face" in p for p in storyboard.storyboard_problems(env.db, project, env.images))

    def test_draws_every_shot_with_the_right_references_and_framing(self, env):
        project, _, _ = ready_project(env, fmt="short")
        project = storyboard.generate_storyboard(env.db, project, env.storage, port=env.images)

        assert project.status == "storyboard_ready"
        assert len(env.images.calls) == 3
        narration, maya_shot, leo_shot = env.images.calls
        assert all(c.aspect == "9:16" for c in env.images.calls)
        assert narration.labels == [] and "no specific characters" in narration.prompt
        assert maya_shot.labels[0] == "Reference sheet for MAYA (Maya):"
        assert "Maya is speaking" in maya_shot.prompt
        assert "vertical" in maya_shot.prompt
        assert 'the line spoken over this shot is: "One more try."' in maya_shot.prompt
        assert env.db.query(YTCost).filter(YTCost.stage == "image").count() == 3

        data = projects.serialize_project(env.db, project)
        assert data["storyboard"]["images_done"] == 3
        assert data["storyboard"]["spent_usd"] == pytest.approx(0.15)

    def test_style_lock_goes_into_every_prompt_with_its_reference(self, env):
        project, _, _ = ready_project(env)
        style = storyboard.create_style(env.db, "biz", "Warm film", "Kodak Portra film look, warm tones.")
        storyboard.set_style_reference(env.db, style, photo(), env.storage)
        storyboard.set_project_style(env.db, project, style.id)
        storyboard.generate_storyboard(env.db, project, env.storage, port=env.images)
        assert all("Kodak Portra" in c.prompt for c in env.images.calls)
        assert all(c.labels[-1].startswith("Style reference") for c in env.images.calls)

    def test_characters_beyond_the_provider_limit_are_dropped_speaker_first(self, env):
        project, _, _ = ready_project(env)
        shot = next(s for s in projects._shots(env.db, project) if s.speaker_label == "LEO")
        shot.characters = ["MAYA", "NARRATOR", "LEO", "MAYA"]
        assert storyboard.on_screen(shot, 1) == ["LEO"]
        assert storyboard.on_screen(shot, 5) == ["LEO", "MAYA", "MAYA"]

    def test_one_failed_shot_does_not_stop_the_others(self, env):
        project, _, _ = ready_project(env)
        env.images.fail_prompts_containing = "One more try."
        project = storyboard.generate_storyboard(env.db, project, env.storage, port=env.images)
        shots = projects._shots(env.db, project)
        assert [bool(s.image_key) for s in shots] == [True, False, True]
        assert shots[1].image_error == "blocked by safety filter"
        assert "shots 2" in project.error
        assert project.status == "voiced"  # not every shot has an image yet

        env.images.fail_prompts_containing = None
        project = storyboard.generate_storyboard(env.db, project, env.storage, port=env.images)
        assert len(env.images.calls) == 4, "only the missing shot is redrawn"
        assert project.status == "storyboard_ready" and project.error is None

    def test_redrawing_one_shot_replaces_its_image(self, env):
        project, _, _ = ready_project(env)
        storyboard.generate_storyboard(env.db, project, env.storage, port=env.images)
        shot = projects._shots(env.db, project)[0]
        old = shot.image_key
        storyboard.generate_storyboard(env.db, project, env.storage, port=env.images, shot_ids=[shot.id])
        assert shot.image_key != old and not env.storage.exists(old)

    def test_generate_refuses_when_not_ready(self, env):
        project = projects.plan_project(env.db, projects.create_project(env.db, "biz", "gen-1", "long_form"),
                                        llm=FakeLLM())
        with pytest.raises(projects.ProjectError, match="has no character"):
            storyboard.generate_storyboard(env.db, project, env.storage, port=env.images)


class TestApproval:
    def test_needs_every_image_and_the_voice_track(self, env):
        project, _, _ = ready_project(env, voiced=False)
        with pytest.raises(projects.ProjectError, match="without an image"):
            storyboard.approve_storyboard(env.db, project)
        storyboard.generate_storyboard(env.db, project, env.storage, port=env.images)
        with pytest.raises(projects.ProjectError, match="Voice the script"):
            storyboard.approve_storyboard(env.db, project)
        projects.voice_project(env.db, project, env.storage)
        project = storyboard.approve_storyboard(env.db, project)
        assert project.status == "storyboard_approved"

    @pytest.mark.parametrize("change", [
        "visual", "shot_type", "recast", "speaker", "style", "sheet", "redraw", "revoice",
    ])
    def test_any_change_to_the_storyboard_clears_approval(self, env, change):
        project, maya, leo = ready_project(env)
        storyboard.generate_storyboard(env.db, project, env.storage, port=env.images)
        project = storyboard.approve_storyboard(env.db, project)
        shots = projects._shots(env.db, project)
        maya_shot = next(s for s in shots if s.speaker_label == "MAYA")

        if change == "visual":
            storyboard.update_shot(env.db, project, maya_shot.id, visual="Maya at the window, hopeful.")
        elif change == "shot_type":
            storyboard.update_shot(env.db, project, maya_shot.id, shot_type="cutaway")
        elif change == "recast":
            other = character_with_sheet(env, "Maya Two", "warm")
            projects.set_cast(env.db, project, {"MAYA": other.id})
        elif change == "speaker":
            projects.reassign_speaker(env.db, project, maya_shot.id, "LEO")
        elif change == "style":
            style = storyboard.create_style(env.db, "biz", "Noir", "Black and white noir.")
            storyboard.set_project_style(env.db, project, style.id)
        elif change == "sheet":
            storyboard.add_face(env.db, maya, photo(), env.storage)
        elif change == "redraw":
            storyboard.generate_storyboard(env.db, project, env.storage, port=env.images, shot_ids=[maya_shot.id])
        elif change == "revoice":
            projects.voice_project(env.db, project, env.storage, force=True)

        env.db.refresh(project)
        assert project.storyboard_approved_at is None
        assert project.status != "storyboard_approved"

    def test_viewing_or_voicing_missing_shots_keeps_approval(self, env):
        project, _, _ = ready_project(env)
        storyboard.generate_storyboard(env.db, project, env.storage, port=env.images)
        project = storyboard.approve_storyboard(env.db, project)
        projects.serialize_project(env.db, project)
        projects.voice_project(env.db, project, env.storage)  # nothing missing: nothing changes
        assert project.status == "storyboard_approved"


class TestShotEdits:
    def test_narrator_and_character_shot_type_rules(self, env):
        project, _, _ = ready_project(env)
        narration, maya_shot, _ = projects._shots(env.db, project)
        with pytest.raises(projects.ProjectError, match="always narration"):
            storyboard.update_shot(env.db, project, narration.id, shot_type="dialogue")
        with pytest.raises(projects.ProjectError, match="cannot be a narration"):
            storyboard.update_shot(env.db, project, maya_shot.id, shot_type="narration")
        with pytest.raises(projects.ProjectError, match="Describe"):
            storyboard.update_shot(env.db, project, maya_shot.id, visual="   ")
        before = maya_shot.text
        storyboard.update_shot(env.db, project, maya_shot.id, visual="Close on Maya.", shot_type="two_character")
        assert (maya_shot.visual_prompt, maya_shot.shot_type, maya_shot.text) == (
            "Close on Maya.", "two_character", before)


class TestStyles:
    def test_crud_and_deleting_unsets_projects(self, env):
        project, _, _ = ready_project(env)
        style = storyboard.create_style(env.db, "biz", "Warm", "Warm light.")
        storyboard.set_project_style(env.db, project, style.id)
        storyboard.update_style(env.db, style, "Warmer", None)
        assert storyboard.serialize_style(style) == {"id": style.id, "name": "Warmer", "prompt": "Warm light.",
                                                     "has_reference": False, "reference_version": None}
        storyboard.delete_style(env.db, style, env.storage)
        env.db.refresh(project)
        assert project.style_id is None

    def test_styles_are_per_business(self, env):
        project, _, _ = ready_project(env)
        foreign = storyboard.create_style(env.db, "other-biz", "Theirs", "Their look.")
        with pytest.raises(projects.ProjectError, match="No such style"):
            storyboard.set_project_style(env.db, project, foreign.id)
        assert storyboard.list_styles(env.db, "biz") == []
