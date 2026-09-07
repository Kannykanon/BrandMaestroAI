# graph/deps.py
"""
Per-request dependency resolution for graph nodes.

Agent Platform Runtime (LanggraphAgent) builds a graph ONCE when the agent
is deployed, then reuses that same compiled graph for every subsequent
`.query(...)` call — it does not rebuild the graph per request the way the
old `build_graph(search, rag, analyzer, memory)` factory did.

That means `rag`, `analyzer`, and `memory` can no longer be pre-bound via
functools.partial() with a single business_id/content_type baked in at
compile time. Instead, each node resolves them fresh, per call, from the
business_id/content_type already carried on GraphState.

`search` is the exception: it has no per-business scope, so a single
instance is safely shared across every request and can still be bound once
at graph-build time.
"""
from brand_rag import BrandRAG
from brand_metrics import BrandMetricsSQL
from learning_memory import FeedbackPortSQL
from embedding_stategy import EmbeddingPort, build_embedding

# Lazily constructed on first real call and reused after that — importing
# this module (e.g. to monkeypatch resolve_deps in tests) must not trigger
# loading/downloading the embedding model as a side effect.
_embedding = None


def _get_embedding() -> EmbeddingPort:
    global _embedding
    if _embedding is None:
        _embedding = build_embedding()
    return _embedding


def resolve_deps(business_id: str, content_type: str):
    """Build fresh, business-scoped RAG/metrics/memory adapters for a single
    node invocation. Cheap: BrandRAG's underlying vector index is cached at
    the class level (keyed by business_id/content_type), so this does not
    re-embed documents or reopen connections — it just returns thin,
    correctly-scoped wrapper objects.
    """
    rag = BrandRAG(
        business_id=business_id,
        content_type=content_type,
        embedding=_get_embedding(),
    )
    analyzer = BrandMetricsSQL(
        business_id=business_id,
        content_type=content_type,
    )
    memory = FeedbackPortSQL(business_id=business_id)
    return rag, analyzer, memory
