"""Importing your own script into YouTube Studio."""
import pytest

import youtube.imports as imports
import youtube.projects as projects
from tests.test_youtube_projects import FakeLLM
from tests.test_youtube_storyboard_routes import api  # noqa: F401  (fixture)

MY_SCRIPT = """EXT. BAKERY - DAWN

The ovens were cold.

MAYA: One more try.
LEO: You said that yesterday.
"""


def test_import_validates_and_defaults_the_title(api):
    with api.Session() as db:
        with pytest.raises(projects.ProjectError, match="at least a few lines"):
            imports.import_script(db, "biz", "Short", "Too short")
        with pytest.raises(projects.ProjectError, match="at most"):
            imports.import_script(db, "biz", "Long", "word " * 5000)
        script = imports.import_script(db, "biz", "", MY_SCRIPT)
        assert script.topic == "EXT. BAKERY - DAWN" and script.label == "imported"
        assert imports.get_import(db, "biz", script.generation_id).content == MY_SCRIPT.strip()
        assert imports.get_import(db, "someone-else", script.generation_id) is None
        assert imports.get_import(db, "biz", "import-abc") is None


def test_paste_upload_list_create_project_and_delete(api, monkeypatch):
    import youtube.router as yt_router
    monkeypatch.setattr(yt_router, "list_eligible_scripts", lambda db, business_id: [])  # no content-writing tables here
    c = api.client
    pasted = c.post("/youtube/scripts/import", data={"title": "Crumb ad", "content": MY_SCRIPT})
    assert pasted.status_code == 201 and pasted.json()["approval"] == "imported"
    sid = pasted.json()["generation_id"]

    uploaded = c.post("/youtube/scripts/import", files={"file": ("launch-ad.txt", MY_SCRIPT.encode("utf-8-sig"), "text/plain")})
    assert uploaded.status_code == 201 and uploaded.json()["topic"] == "launch-ad"
    assert c.post("/youtube/scripts/import", files={"file": ("x.bin", bytes(range(256)) * 4, "application/octet-stream")}).status_code == 400

    listed = c.get("/youtube/scripts").json()["scripts"]
    assert {s["topic"] for s in listed} >= {"Crumb ad", "launch-ad"}
    full = c.get(f"/youtube/scripts/{sid}")
    assert full.status_code == 200 and full.json()["content"] == MY_SCRIPT.strip()

    project = c.post("/youtube/projects", json={"generation_id": sid, "format": "short"})
    assert project.status_code == 201 and project.json()["approval"] == "imported"
    pid = project.json()["id"]
    with api.Session() as db:
        planned = projects.plan_project(db, projects.get_project(db, "biz", pid), llm=FakeLLM())
        assert [s.speaker_label for s in projects._shots(db, planned)] == ["NARRATOR", "MAYA", "LEO"]

    assert c.delete(f"/youtube/scripts/{sid}").status_code == 204
    assert c.get(f"/youtube/projects/{pid}").json()["script"] == MY_SCRIPT.strip(), "the project keeps its copy"
    assert c.delete("/youtube/scripts/some-generation-id").status_code == 404, "content writing's scripts are not deleted here"
    api.user.business_id = "someone-else"
    assert c.delete(f"/youtube/scripts/{uploaded.json()['generation_id']}").status_code == 404
