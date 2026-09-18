"""A cached brand context must not outlive the code that built it.

Em-dash counts were added to end a loop where the system refused a mark the
brand writes in every scene heading. The fix deployed, the container had it,
and the next run failed in exactly the same way: get_context() serves the Redis
cache, which still held a context assembled before the change. The counted
mechanics are only re-attached when the read falls through to Postgres.

A day of that is the cache's TTL. The key now carries a version, so the entry
built by older code cannot be found by newer code.
"""
from brand_metrics import MECHANICS_VERSION, BrandMetricsSQL


class _Brain(BrandMetricsSQL):
    def __init__(self, business_id, content_type):
        # Skip the real __init__: Redis and the model singletons are not needed
        # to ask what a cache key looks like.
        self.business_id = business_id
        self.content_type = content_type


def test_the_key_carries_the_version():
    brain = _Brain("biz", "script")
    assert f":{MECHANICS_VERSION}:" in brain._cache_key
    assert f":{MECHANICS_VERSION}:" in brain._stale_key


def test_the_stale_entry_is_versioned_too():
    """It is served during a rebuild, so an unversioned one would reintroduce
    exactly what the version exists to retire."""
    brain = _Brain("biz", "script")
    assert brain._stale_key != brain._cache_key
    assert brain._stale_key.startswith("brand_context_stale:")


def test_brands_and_content_types_still_have_their_own_entries():
    assert _Brain("a", "script")._cache_key != _Brain("b", "script")._cache_key
    assert _Brain("a", "script")._cache_key != _Brain("a", "blog")._cache_key


def test_the_version_is_ahead_of_the_first_release():
    """It starts at 1. Anything later means a mechanics change has shipped and
    the constant was remembered."""
    assert MECHANICS_VERSION >= 2
