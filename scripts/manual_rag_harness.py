"""
test_brand_rag.py -- Standalone harness to test BrandRAG in isolation.

Run:
    python test_brand_rag.py

What it does:
  1. Tests every chunking strategy (blog, social, ad, proposal) against
     synthetic brand documents — no real DB, no FastAPI, no Celery.
  2. Builds an in-memory LlamaIndex VectorStoreIndex (SimpleVectorStore)
     so you don't need Postgres running.
  3. Reports chunk counts, sizes, retrieval scores, and latency for each
     (content_type × embedding_model) combination.
  4. Lets you drop in your own documents via CUSTOM_DOCS at the top.

Dependencies already in pyproject.toml — nothing new to install.
"""

from __future__ import annotations

import io
import logging
import os
import sys
import time
import textwrap
from dataclasses import dataclass, field
from typing import Optional

# Force UTF-8 output on Windows (avoids cp1252 UnicodeEncodeError)
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── bootstrap logging before anything else ──────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s  %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("test_brand_rag")

# ── load .env so POSTGRES_URI / GOOGLE_API_KEY are available (not used here) ──
from dotenv import load_dotenv
load_dotenv()


# ════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION — edit these to customise the test run
# ════════════════════════════════════════════════════════════════════════════

# The queries that will be issued against every (content_type × strategy)
TEST_QUERIES = [
    "What is the brand voice and tone?",
    "What are the key value propositions?",
    "Who is the target audience?",
    "What makes this brand unique?",
]

# How many top chunks to retrieve per query
SIMILARITY_TOP_K = 3

# Which embedding model to use for the test  (local, no key needed)
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"

# ── Custom documents: map content_type → text ────────────────────────────────
# If you leave a value as None the built-in synthetic sample is used.
CUSTOM_DOCS: dict[str, Optional[str]] = {
    "blog":     None,
    "social":   None,
    "ad":       None,
    "proposal": None,
}

# ════════════════════════════════════════════════════════════════════════════
#  SYNTHETIC BRAND DOCUMENTS  (used when CUSTOM_DOCS[key] is None)
# ════════════════════════════════════════════════════════════════════════════

_SYNTHETIC: dict[str, str] = {
    "blog": textwrap.dedent("""\
        # The Future of Sustainable Fashion: Why BrandX Leads the Way

        At BrandX, we believe that style should never come at the expense of the
        planet. Our journey started in 2015 when our founders noticed a massive gap
        in the market: consumers wanted premium quality AND environmental
        responsibility, but the industry forced them to choose.

        ## Our Brand Voice
        We speak to conscious consumers who care deeply about where their clothes
        come from. Our tone is warm, informed, and occasionally playful — never
        preachy. We celebrate small wins alongside big milestones.

        ## Core Values
        - **Transparency**: Every product page shows the carbon footprint breakdown.
        - **Craftsmanship**: We work only with artisans who earn a living wage.
        - **Circularity**: Take-back programme since 2019; over 200 k garments recycled.

        ## Target Audience
        Our primary customer is aged 25–40, college-educated, urban, and
        sustainability-minded. They read Vogue Sustainability and follow climate
        accounts on Instagram. They spend intentionally and expect the same from us.

        ## What Sets Us Apart
        Unlike fast-fashion giants who bolt on a "green" line, sustainability is
        woven into every decision at BrandX — from the organic cotton farms in
        Portugal to our zero-waste packaging facility in Brooklyn.
    """),

    "social": textwrap.dedent("""\
        🌿 Small steps, big impact. Today we planted our 50,000th tree with
        @Ecosia. Every purchase you make funds one more. #BrandX #GoGreen

        ✨ New drop alert! Our autumn capsule collection is here — earth tones,
        timeless cuts, zero compromise. Link in bio. #SustainableFashion

        💬 "I've worn this jacket every day for 3 years and it still looks brand
        new." — Sarah M., verified buyer. Quality over quantity, always.

        🔁 We just hit 200,000 garments returned & recycled! Thank you for being
        part of the loop. Together we're rewriting what fashion means.

        📣 Brand Voice reminder: we're encouraging, never guilty-tripping. We meet
        our audience where they are and celebrate every conscious choice they make.
    """),

    "ad": textwrap.dedent("""\
        Style that stands for something.

        BrandX — premium sustainable fashion for people who refuse to compromise.

        From farm to wardrobe, every stitch is intentional.

        Shop the collection. Plant a tree. Make a statement.

        BrandX.com | Free returns | B-Corp Certified

        ---

        Headline A: "Fashion that loves the planet back."
        Headline B: "Wear your values."
        Headline C: "Quality that lasts. Impact that matters."

        CTA options: Shop Now / Explore the Collection / Join the Movement

        Tone: Aspirational, confident, warm — never greenwashy or preachy.
        Target: Eco-conscious millennials + Gen Z, household income $60 k+.
    """),

    "proposal": textwrap.dedent("""\
        # BrandX Partnership Proposal — Q3 2025

        ## Executive Summary
        BrandX is seeking a strategic retail partnership with GreenMart to expand
        our omnichannel footprint across the US Pacific Northwest. This proposal
        outlines a 12-month pilot covering 15 flagship GreenMart locations.

        ## Brand Overview
        Founded 2015 | Revenue $42 M (2024) | B-Corp Certified | Net-Positive
        Carbon since 2022.

        BrandX manufactures premium sustainable apparel using GOTS-certified
        organic cotton and recycled fibres. We sell DTC via brandx.com and through
        50 select retail partners globally.

        ## Value Proposition
        - Proven 38 % gross margin on wholesale (above category average of 31 %).
        - Existing 240 k loyalty members within GreenMart's drive zones.
        - Joint co-marketing budget of $500 k proposed for Year 1.
        - In-store circular take-back programme drives repeat foot traffic.

        ## Target Audience Alignment
        GreenMart's core shopper (F 28–45, sustainability index 8/10) matches
        BrandX's primary ICP within a 92 % overlap per Nielsen Spectra data.

        ## Proposed Terms
        - 60 / 40 revenue share (BrandX / GreenMart) on retail sales.
        - 90-day payment terms; quarterly sell-through reporting.
        - Exclusivity clause: BrandX will not onboard competing grocery retailers
          within GreenMart's defined trade zones for 24 months.

        ## Next Steps
        1. Term-sheet review by both legal teams (target: 30 days).
        2. Pilot store selection and planogram sign-off (target: 60 days).
        3. Soft launch in 3 pilot stores (target: Q4 2025).
    """),
}


