"""Project flow: create from an approved script, plan, cast, voice.

Runs on an in-memory SQLite database with the yt_ tables, a fake voice
provider and local storage, so the whole flow is exercised without a model,
network or Postgres.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, inspect
from sqlalchemy.orm import sessionmaker

import youtube.projects as projects
from youtube.audio import SAMPLE_RATE, Audio
from youtube.eligibility import EligibleScript
from youtube.models import YTCost, YTModel, init_youtube_tables
from youtube.storage import LocalStorage
from youtube.voice import Voice, VoicePort, VoiceRegistry

SCRIPT = """INT. SHOP - NIGHT
The doors opened.

MAYA: We did it.
LEO: Did we?
MAYA: Look outside.
"""


class FakeVoice(VoicePort):
    name = "fake"
    VOICES = ("warm", "deep", "bright")

    def __init__(self):
        self.calls = []

    def list_voices(self):
        return [Voice(v, v.title(), "en-us", None, self.name) for v in self.VOICES]

    def synthesize(self, text, voice_id, speed=1.0):
        self.calls.append((text, voice_id))
        # One second per word keeps durations easy to check.
        return Audio(np.full(SAMPLE_RATE * len(text.split()), 0.1, dtype=np.float32))


class FakeLLM:
    def invoke(self, prompt):
        return SimpleNamespace(content='{"shots": [], "speakers": {"MAYA": "founder"}}')


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine("sqlite://")
    assert init_youtube_tables(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()

    voice = FakeVoice()
    monkeypatch.setattr(VoiceRegistry, "_instances", {"fake": voice})
    monkeypatch.setitem(VoiceRegistry.PROVIDERS, "fake", FakeVoice)

    scripts = {
        ("biz", "gen-1"): EligibleScript("gen-1", "The launch", SCRIPT, "human", 9.0,
                                         datetime(2026, 9, 14, tzinfo=timezone.utc)),
    }
    monkeypatch.setattr(projects, "get_eligible_script", lambda db, b, g: scripts.get((b, g)))

    storage = LocalStorage(root=str(tmp_path / "store"))
    yield SimpleNamespace(db=db, voice=voice, storage=storage, scripts=scripts, engine=engine)
    db.close()


def planned_project(env, fmt="long_form"):
    project = projects.create_project(env.db, "biz", "gen-1", fmt)
    return projects.plan_project(env.db, project, llm=FakeLLM(), storage=env.storage)


def cast_everyone(env, project):
    ids = {}
    for name, voice in (("Narrator", "deep"), ("Maya", "warm"), ("Leo", "bright")):
        ids[name.upper()] = projects.create_character(env.db, "biz", name, voice, "fake").id
    return projects.set_cast(env.db, project, ids)


class TestCreate:
    def test_snapshots_the_approved_script(self, env):
        project = projects.create_project(env.db, "biz", "gen-1", "short")
        assert (project.status, project.approval_label, project.format) == ("draft", "human", "short")
        env.scripts[("biz", "gen-1")].content = "changed later in marketing"
        assert project.script_snapshot == SCRIPT

    @pytest.mark.parametrize("business, generation", [("biz", "missing"), ("other-biz", "gen-1")])
    def test_refuses_scripts_that_are_not_eligible_for_this_business(self, env, business, generation):
        with pytest.raises(projects.ProjectError, match="not approved"):
            projects.create_project(env.db, business, generation, "long_form")

    def test_refuses_unknown_formats(self, env):
        with pytest.raises(projects.ProjectError, match="format"):
            projects.create_project(env.db, "biz", "gen-1", "square")


class TestPlan:
    def test_creates_shots_and_one_cast_row_per_speaker(self, env):
        project = planned_project(env)
        data = projects.serialize_project(env.db, project)
        assert project.status == "planned"
        assert [(s["speaker"], s["text"]) for s in data["shots"]] == [
            ("NARRATOR", "The doors opened."), ("MAYA", "We did it."),
            ("LEO", "Did we?"), ("MAYA", "Look outside.")]
        assert {c["speaker"] for c in data["cast"]} == {"NARRATOR", "MAYA", "LEO"}
        assert next(c for c in data["cast"] if c["speaker"] == "MAYA")["description"] == "founder"
        assert data["shots"][0]["visual"] == "INT. SHOP - NIGHT"

    def test_replanning_keeps_cast_assignments(self, env):
        project = cast_everyone(env, planned_project(env))
        assert project.status == "cast"
        project = projects.plan_project(env.db, project, llm=FakeLLM(), storage=env.storage)
        assert project.status == "cast"

    def test_a_script_with_nothing_to_say_fails_the_project(self, env):
        env.scripts[("biz", "gen-2")] = EligibleScript("gen-2", "t", "# Title\n[Black]", "human", 9, None)
        project = projects.create_project(env.db, "biz", "gen-2", "long_form")
        with pytest.raises(projects.ProjectError):
            projects.plan_project(env.db, project, llm=FakeLLM())
        assert project.status == "failed" and "without changing it" in project.error


class TestCast:
    def test_status_becomes_cast_only_when_every_speaker_has_a_voice(self, env):
        project = planned_project(env)
        maya = projects.create_character(env.db, "biz", "Maya", "warm", "fake")
        silent = projects.create_character(env.db, "biz", "Leo")  # no voice yet
        project = projects.set_cast(env.db, project, {"MAYA": maya.id, "LEO": silent.id})
        assert project.status == "planned"
        assert projects.cast_ready(env.db, project) is False

    def test_rejects_unknown_speakers_and_other_businesses_characters(self, env):
        project = planned_project(env)
        foreign = projects.create_character(env.db, "other-biz", "Maya", "warm", "fake")
        with pytest.raises(projects.ProjectError, match="No speaker"):
            projects.set_cast(env.db, project, {"GHOST": None})
        with pytest.raises(projects.ProjectError, match="No character"):
            projects.set_cast(env.db, project, {"MAYA": foreign.id})

    def test_character_voices_are_checked_against_the_provider(self, env):
        with pytest.raises(projects.ProjectError, match="not available"):
            projects.create_character(env.db, "biz", "Maya", "does-not-exist", "fake")

    def test_character_names_are_unique_per_business(self, env):
        projects.create_character(env.db, "biz", "Maya")
        projects.create_character(env.db, "other-biz", "Maya")
        with pytest.raises(projects.ProjectError, match="already exists"):
            projects.create_character(env.db, "biz", "Maya")


class TestVoice:
    def test_voices_every_shot_in_its_characters_voice_and_joins_a_track(self, env):
        project = cast_everyone(env, planned_project(env))
        project = projects.voice_project(env.db, project, env.storage)

        assert project.status == "voiced"
        assert env.voice.calls == [("The doors opened.", "deep"), ("We did it.", "warm"),
                                   ("Did we?", "bright"), ("Look outside.", "warm")]
        # 3 + 3 + 2 + 2 seconds of speech, three speaker changes of 0.6 s.
        assert project.audio_duration_s == pytest.approx(10 + 3 * projects.GAP_SPEAKER_CHANGE_S)
        track = Audio.from_wav(env.storage.get(project.audio_key))
        assert track.duration_s == pytest.approx(project.audio_duration_s, abs=0.01)
        assert project.audio_key.startswith("businesses/biz/projects/")
        assert env.db.query(YTCost).count() == 4

    def test_revoicing_only_does_missing_shots(self, env):
        project = projects.voice_project(env.db, cast_everyone(env, planned_project(env)), env.storage)
        env.voice.calls.clear()
        projects.voice_project(env.db, project, env.storage)
        assert env.voice.calls == []
        projects.voice_project(env.db, project, env.storage, force=True)
        assert len(env.voice.calls) == 4

    def test_recasting_a_speaker_discards_only_their_audio(self, env):
        project = projects.voice_project(env.db, cast_everyone(env, planned_project(env)), env.storage)
        new_leo = projects.create_character(env.db, "biz", "Leo Two", "deep", "fake")
        project = projects.set_cast(env.db, project, {"LEO": new_leo.id})
        assert project.audio_key is None and project.status == "cast"
        env.voice.calls.clear()
        projects.voice_project(env.db, project, env.storage)
        assert env.voice.calls == [("Did we?", "deep")]

    def test_refuses_to_voice_before_casting(self, env):
        with pytest.raises(projects.ProjectError, match="character with a voice"):
            projects.voice_project(env.db, planned_project(env), env.storage)


class TestShotSpeaker:
    def test_reassigning_changes_speaker_not_words_and_updates_the_cast(self, env):
        project = cast_everyone(env, planned_project(env))
        shot = next(s for s in projects._shots(env.db, project) if s.speaker_label == "LEO")
        shot = projects.reassign_speaker(env.db, project, shot.id, "Stranger")
        assert (shot.speaker_label, shot.text, shot.shot_type) == ("STRANGER", "Did we?", "dialogue")
        speakers = {c.speaker_label for c in projects._cast(env.db, project)}
        assert speakers == {"NARRATOR", "MAYA", "STRANGER"}  # LEO no longer speaks
        assert project.status == "planned"                   # STRANGER has no character yet

    def test_reassigning_to_narrator_makes_it_narration(self, env):
        project = planned_project(env)
        shot = next(s for s in projects._shots(env.db, project) if s.speaker_label == "LEO")
        shot = projects.reassign_speaker(env.db, project, shot.id, "VO")
        assert (shot.speaker_label, shot.shot_type, shot.characters) == ("NARRATOR", "narration", [])


class TestGuards:
    def test_busy_projects_refuse_changes(self, env):
        project = planned_project(env)
        projects.mark_busy(env.db, project, "voicing")
        with pytest.raises(projects.ProjectError, match="voicing"):
            projects.mark_busy(env.db, project, "planning")
        with pytest.raises(projects.ProjectError, match="voicing"):
            projects.set_cast(env.db, project, {})

    def test_lookups_are_scoped_to_the_business(self, env):
        project = planned_project(env)
        character = projects.create_character(env.db, "biz", "Maya")
        assert projects.get_project(env.db, "other-biz", project.id) is None
        assert projects.get_character(env.db, "other-biz", character.id) is None
        assert projects.list_projects(env.db, "other-biz") == []

    def test_short_that_runs_too_long_is_warned_not_trimmed(self, env):
        long_script = " ".join(["word"] * 600) + "."
        env.scripts[("biz", "gen-long")] = EligibleScript("gen-long", "t", long_script, "human", 9, None)
        project = projects.create_project(env.db, "biz", "gen-long", "short")
        project = projects.plan_project(env.db, project, llm=FakeLLM())
        data = projects.serialize_project(env.db, project)
        assert data["warnings"] and "never trimmed" in data["warnings"][0]
        assert sum(len(s["text"].split()) for s in data["shots"]) == 600

    def test_deleting_a_project_removes_its_audio(self, env):
        project = projects.voice_project(env.db, cast_everyone(env, planned_project(env)), env.storage)
        keys = [project.audio_key] + [s.audio_key for s in projects._shots(env.db, project)]
        projects.delete_project(env.db, project, env.storage)
        assert not any(env.storage.exists(k) for k in keys)
        assert projects.get_project(env.db, "biz", project.id) is None


def test_existing_tables_get_new_columns(tmp_path):
    """Phase 0 shipped yt_shots without these columns; startup must add them."""
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    old = MetaData()
    Table("yt_shots", old, Column("id", Integer, primary_key=True), Column("text", String))
    old.create_all(engine)

    assert init_youtube_tables(engine)
    columns = {c["name"] for c in inspect(engine).get_columns("yt_shots")}
    assert {"delivery", "characters"} <= columns
    assert init_youtube_tables(engine), "running again must be harmless"


class TestConcurrentStartup:
    """The API starts several worker processes at once, and each runs the migration."""

    def test_postgres_column_adds_cannot_collide(self):
        from youtube.models import add_column_ddl

        assert add_column_ddl("postgresql", "yt_shots", "delivery", "TEXT") == \
            "ALTER TABLE yt_shots ADD COLUMN IF NOT EXISTS delivery TEXT"
        assert add_column_ddl("sqlite", "yt_shots", "delivery", "TEXT") == \
            "ALTER TABLE yt_shots ADD COLUMN delivery TEXT"
        with pytest.raises(ValueError):
            add_column_ddl("postgresql", "generations", "approved", "BOOLEAN")

    def test_a_column_added_by_another_process_is_not_an_error(self, tmp_path, monkeypatch):
        import youtube.models as models

        engine = create_engine(f"sqlite:///{tmp_path / 'race.db'}")
        assert init_youtube_tables(engine)

        # Simulate the race: this process saw the column missing, but another
        # process added it before this process's ALTER ran.
        real = models._column_names
        seen = {"first": True}

        def stale_then_real(eng, table):
            names = real(eng, table)
            if table == "yt_shots" and seen["first"]:
                seen["first"] = False
                return names - {"delivery"}
            return names

        monkeypatch.setattr(models, "_column_names", stale_then_real)
        assert init_youtube_tables(engine) is True

    def test_a_genuine_failure_is_still_reported(self, tmp_path, monkeypatch):
        import youtube.models as models

        engine = create_engine(f"sqlite:///{tmp_path / 'broken.db'}")
        assert init_youtube_tables(engine)
        monkeypatch.setattr(models, "COLUMN_MIGRATIONS", (("yt_shots", "bad", "NOT A TYPE ((("),))
        assert init_youtube_tables(engine) is False

    def test_a_table_created_by_another_process_is_not_an_error(self, monkeypatch):
        import youtube.models as models

        engine = create_engine("sqlite://")
        calls = {"n": 0}
        real_create_all = models.YTModel.metadata.create_all

        def first_call_loses_the_race(bind, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                real_create_all(bind, **kwargs)
                raise RuntimeError('relation "yt_projects" already exists')
            return real_create_all(bind, **kwargs)

        monkeypatch.setattr(models.YTModel.metadata, "create_all", first_call_loses_the_race)
        assert init_youtube_tables(engine) is True
        assert calls["n"] == 2
