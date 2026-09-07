from graph.state import GraphState
from nodes import researcher_node, writer_node, enforcer_node, deployer_node
from search import SearchPort
from langgraph.graph import StateGraph, END
from functools import partial

def build_graph(search: SearchPort) -> StateGraph:
    """
    Compiles the Researcher -> Writer -> Enforcer -> Deployer graph.

    Only `search` is bound at compile time (it has no per-business scope).
    rag/analyzer/memory are resolved per node call from
    state["business_id"]/state["content_type"] — see graph/deps.py — so
    this compiled graph is safe to build ONCE and reuse across many
    requests, which is what Agent Platform Runtime's LanggraphAgent
    template expects (it calls this builder once at deploy time, not
    per query).
    """
    researcher = partial(researcher_node, search=search)

    graph = StateGraph(GraphState)

    graph.add_node("researcher", researcher)
    graph.add_node("writer",     writer_node)
    graph.add_node("enforcer",   enforcer_node)
    graph.add_node("deployer",   deployer_node)

    graph.set_entry_point("researcher")
    graph.add_edge("researcher", "writer")
    graph.add_edge("writer",     "enforcer")

    # Same ceiling the Enforcer applies, read from one place so the two
    # cannot drift apart (MAX_REVISION_ITERATIONS env var).
    from nodes.enforcer import MAX_ITERATIONS as MAX_GRAPH_ITERATIONS

    def _route_after_enforcer(s):
        if s["approved"]:
            return "deployer"
        if s.get("iteration", 0) >= MAX_GRAPH_ITERATIONS:
            return "deployer"      # enforcer already handles hallucination-safe approval
        return "writer"

    # Explicit path_map so the graph's static topology (what draws in a
    # visualization, e.g. the Opik trace) only shows the two edges this
    # function can actually return. Without it, LangGraph can't prove which
    # nodes a plain Python function might reach, so it conservatively draws a
    # conditional edge to every other node in the graph — including back to
    # "researcher", which _route_after_enforcer never returns and which never
    # fires in any real run (confirmed against live execution traces). That
    # phantom edge is misleading in exactly the kind of artifact meant to
    # demonstrate the pipeline's real behavior.
    graph.add_conditional_edges(
        "enforcer",
        _route_after_enforcer,
        {"writer": "writer", "deployer": "deployer"},
    )

    graph.add_edge("deployer", END)

    return graph.compile()