# ════════════════════════════════════════════════════════════════════════════
#  IN-MEMORY RAG HARNESS  (bypasses Postgres & the full BrandRAG class)
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class ChunkStats:
    content_type: str
    strategy_name: str
    num_chunks: int
    avg_chunk_size: float
    min_chunk_size: int
    max_chunk_size: int
    chunk_size_param: int
    chunk_overlap_param: int


@dataclass
class QueryResult:
    query: str
    retrieved_texts: list[str]
    latency_ms: float
    num_results: int


@dataclass
class RunResult:
    content_type: str
    strategy_name: str
    embedding_model: str
    chunk_stats: ChunkStats
    query_results: list[QueryResult]
    index_build_ms: float


def _build_in_memory_index(docs_text: list[str], embedding, node_parser):
    """Build a LlamaIndex VectorStoreIndex backed by SimpleVectorStore (RAM only)."""
    from llama_index.core import VectorStoreIndex, Document
    from llama_index.core import StorageContext
    from llama_index.core.vector_stores import SimpleVectorStore

    documents = [Document(text=t) for t in docs_text]
    vector_store = SimpleVectorStore()
    storage_context = StorageContext.from_defaults(vector_store=vector_store)

    index = VectorStoreIndex.from_documents(
        documents,
        storage_context=storage_context,
        embed_model=embedding,
        transformations=[node_parser],
        show_progress=False,
    )
    return index


def run_strategy_test(
    content_type: str,
    document_text: str,
    embedding_model_name: str,
    queries: list[str],
    top_k: int,
) -> RunResult:
    from chunking_stategy import get_chunking_strategy
    from embedding_stategy import FastEmbedEmbedding
    from llama_index.core.node_parser import SimpleNodeParser

    strategy = get_chunking_strategy(content_type)
    strategy_name = strategy.__class__.__name__

    # ── Chunk the document and collect stats ─────────────────────────────────
    raw_chunks = strategy.chunk(document_text)
    sizes = [len(c) for c in raw_chunks]
    chunk_stats = ChunkStats(
        content_type=content_type,
        strategy_name=strategy_name,
        num_chunks=len(raw_chunks),
        avg_chunk_size=sum(sizes) / len(sizes) if sizes else 0,
        min_chunk_size=min(sizes) if sizes else 0,
        max_chunk_size=max(sizes) if sizes else 0,
        chunk_size_param=strategy.chunk_size,
        chunk_overlap_param=strategy.chunk_overlap,
    )

    # ── Build embedding + node parser (mirrors BrandRAG._build) ─────────────
    embed_wrapper = FastEmbedEmbedding(model=embedding_model_name)
    llamaindex_embed = embed_wrapper.to_llamaindex()

    node_parser = SimpleNodeParser.from_defaults(
        chunk_size=strategy.chunk_size,
        chunk_overlap=strategy.chunk_overlap,
    )

    # ── Build in-memory index ────────────────────────────────────────────────
    t0 = time.perf_counter()
    index = _build_in_memory_index([document_text], llamaindex_embed, node_parser)
    index_build_ms = (time.perf_counter() - t0) * 1000

    retriever = index.as_retriever(similarity_top_k=top_k)

    # ── Run queries ──────────────────────────────────────────────────────────
    query_results: list[QueryResult] = []
    for q in queries:
        t1 = time.perf_counter()
        nodes = retriever.retrieve(q)
        latency_ms = (time.perf_counter() - t1) * 1000
        query_results.append(QueryResult(
            query=q,
            retrieved_texts=[n.text for n in nodes],
            latency_ms=latency_ms,
            num_results=len(nodes),
        ))

    return RunResult(
        content_type=content_type,
        strategy_name=strategy_name,
        embedding_model=embedding_model_name,
        chunk_stats=chunk_stats,
        query_results=query_results,
        index_build_ms=index_build_ms,
    )


