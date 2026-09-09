# nodes/researcher.py
import logging
from model import LLMSingleton
from search import SearchPort
from prompts.researcher import RESEARCH_SUMMARY, PUBLISHABLE_FACTS_FILTER
from graph.state import GraphState
from utils.brand_profile import extract_section, extract_brand_name
from utils.documents import has_reference_documents

logger = logging.getLogger(__name__)
from utils.observe import observe


@observe("researcher_node")
def researcher_node(state: GraphState, search: SearchPort) -> GraphState:
    """
    Retrieves research context for content generation.

    Default: queries RAG system using uploaded product documents
    as source of truth for content generation.

    Optional: performs web search when use_search is True,
    useful for trend-based or statistics-driven content.

    `rag` and `analyzer` are resolved per call from state["business_id"] /
    state["content_type"] (see graph/deps.py) rather than pre-bound, so this
    node works unchanged whether the graph is compiled fresh per request
    (local/Celery path) or once at deploy time (Agent Platform Runtime).

    Args:
        state:  Current graph state
        search: Search port for optional web search (Parallel)

    Returns:
        Updated state with research context
    """
    topic = state["topic"]
    content_type = state["content_type"]
    from graph.deps import resolve_deps
    rag, analyzer, _memory = resolve_deps(state["business_id"], content_type)

    # Extract brief brand context for research filtering (#7)
    brand_context = "No brand context available — summarize neutrally."
    brand_name = ""
    try:
        metrics = analyzer.get_context()
        if metrics:
            # Extract just the overview and value hierarchy for research filtering
            overview = extract_section(metrics, "BRAND VOICE OVERVIEW")
            value_hierarchy = extract_section(metrics, "VALUE HIERARCHY")
            diagnostic = extract_section(metrics, "DIAGNOSTIC STYLE")

            parts = [p for p in [overview, value_hierarchy, diagnostic] if p]
            if parts:
                brand_context = " ".join(parts)[:500]  # Cap at 500 chars
            brand_name = extract_brand_name(metrics)
    except Exception as e:
        logger.warning("Failed to extract brand context for research: %s", e)

    # RAG over the brand's own uploaded documents is ALWAYS run: those are the
    # authoritative facts about the thing being written about. Web search, when
    # enabled, is additive external context on top of it — previously the two
    # were mutually exclusive, so turning search on silently discarded the
    # product document the copy was supposed to be grounded in.
    sources = []

    owned = ""
    try:
        owned = rag.query(topic) or ""
    except Exception as e:
        logger.warning("RAG query failed for topic=%r: %s", topic, e)

    # Strip internal planning material out of reference-document content before
    # the writer ever sees it. Only worth a call when this content type actually
    # has reference documents indexed — past published content is publishable by
    # definition and needs no filtering.
    if owned.strip() and has_reference_documents(state["business_id"], content_type):
        try:
            cleaned = LLMSingleton.get("extraction").invoke(
                PUBLISHABLE_FACTS_FILTER.format(source_material=owned)
            ).content
            if cleaned and cleaned.strip():
                logger.info(
                    "Publishable-facts filter applied: %d -> %d chars",
                    len(owned), len(cleaned.strip()),
                )
                owned = cleaned.strip()
            else:
                logger.warning("Publishable-facts filter returned empty — keeping raw source")
        except Exception as e:
            # Fail open: unfiltered facts beat no facts. The enforcer's
            # publishability gate is still there as the backstop.
            logger.warning("Publishable-facts filter failed, using raw source: %s", e)

    if owned.strip():
        sources.append(
            "═══ SOURCE MATERIAL — THE BRAND'S OWN DOCUMENTS (AUTHORITATIVE) ═══\n"
            "Facts about the subject itself come from here. Where this and the\n"
            "external context below disagree, this wins.\n\n"
            + owned.strip()
        )

    external = ""
    if state.get("use_search", False):
        try:
            # Scope the query to the brand. The topic alone is the user's
            # phrasing of an internal subject, and a title that collides with
            # a common noun sends the search somewhere else entirely: "SALVAGE
            # national release date" returned clinical papers on salvage
            # therapy, and the pipeline wrote a press release for the American
            # Urological Association. The brand name is the one token that
            # disambiguates which SALVAGE is meant.
            scoped_query = f"{brand_name} {topic}".strip() if brand_name else topic
            if scoped_query != topic:
                logger.info("Search query scoped to brand: %r", scoped_query)
            raw_results = search.search(query=scoped_query, max_results=5)
            external = LLMSingleton.get().invoke(
                RESEARCH_SUMMARY.format(
                    topic=topic,
                    content_type=content_type,
                    results=raw_results,
                    brand_context=brand_context
                )
            ).content or ""
        except Exception as e:
            logger.warning("Web search failed for topic=%r: %s", topic, e)

        if external.strip():
            sources.append(
                "═══ EXTERNAL CONTEXT — LIVE WEB SEARCH (SUPPORTING) ═══\n"
                "Current market/reception context to position the piece against.\n"
                "Use it for framing and timeliness. Do NOT use it to assert facts\n"
                "about the brand's own product that the source material above does\n"
                "not already establish.\n\n"
                + external.strip()
            )

    research = "\n\n".join(sources)

    if not research.strip():
        research = "No research available — write from the topic brief alone."

    mode = (
        "rag+web_search" if (owned.strip() and external.strip())
        else "web_search" if external.strip()
        else "rag" if owned.strip()
        else "none"
    )
    logger.info(
        "Research complete via %s for topic=%r (owned=%d chars, external=%d chars)",
        mode, topic, len(owned), len(external),
    )

    return {**state, "research": research}
