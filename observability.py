"""
Opik tracing for the Researcher -> Writer -> Enforcer -> Deployer graph.

Wired at the graph level (not per-LLM-call) so a trace shows the actual
4-agent architecture — which node ran, what it received, what it produced,
and where a revision loop kicked in — rather than a flat list of disconnected
LLM calls with no relationship between them. That structure is what makes a
trace useful as evidence (e.g. "the Enforcer caught this and sent it back"),
not just a log of API calls.

Configure via env vars: OPIK_API_KEY (required to enable tracing at all),
OPIK_WORKSPACE, OPIK_PROJECT_NAME. Nothing here is hardcoded to a specific
project or workspace — same principle as the rest of this codebase's brand
handling: behavior is driven by configuration/data, not baked-in identifiers.

Fails open everywhere: a missing key, an unreachable Opik backend, or any
import/runtime error results in an empty callback list rather than a raised
exception, so a telemetry outage can never block content generation.
"""
import logging
import os

logger = logging.getLogger(__name__)

_opik_available = bool(os.getenv("OPIK_API_KEY"))
if not _opik_available:
    logger.info("OPIK_API_KEY not set — Opik tracing disabled")

# Importing `opik` itself costs ~8-10s (it pulls in several heavy
# dependencies, e.g. litellm) — measured directly, and it is import cost,
# not a network/credential check (constructing OpikTracer afterwards is
# ~0.05s). That cost must never land on a live generation request, so it is
# paid here, once, at module import time (i.e. when a Celery worker or the
# API process boots), not lazily inside get_trace_callbacks() on whichever
# request happens to run first.
OpikTracer = None
if _opik_available:
    try:
        from opik.integrations.langchain import OpikTracer as _OpikTracer
        OpikTracer = _OpikTracer

        # Compatibility shim: this project pins langchain_core==0.1.x, from
        # before LangChain's messages became native Pydantic v2 models. Opik's
        # LangChain integration calls msg.model_dump() unconditionally and
        # assumes that method exists (verified directly — it raises
        # AttributeError on every single chat-model call otherwise, silently
        # swallowed by Opik's own error handling, which means tracing
        # would run with every message serialization failing rather than
        # actually failing open as intended). Add the missing method only
        # where it's absent, delegating to the v1-style .dict() that these
        # message classes already provide — do not touch anything if a
        # future langchain_core upgrade already provides model_dump() natively.
        from langchain_core.messages import BaseMessage
        if not hasattr(BaseMessage, "model_dump"):
            _DUMP_KWARGS = {
                "include", "exclude", "by_alias",
                "exclude_unset", "exclude_defaults", "exclude_none",
            }

            def _model_dump_shim(self, **kwargs):
                return self.dict(**{k: v for k, v in kwargs.items() if k in _DUMP_KWARGS})

            BaseMessage.model_dump = _model_dump_shim
            logger.info(
                "Applied model_dump() compatibility shim to BaseMessage for Opik tracing "
                "(langchain_core %s predates native Pydantic v2 messages)",
                __import__("langchain_core").__version__,
            )
    except Exception as e:
        logger.warning("Opik import failed at startup, tracing disabled: %s", e)
        _opik_available = False


def get_trace_callbacks(graph_flow=None, tags=None):
    """
    Build the LangChain callback list for one graph run.

    Pass the result as config={"callbacks": ...} to a compiled LangGraph's
    .invoke()/.astream() call. Returns [] (never raises) if Opik isn't
    configured or unavailable, so every call site can use this unconditionally
    without its own try/except. The one-time import cost has already been
    paid at process startup (see above) — this only does the ~0.05s
    per-run tracer construction.

    `graph_flow`: the compiled graph (already has .get_graph() available
    before any run), used to render the trace as the actual agent topology.
    `tags`: optional list of strings to filter/group traces in the Opik UI
    (e.g. content_type, whether web search was used).
    """
    if not _opik_available or OpikTracer is None:
        return []

    try:
        graph_repr = graph_flow.get_graph() if graph_flow is not None else None
        tracer = OpikTracer(
            graph=graph_repr,
            tags=tags or [],
            project_name=os.getenv("OPIK_PROJECT_NAME", "brandmuse-ai"),
        )
        return [tracer]
    except Exception as e:
        logger.warning("Opik tracer unavailable, continuing without tracing: %s", e)
        return []


def log_token_usage_to_opik(label: str = "generation", extra: dict = None) -> dict:
    """Attach the token counts recorded since the last reset to the current Opik trace.

    Token usage is captured at the Gemini SDK boundary (see
    model.install_token_capture) because the pinned langchain-google-genai
    drops it before any callback runs. Opik is where those numbers are useful,
    so this pushes them onto the active span alongside the graph trace.

    Returns the snapshot either way, so a caller can log or assert on it even
    when Opik is disabled. Fails open like everything else in this module.
    """
    from model import TokenUsage

    snapshot = TokenUsage.snapshot()
    if not _opik_available:
        return snapshot

    try:
        from opik import opik_context

        payload = {"token_usage": snapshot, "label": label}
        if extra:
            payload.update(extra)
        opik_context.update_current_trace(metadata=payload)
        logger.info("Token usage reported to Opik: %s", snapshot)
    except Exception as e:
        logger.debug("Could not attach token usage to Opik trace: %s", e)

    return snapshot