# ===========================================================================
#  PRETTY PRINTER
# ===========================================================================

_SEP = "-" * 72
_THICK = "=" * 72

def _header(title: str):
    print(f"\n{_THICK}")
    print(f"  {title}")
    print(_THICK)

def _section(title: str):
    print(f"\n{_SEP}")
    print(f"  {title}")
    print(_SEP)

def print_run_result(result: RunResult):
    cs = result.chunk_stats
    _header(
        f"content_type={result.content_type!r}  |  "
        f"strategy={result.strategy_name}  |  "
        f"embed={result.embedding_model}"
    )

    # Chunk stats
    print(f"\nCHUNK STATISTICS")
    print(f"    chunk_size param : {cs.chunk_size_param}")
    print(f"    chunk_overlap    : {cs.chunk_overlap_param}")
    print(f"    chunks produced  : {cs.num_chunks}")
    print(f"    avg size (chars) : {cs.avg_chunk_size:.0f}")
    print(f"    min / max (chars): {cs.min_chunk_size} / {cs.max_chunk_size}")
    print(f"    index build time : {result.index_build_ms:.0f} ms")

    # Query results
    print(f"\nRETRIEVAL RESULTS")
    for qr in result.query_results:
        print(f"\n  Query : {qr.query!r}")
        print(f"  Found : {qr.num_results} node(s)  |  latency: {qr.latency_ms:.0f} ms")
        for i, text in enumerate(qr.retrieved_texts, 1):
            preview = text.replace("\n", " ").strip()[:120]
            print(f"    [{i}] {preview}{'…' if len(text) > 120 else ''}")


def print_comparison_table(results: list[RunResult]):
    _header("COMPARISON TABLE — all strategies")
    header = (
        f"{'content_type':<12} {'strategy':<22} "
        f"{'chunks':>6} {'avg_sz':>7} "
        f"{'build_ms':>9} {'avg_q_ms':>9}"
    )
    print(f"\n{header}")
    print("─" * len(header))
    for r in results:
        avg_q = sum(qr.latency_ms for qr in r.query_results) / len(r.query_results)
        print(
            f"{r.content_type:<12} {r.strategy_name:<22} "
            f"{r.chunk_stats.num_chunks:>6} "
            f"{r.chunk_stats.avg_chunk_size:>7.0f} "
            f"{r.index_build_ms:>9.0f} "
            f"{avg_q:>9.0f}"
        )


# ════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ════════════════════════════════════════════════════════════════════════════

def main():
    content_types = list(_SYNTHETIC.keys())  # ["blog", "social", "ad", "proposal"]

    logger.info("Starting BrandRAG isolation test")
    logger.info("Embedding model : %s", EMBEDDING_MODEL)
    logger.info("Content types   : %s", content_types)
    logger.info("Queries per type: %d", len(TEST_QUERIES))

    all_results: list[RunResult] = []

    for ct in content_types:
        doc_text = CUSTOM_DOCS.get(ct) or _SYNTHETIC[ct]
        logger.info("Testing content_type=%r  (doc length=%d chars)", ct, len(doc_text))

        try:
            result = run_strategy_test(
                content_type=ct,
                document_text=doc_text,
                embedding_model_name=EMBEDDING_MODEL,
                queries=TEST_QUERIES,
                top_k=SIMILARITY_TOP_K,
            )
            print_run_result(result)
            all_results.append(result)
        except Exception as exc:
            logger.error("FAILED for content_type=%r: %s", ct, exc, exc_info=True)

    if len(all_results) > 1:
        print_comparison_table(all_results)

    logger.info("Done.")


if __name__ == "__main__":
    main()
