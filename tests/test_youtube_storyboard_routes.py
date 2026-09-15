"""Phase 2 routes: faces, sheets, styles and storyboard, through FastAPI with SQLite and fakes."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import youtube.projects as projects
import youtube.router as yt_router
import youtube.storyboard as storyboard
import youtube.tasks as tasks
from auth import get_current_user
from database import get_db
from tests.test_youtube_projects import FakeLLM, FakeVoice
from tests.test_youtube_storyboard import SCRIPT, FakeImages, photo
from youtube.eligibility import EligibleScript
from youtube.images import ImageRegistry
from youtube.models import init_youtube_tables
from youtube.storage import LocalStorage, StorageSingleton
from youtube.voice import VoiceRegistry


@pytest.fixture
def api(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    init_youtube_tables(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    monkeypatch.setattr(VoiceRegistry, "_instances", {"fake": FakeVoice()})
    monkeypatch.setitem(VoiceRegistry.PROVIDERS, "fake", FakeVoice)
    images = FakeImages()
    monkeypatch.setattr(ImageRegistry, "_instances", {"fake_images": images})
    monkeypatch.setitem(ImageRegistry.PROVIDERS, "fake_images", FakeImages)
    monkeypatch.setenv("YT_IMAGE_PROVIDER", "fake_images")
    script = EligibleScript("gen-1", "The bakery", SCRIPT, "human", 9.0, None)
    monkeypatch.setattr(projects, "get_eligible_script", lambda db, b, g: script if (b, g) == ("biz", "gen-1") else None)
    storage = LocalStorage(root=str(tmp_path / "store"))
    monkeypatch.setattr(StorageSingleton, "_instance", storage)

    queued = []

    class _Task:
        def __init__(self, name):
            self.name = name

        def delay(self, *args, **kwargs):
            queued.append((self.name, args, kwargs))
            return SimpleNamespace(id="task")

    for name in ("plan_project", "voice_project", "voice_previews", "storyboard_project", "character_sheet"):
        monkeypatch.setattr(tasks, name, _Task(name))

    def get_session():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(yt_router.router, prefix="/youtube")
    user = SimpleNamespace(business_id="biz")
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = get_session
    return SimpleNamespace(client=TestClient(app), queued=queued, Session=Session, storage=storage,
                           images=images, user=user)


def upload(client, character_id, data=None):
    return client.post(f"/youtube/characters/{character_id}/faces",
                       files={"file": ("face.jpg", data or photo(), "image/jpeg")})


def test_face_upload_rights_sheet_and_approval(api):
    c = api.client
    maya = c.post("/youtube/characters", json={"name": "Maya", "voice_id": "warm", "voice_provider": "fake"}).json()

    assert upload(c, maya["id"]).status_code == 400, "rights must be confirmed first"
    assert c.post(f"/youtube/characters/{maya['id']}/rights", json={"confirmed": True}).json()["rights_confirmed"]
    body = upload(c, maya["id"]).json()
    assert len(body["faces"]) == 1 and body["sheet"] is None

    face = c.get(f"/youtube/characters/{maya['id']}/images/{body['faces'][0]['id']}")
    assert face.status_code == 200 and face.headers["content-type"] == "image/jpeg"
    assert face.headers["content-disposition"].startswith("inline")

    assert upload(c, maya["id"], b"not an image").status_code == 400

    assert c.post(f"/youtube/characters/{maya['id']}/sheet").status_code == 202
    assert api.queued[-1] == ("character_sheet", (maya["id"], "biz"), {})
    assert c.post(f"/youtube/characters/{maya['id']}/sheet").status_code == 409, "already generating"

    with api.Session() as db:
        character = projects.get_character(db, "biz", maya["id"])
        storyboard.generate_sheet(db, character, api.storage, port=api.images)
    body = c.post(f"/youtube/characters/{maya['id']}/sheet/approve").json()
    assert body["sheet_status"] == "approved" and body["sheet"]["approved"] is True

    listed = c.get("/youtube/characters").json()["characters"][0]
    assert listed["sheet_status"] == "approved" and listed["rights_confirmed"]


def test_uploads_over_the_size_limit_are_refused(api, monkeypatch):
    monkeypatch.setattr(yt_router, "MAX_UPLOAD_BYTES", 1000)
    c = api.client
    maya = c.post("/youtube/characters", json={"name": "Maya"}).json()
    c.post(f"/youtube/characters/{maya['id']}/rights", json={"confirmed": True})
    assert upload(c, maya["id"]).status_code == 413


def test_storyboard_routes(api):
    c = api.client
    project = c.post("/youtube/projects", json={"generation_id": "gen-1", "format": "short"}).json()
    pid = project["id"]
    with api.Session() as db:
        projects.plan_project(db, projects.get_project(db, "biz", pid), llm=FakeLLM())

    # Not ready: nothing is cast yet, so the request is refused with the reasons.
    refused = c.post(f"/youtube/projects/{pid}/storyboard", json={})
    assert refused.status_code == 400 and "has no character" in refused.json()["detail"]

    ids = {}
    for label, name, voice in (("NARRATOR", "Narrator", "deep"), ("MAYA", "Maya", "warm"), ("LEO", "Leo", "bright")):
        ids[label] = c.post("/youtube/characters", json={"name": name, "voice_id": voice, "voice_provider": "fake"}).json()["id"]
        if label != "NARRATOR":
            c.post(f"/youtube/characters/{ids[label]}/rights", json={"confirmed": True})
            upload(c, ids[label])
            with api.Session() as db:
                storyboard.start_sheet(db, projects.get_character(db, "biz", ids[label]))
                storyboard.generate_sheet(db, projects.get_character(db, "biz", ids[label]), api.storage, port=api.images)
            c.post(f"/youtube/characters/{ids[label]}/sheet/approve")
    c.put(f"/youtube/projects/{pid}/cast", json={"assignments": ids})

    style = c.post("/youtube/styles", json={"name": "Warm", "prompt": "Warm film look."}).json()
    assert c.patch(f"/youtube/projects/{pid}", json={"style_id": style["id"]}).json()["storyboard"]["style_id"] == style["id"]

    assert c.post(f"/youtube/projects/{pid}/storyboard", json={"force": False}).status_code == 202
    assert api.queued[-1] == ("storyboard_project", (pid, "biz"), {"force": False, "shot_ids": None})
    assert c.post(f"/youtube/projects/{pid}/storyboard", json={}).status_code == 409, "already drawing"

    with api.Session() as db:
        p = projects.get_project(db, "biz", pid)
        storyboard.generate_storyboard(db, p, api.storage, port=api.images)
        projects.voice_project(db, p, api.storage)

    detail = c.get(f"/youtube/projects/{pid}").json()
    assert detail["status"] == "storyboard_ready"
    assert all(s["has_image"] for s in detail["shots"])
    shot = detail["shots"][1]

    image = c.get(f"/youtube/projects/{pid}/shots/{shot['id']}/image")
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    assert c.get(f"/youtube/projects/{pid}/shots/{shot['id']}/image", params={"as_link": "true"}).json() == {"url": None}

    edited = c.patch(f"/youtube/projects/{pid}/shots/{shot['id']}", json={"visual": "Maya by the oven."}).json()
    assert next(s for s in edited["shots"] if s["id"] == shot["id"])["visual"] == "Maya by the oven."
    assert c.post(f"/youtube/projects/{pid}/shots/{shot['id']}/image").status_code == 202
    assert api.queued[-1] == ("storyboard_project", (pid, "biz"), {"force": False, "shot_ids": [shot["id"]]})
    with api.Session() as db:
        projects.settle_status(db, projects.get_project(db, "biz", pid))
        db.commit()

    approved = c.post(f"/youtube/projects/{pid}/storyboard/approve")
    assert approved.status_code == 200 and approved.json()["status"] == "storyboard_approved"


def test_styles_crud_and_reference(api):
    c = api.client
    style = c.post("/youtube/styles", json={"name": "Noir", "prompt": "Black and white."}).json()
    assert c.post(f"/youtube/styles/{style['id']}/reference",
                  files={"file": ("ref.jpg", photo(), "image/jpeg")}).json()["has_reference"]
    assert c.get(f"/youtube/styles/{style['id']}/reference").status_code == 200
    assert c.delete(f"/youtube/styles/{style['id']}/reference").json()["has_reference"] is False
    assert c.patch(f"/youtube/styles/{style['id']}", json={"name": "Noir 2"}).json()["name"] == "Noir 2"
    assert c.delete(f"/youtube/styles/{style['id']}").status_code == 204
    assert c.get("/youtube/styles").json() == {"styles": []}


def test_other_businesses_cannot_reach_characters_or_styles(api):
    c = api.client
    maya = c.post("/youtube/characters", json={"name": "Maya"}).json()
    style = c.post("/youtube/styles", json={"name": "Warm", "prompt": "Warm."}).json()
    api.user.business_id = "someone-else"
    assert c.post(f"/youtube/characters/{maya['id']}/rights", json={"confirmed": True}).status_code == 404
    assert upload(c, maya["id"]).status_code == 404
    assert c.post(f"/youtube/characters/{maya['id']}/sheet").status_code == 404
    assert c.patch(f"/youtube/styles/{style['id']}", json={"name": "Mine"}).status_code == 404


def test_status_reports_the_image_provider(api):
    body = api.client.get("/youtube/status").json()
    assert body["images"] == {"provider": "fake_images", "configured": True, "missing": [], "usd_per_image": 0.05}
