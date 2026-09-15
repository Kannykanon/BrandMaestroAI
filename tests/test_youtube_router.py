"""The /youtube routes: scoped to the caller's business, and honest about setup."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import youtube.router as yt_router
from auth import get_current_user
from database import get_db
from youtube.eligibility import EligibleScript
from youtube.storage import StorageSingleton

SCRIPT = EligibleScript(
    generation_id="g1", topic="A launch story", content="NARRATOR: It began.\nMAYA: We did it.",
    label="human", score=9.1, completed_at=datetime(2026, 9, 14, tzinfo=timezone.utc),
)


@pytest.fixture
def client(monkeypatch):
    calls = []

    def fake_list(db, business_id):
        calls.append(("list", business_id))
        return [SCRIPT]

    def fake_get(db, business_id, generation_id):
        calls.append(("get", business_id, generation_id))
        return SCRIPT if generation_id == "g1" else None

    monkeypatch.setattr(yt_router, "list_eligible_scripts", fake_list)
    monkeypatch.setattr(yt_router, "get_eligible_script", fake_get)
    import youtube.imports as imports
    monkeypatch.setattr(imports, "list_imports", lambda db, business_id: [])
    for var in ("YT_STORAGE_PROVIDER", "YT_GCS_BUCKET"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(StorageSingleton, "_instance", None)

    app = FastAPI()
    app.include_router(yt_router.router, prefix="/youtube")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(business_id="biz-owner")
    app.dependency_overrides[get_db] = lambda: None

    test_client = TestClient(app)
    test_client.calls = calls
    return test_client


def test_lists_scripts_for_the_callers_business_only(client):
    response = client.get("/youtube/scripts")
    assert response.status_code == 200
    assert client.calls == [("list", "biz-owner")]
    [script] = response.json()["scripts"]
    assert script["generation_id"] == "g1"
    assert script["approval"] == "human"
    assert "content" not in script, "the list returns a preview, not the whole script"


def test_returns_one_script_in_full(client):
    response = client.get("/youtube/scripts/g1")
    assert response.status_code == 200
    assert response.json()["content"] == SCRIPT.content
    assert client.calls == [("get", "biz-owner", "g1")]


def test_missing_or_ineligible_script_is_404(client):
    assert client.get("/youtube/scripts/other").status_code == 404


def test_status_reports_storage_setup(client, monkeypatch):
    body = client.get("/youtube/status").json()
    assert body["storage"] == {"provider": "local", "configured": True, "missing": []}
    assert body["channel_connected"] is False

    monkeypatch.setenv("YT_STORAGE_PROVIDER", "gcs")
    body = client.get("/youtube/status").json()
    assert body["storage"] == {"provider": "gcs", "configured": False, "missing": ["YT_GCS_BUCKET"]}


def test_routes_require_authentication():
    app = FastAPI()
    app.include_router(yt_router.router, prefix="/youtube")
    assert TestClient(app).get("/youtube/scripts").status_code == 401


# ---------------------------------------------------------------------------
#  Projects, characters and audio through the routes (SQLite, fake voice)
# ---------------------------------------------------------------------------
from tests.test_youtube_projects import SCRIPT as SCRIPT_TEXT, FakeVoice  # noqa: E402


@pytest.fixture
def api(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import youtube.projects as projects
    import youtube.tasks as tasks
    from youtube.models import init_youtube_tables
    from youtube.storage import LocalStorage
    from youtube.voice import VoiceRegistry

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    init_youtube_tables(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    monkeypatch.setattr(VoiceRegistry, "_instances", {"fake": FakeVoice()})
    monkeypatch.setitem(VoiceRegistry.PROVIDERS, "fake", FakeVoice)
    monkeypatch.setattr(projects, "get_eligible_script",
                        lambda db, b, g: SCRIPT_OBJ if (b, g) == ("biz-owner", "gen-1") else None)
    storage = LocalStorage(root=str(tmp_path / "store"))
    monkeypatch.setattr(StorageSingleton, "_instance", storage)

    queued = []

    class _Task:
        def __init__(self, name):
            self.name = name

        def delay(self, *args, **kwargs):
            queued.append((self.name, args, kwargs))
            return SimpleNamespace(id=f"task-{len(queued)}")

    for name in ("plan_project", "voice_project", "voice_previews"):
        monkeypatch.setattr(tasks, name, _Task(name))

    def get_session():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.include_router(yt_router.router, prefix="/youtube")
    owner = SimpleNamespace(business_id="biz-owner")
    app.dependency_overrides[get_current_user] = lambda: owner
    app.dependency_overrides[get_db] = get_session
    return SimpleNamespace(client=TestClient(app), queued=queued, Session=Session, owner=owner,
                           storage=storage, projects=projects)


SCRIPT_OBJ = EligibleScript("gen-1", "The launch", SCRIPT_TEXT, "enforcer", 8.5, None)


def test_project_flow_through_the_routes(api):
    c = api.client
    project = c.post("/youtube/projects", json={"generation_id": "gen-1", "format": "long_form"}).json()
    assert project["status"] == "draft" and project["approval"] == "enforcer"
    pid = project["id"]

    assert c.post(f"/youtube/projects/{pid}/plan").status_code == 202
    assert api.queued[-1] == ("plan_project", (pid, "biz-owner"), {})
    assert c.post(f"/youtube/projects/{pid}/plan").status_code == 409, "a second plan is refused while planning"

    # Run the queued planning step directly, as the worker would.
    with api.Session() as db:
        p = api.projects.get_project(db, "biz-owner", pid)
        api.projects.plan_project(db, p, llm=SimpleNamespace(
            invoke=lambda prompt: SimpleNamespace(content="{}")))

    assert c.post(f"/youtube/projects/{pid}/voice", json={}).status_code == 400, "cannot voice before casting"

    ids = {}
    for name, voice in (("Narrator", "deep"), ("Maya", "warm"), ("Leo", "bright")):
        response = c.post("/youtube/characters", json={"name": name, "voice_id": voice, "voice_provider": "fake"})
        assert response.status_code == 201
        ids[name.upper()] = response.json()["id"]
    cast = c.put(f"/youtube/projects/{pid}/cast", json={"assignments": ids}).json()
    assert cast["status"] == "cast"

    assert c.post(f"/youtube/projects/{pid}/voice", json={"force": False}).status_code == 202
    assert api.queued[-1] == ("voice_project", (pid, "biz-owner"), {"force": False})

    with api.Session() as db:
        p = api.projects.get_project(db, "biz-owner", pid)
        api.projects.voice_project(db, p, api.storage)

    audio = c.get(f"/youtube/projects/{pid}/audio")
    assert audio.status_code == 200
    assert audio.headers["content-type"] == "audio/wav"
    assert audio.content[:4] == b"RIFF"
    # Local storage cannot sign a link, so the UI is told to download through the API.
    assert c.get(f"/youtube/projects/{pid}/audio", params={"as_link": "true"}).json() == {"url": None}

    shot_id = c.get(f"/youtube/projects/{pid}").json()["shots"][0]["id"]
    assert c.get(f"/youtube/projects/{pid}/shots/{shot_id}/audio").status_code == 200


def test_projects_of_other_businesses_are_invisible(api):
    pid = api.client.post("/youtube/projects", json={"generation_id": "gen-1"}).json()["id"]
    api.owner.business_id = "someone-else"
    assert api.client.get(f"/youtube/projects/{pid}").status_code == 404
    assert api.client.post(f"/youtube/projects/{pid}/plan").status_code == 404
    assert api.client.get("/youtube/projects").json() == {"projects": []}


def test_ineligible_script_and_bad_format_are_rejected(api):
    assert api.client.post("/youtube/projects", json={"generation_id": "nope"}).status_code == 400
    assert api.client.post("/youtube/projects",
                           json={"generation_id": "gen-1", "format": "square"}).status_code == 422


def test_voices_list_and_previews(api):
    body = api.client.get("/youtube/voices", params={"provider": "fake"}).json()
    assert body["provider"] == "fake"
    assert [v["id"] for v in body["voices"]] == ["warm", "deep", "bright"]
    assert not any(v["has_preview"] for v in body["voices"])
    assert api.client.post("/youtube/voices/previews", json={"provider": "fake"}).status_code == 202
    assert api.client.get("/youtube/voices", params={"provider": "elevenlabs"}).status_code == 400
    assert api.client.get("/youtube/voices/fake/warm/preview").status_code == 404
