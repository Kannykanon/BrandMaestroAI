"""Which marketing scripts YouTube Automation may use, and how each is labelled.

The rule exists twice — as SQL for the listing query and as Python for the
label — so these tests check both against the same table of cases, and check
the SQL against the Python on an in-memory SQLite database for every combination.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import Boolean, Column, MetaData, String, Table, create_engine, insert, select
from sqlalchemy.dialects import postgresql

from youtube.eligibility import (
    approval_label,
    eligibility_condition,
    eligible_scripts_query,
    _to_eligible,
)
from youtube.models import APPROVAL_ENFORCER, APPROVAL_HUMAN

# (human_approved, generations.approved, expected label)
CASES = [
    (True, True, APPROVAL_HUMAN),
    (True, False, APPROVAL_HUMAN),        # a person overrides the enforcer
    (True, None, APPROVAL_HUMAN),         # legacy row, approved by a person
    (False, True, None),                  # a person rejected it
    (False, False, None),
    (False, None, None),
    (None, True, APPROVAL_ENFORCER),      # not reviewed, enforcer approved
    (None, False, None),
    (None, None, None),                   # legacy row, never reviewed
]


@pytest.mark.parametrize("human, enforcer, expected", CASES)
def test_approval_label(human, enforcer, expected):
    assert approval_label(human, enforcer) == expected


def test_sql_rule_matches_python_rule_for_every_combination():
    metadata = MetaData()
    generations = Table(
        "generations", metadata,
        Column("generation_id", String, primary_key=True),
        Column("approved", Boolean, nullable=True),
    )
    reviews = Table(
        "reviewer_learning", metadata,
        Column("generation_id", String, primary_key=True),
        Column("human_approved", Boolean, nullable=True),
    )
    engine = create_engine("sqlite://")
    metadata.create_all(engine)

    expected = {}
    with engine.begin() as conn:
        for i, (human, enforcer, label) in enumerate(CASES):
            gid = f"g{i}"
            conn.execute(insert(generations).values(generation_id=gid, approved=enforcer))
            conn.execute(insert(reviews).values(generation_id=gid, human_approved=human))
            expected[gid] = label
        # A generation with no review row at all: the outer join yields NULL.
        conn.execute(insert(generations).values(generation_id="no_review_approved", approved=True))
        expected["no_review_approved"] = APPROVAL_ENFORCER
        conn.execute(insert(generations).values(generation_id="no_review_legacy", approved=None))
        expected["no_review_legacy"] = None

        rows = conn.execute(
            select(generations.c.generation_id)
            .select_from(generations.outerjoin(
                reviews, reviews.c.generation_id == generations.c.generation_id))
            .where(eligibility_condition(generations.c, reviews.c))
        ).scalars().all()

    assert set(rows) == {gid for gid, label in expected.items() if label is not None}


def test_listing_query_scopes_to_business_scripts_and_completed():
    sql = str(eligible_scripts_query("biz-1").compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "generations.business_id = 'biz-1'" in sql
    assert "generations.content_type IN ('script', 'ad')" in sql
    assert "generations.status = 'completed'" in sql
    assert "LEFT OUTER JOIN reviewer_learning" in sql
    assert "reviewer_learning.human_approved IS true" in sql
    assert "reviewer_learning.human_approved IS NULL AND generations.approved IS true" in sql


def _generation(**overrides):
    base = dict(
        generation_id="g1", topic="A launch story", content="MAYA: We did it.",
        approved=True, score=8.7, completed_at=datetime(2026, 9, 14, tzinfo=timezone.utc),
    )
    return SimpleNamespace(**{**base, **overrides})


def test_row_conversion_relabels_with_the_python_rule():
    script = _to_eligible(_generation(approved=True), None)
    assert script.label == APPROVAL_ENFORCER
    summary = script.to_summary()
    assert summary["approval"] == "enforcer"
    assert summary["word_count"] == 4
    assert summary["completed_at"] == "2026-09-14T00:00:00+00:00"


def test_row_conversion_drops_ineligible_or_empty_rows():
    assert _to_eligible(_generation(approved=None), None) is None
    assert _to_eligible(_generation(), False) is None
    assert _to_eligible(_generation(content="   "), True) is None


def test_ads_are_eligible_and_say_so():
    from youtube.eligibility import _to_eligible

    ad = _generation(content_type="ad", content="**Headline:** Fresh bread")
    summary = _to_eligible(ad, True).to_summary()
    assert summary["content_type"] == "ad" and summary["approval"] == "human"
    assert _to_eligible(_generation(), None).content_type == "script"
