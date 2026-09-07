# deploy/agent_runtime.py
"""
Wraps the existing LangGraph pipeline (graph/graph.py) so it can be
deployed on Google Cloud's Gemini Enterprise Agent Platform Runtime, using
the framework-specific `LanggraphAgent` template.

Docs this follows:
  https://docs.cloud.google.com/gemini-enterprise-agent-platform/build/runtime/create-a-langgraph-agent

Agent Platform Runtime calls `runnable_builder(model=..., **kwargs)` exactly
ONCE, at deploy time, to construct the graph. The returned compiled graph is
then reused for every subsequent `.query(...)` / `.stream_query(...)` call
against the deployed agent. That's why graph/graph.py and graph/deps.py were
refactored: `build_graph()` now only closes over `search` (which has no
per-request scope), while `rag`/`analyzer`/`memory` are resolved fresh
inside each node from `state["business_id"]` / `state["content_type"]`.

This module does NOT run any network calls at import time — it's safe to
import for local testing without GCP credentials.
"""
import os

from graph.graph import build_graph
from search import ParallelSearch


def researcher_pipeline_builder(*, model=None, **kwargs):
    """
    `runnable_builder` for `vertexai.agent_engines.LanggraphAgent`.

    We intentionally ignore the `model` Agent Platform would otherwise
    inject via ChatVertexAI: BrandMaestro's nodes route models internally per
    task type through `model.LLMSingleton` (extraction/enforcement/
    synthesis/generation each get their own temperature + model choice),
    so the graph builds its own model routing rather than taking a single
    shared `model` object. If you want Agent Platform's default
    ChatVertexAI model management instead, thread `model` down into
    LLMSingleton here.
    """
    search = ParallelSearch(api_key=os.environ["PARALLEL_API_KEY"])
    return build_graph(search)


def get_agent_engine_app():
    """
    Returns the `LanggraphAgent` instance ready to hand to
    `vertexai.agent_engines.create(...)`. Import is deferred so this module
    can be imported (e.g. by tests) without the Agent Platform SDK
    installed.
    """
    from vertexai.preview import reasoning_engines  # Agent Platform SDK

    return reasoning_engines.LanggraphAgent(
        model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        runnable_builder=researcher_pipeline_builder,
    )
