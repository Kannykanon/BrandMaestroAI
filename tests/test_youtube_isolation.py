"""YouTube Automation must not be able to affect marketing.

Marketing code never imports it, its tables are not part of marketing's
database setup, marketing workers never listen on its queues, and the app
starts without it.
"""
import ast
import inspect
from pathlib import Path

from celery import Celery
from kombu import Queue

ROOT = Path(__file__).resolve().parent.parent
# The app entry point is allowed to mount the optional router.
ALLOWED_TO_IMPORT_YOUTUBE = {"main.py"}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_marketing_code_never_imports_youtube():
    offenders = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT)
        top = rel.parts[0]
        if top in {".venv", "youtube", "tests"} or str(rel) in ALLOWED_TO_IMPORT_YOUTUBE:
            continue
        if any(name == "youtube" or name.startswith("youtube.") for name in _imports(path)):
            offenders.append(str(rel))
    assert offenders == []


def test_youtube_tables_are_not_in_marketing_metadata():
    import youtube.models  # noqa: F401 - importing registers the yt_ tables
    from database import Model
    from youtube.models import YTModel

    assert not [t for t in Model.metadata.tables if t.startswith("yt_")]
    assert all(t.startswith("yt_") for t in YTModel.metadata.tables)
    assert len(YTModel.metadata.tables) == 13


def test_youtube_tables_never_reference_marketing_tables():
    from youtube.models import YTModel

    for table in YTModel.metadata.tables.values():
        for fk in table.foreign_keys:
            assert fk.column.table.name.startswith("yt_"), f"{table.name} -> {fk.target_fullname}"


def test_table_setup_failure_is_logged_not_raised():
    from youtube.models import init_youtube_tables

    class _BrokenEngine:
        def __getattr__(self, name):
            raise RuntimeError("database unavailable")

    assert init_youtube_tables(_BrokenEngine()) is False


def test_marketing_queues_do_not_include_youtube_queues():
    source = inspect.getsource(__import__("celery_task"))
    assert "yt_" not in source


def test_register_queues_adds_without_replacing_and_is_idempotent():
    from youtube.queues import YT_QUEUE_NAMES, YT_TASK_ROUTES, register_queues

    app = Celery("test")
    app.conf.task_queues = [Queue("generation")]
    app.conf.task_routes = {"tasks.generate_content": {"queue": "generation"}}

    register_queues(app)
    register_queues(app)

    names = [q.name for q in app.conf.task_queues]
    assert names == ["generation", *YT_QUEUE_NAMES]
    assert app.conf.task_routes["tasks.generate_content"] == {"queue": "generation"}
    for pattern, route in YT_TASK_ROUTES.items():
        assert app.conf.task_routes[pattern] == route


def test_app_starts_without_youtube_when_the_module_is_unavailable():
    source = inspect.getsource(__import__("main"))
    assert "youtube_router = None" in source
    assert "if youtube_router is not None:" in source
