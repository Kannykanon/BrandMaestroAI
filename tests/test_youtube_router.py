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
