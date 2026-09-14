"""The enforcer's verdict is saved with each generation.

status='completed' does not mean approved: when the revision loop runs out of
rounds, the deployer still saves the last draft. YouTube Automation needs the
real verdict to decide which scripts are eligible.
"""
import inspect
import re

import pytest
from sqlalchemy.dialects import postgresql

import database
import graph.deps
import nodes.deployer


class _Session:
    def __init__(self, sink):
        self.sink = sink

    def execute(self, stmt):
        self.sink.append(stmt)

    def commit(self):
        pass


class _Memory:
    def save(self, **kwargs):
        pass


@pytest.fixture
def run_deployer(monkeypatch):
    statements = []

    class _Ctx:
        def __enter__(self):
            return _Session(statements)

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(nodes.deployer, "get_db_session", lambda: _Ctx())
    monkeypatch.setattr(graph.deps, "resolve_deps", lambda b, c: (None, None, _Memory()))

    def run(**state):
        base = {
            "generation_id": "g1", "business_id": "b1", "content_type": "script",
            "topic": "t", "format_type": "video script", "content": "MAYA: hello",
            "score": 7.9,
        }
        nodes.deployer.deployer_node({**base, **state})
        return statements[-1].compile(dialect=postgresql.dialect())

    return run


@pytest.mark.parametrize("verdict", [True, False])
def test_deployer_saves_the_enforcers_verdict(run_deployer, verdict):
    compiled = run_deployer(approved=verdict)
    assert compiled.params["approved"] is verdict

    # The on-conflict update writes it too, so a re-run replaces a stale verdict.
    update_clause = str(compiled).split("ON CONFLICT")[1]
    match = re.search(r"approved = %\((\w+)\)s", update_clause)
    assert match, update_clause
    assert compiled.params[match.group(1)] is verdict


def test_missing_verdict_is_saved_as_not_approved(run_deployer):
    assert run_deployer().params["approved"] is False


def test_generation_model_has_a_nullable_verdict():
    column = database.Generation.__table__.c.approved
    assert column.nullable is True
    assert column.default is None and column.server_default is None


def test_existing_databases_get_the_column_without_a_default():
    source = inspect.getsource(database.init_db)
    assert '"ALTER TABLE generations ADD COLUMN approved BOOLEAN"' in source
