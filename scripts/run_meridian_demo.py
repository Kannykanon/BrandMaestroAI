"""End-to-end demo run against the Meridian Studios sample documents.

Run A: ingest the three brand-voice documents, then generate with
       use_search=True so the Researcher calls the Parallel Search API.
Run B: ingest the Nightfall Protocol product document, then generate with
       use_search=False so the Researcher grounds in RAG over that document.

Needs Postgres + Redis up and GOOGLE_API_KEY / PARALLEL_API_KEY set.
Runs the graph in-process; Celery and RabbitMQ are not involved.

    python scripts/run_meridian_demo.py
"""
import logging
import os
import sys
import time
import uuid

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
# Quiet the noisy third-party loggers so the run is readable.
for noisy in ("httpx", "urllib3", "sqlalchemy.engine", "fastembed", "llama_index"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

# Imported at module level (script startup) rather than inside generate(),
# since importing opik itself costs ~8-10s — that should land once while the
# script is starting up, not silently in the middle of "RUN A".
from observability import get_trace_callbacks

DOWNLOADS = r"C:\Users\HomePC\Downloads"

VOICE_DOCS = [
    ("brand-voice-blog-post.txt", "blog"),
    ("brand-voice-press-release.txt", "press_release"),
    ("brand-voice-social-captions.txt", "social"),
    ("brand-voice-proposal.txt", "proposal"),
]
PRODUCT_DOC = ("product-document-nightfall-protocol.txt", "press_release")

SEP = "=" * 78


def banner(text):
    print(f"\n{SEP}\n{text}\n{SEP}", flush=True)


def read_doc(filename):
    path = os.path.join(DOWNLOADS, filename)
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def ensure_user():
    """Create (or reuse) the Meridian Studios account and return its business_id."""
    from database import User, get_db_session
    from auth import hash_password

    with get_db_session() as session:
        user = session.query(User).filter(User.username == "meridian_demo").first()
        if user:
            return user.business_id, user.id

        user = User(
            first_name="Meridian",
            last_name="Studios",
            username="meridian_demo",
            email="demo@meridian.test",
            password=hash_password("demo-password-not-a-secret"),
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        return user.business_id, user.id


def ingest(business_id, user_id, filename, content_type, extract_voice=True):
    """Mirror what POST /documents/top-performing does for one file."""
    from brand_metrics import BrandMetricsSQL
    from brand_rag import BrandRAG
    from database import BrandDocument, get_db_session
    from embedding_stategy import GoogleEmbedding

    content = read_doc(filename)

    with get_db_session() as session:
        doc = BrandDocument(
            user_id=user_id,
            business_id=business_id,
            content_type=content_type,
            file_content=content,
            filename=filename,
        )
        session.add(doc)
        session.commit()
        session.refresh(doc)
        doc_id = doc.id

    rag = BrandRAG(
        business_id=business_id,
        content_type=content_type,
        embedding=GoogleEmbedding(),
    )
    rag.refresh(content)

    analyzer = BrandMetricsSQL(business_id=business_id, content_type=content_type)
    extracted = False
    if extract_voice:
        extracted = analyzer.extract_and_save(doc_id=doc_id, doc_content=content)
        analyzer.invalidate_cache()

    print(
        f"  ingested {filename:<42} type={content_type:<14} "
        f"chars={len(content):<6} voice_extracted={extracted}",
        flush=True,
    )
    return doc_id


def synthesize(business_id, content_type):
    from brand_metrics import BrandMetricsSQL

    analyzer = BrandMetricsSQL(business_id=business_id, content_type=content_type)
    brain = analyzer.build_and_cache_context()
    print(f"  brand brain [{content_type}]: {len(brain)} chars", flush=True)
    return brain


def generate(business_id, user_id, content_type, topic, format_type, use_search):
    from graph.graph import build_graph
    from graph.state import GraphState
    from search import ParallelSearch

    search = ParallelSearch(api_key=os.environ["PARALLEL_API_KEY"])
    graph = build_graph(search)

    state = GraphState(
        business_id=business_id,
        content_type=content_type,
        topic=topic,
        format_type=format_type,
        user_id=user_id,
        use_search=use_search,
        human_feedback="",
        webhook_url=None,
        research="",
        content="",
        creative_angle="",
        iteration=0,
        approved=False,
        feedback="",
        flagged_passages="",
        score=0.0,
        style_match=0.0,
        tone_match=0.0,
        structure_match=0.0,
        signature_match=0.0,
        generation_id=str(uuid.uuid4()),
        status="pending",
    )

    trace_callbacks = get_trace_callbacks(
        graph, tags=[content_type, "web_search" if use_search else "rag"]
    )

    started = time.monotonic()
    final = graph.invoke(state, config={"callbacks": trace_callbacks})
    elapsed = time.monotonic() - started

    print(f"\n  research source : {'Parallel web search' if use_search else 'RAG over uploaded docs'}")
    print(f"  elapsed         : {elapsed:.1f}s")
    print(f"  iterations      : {final.get('iteration')}")
    print(f"  approved        : {final.get('approved')}")
    print(f"  score           : {final.get('score')}/10")
    print(
        f"  dimensions      : style={final.get('style_match')} tone={final.get('tone_match')} "
        f"structure={final.get('structure_match')} signature={final.get('signature_match')}"
    )
    print(f"  creative angle  : {final.get('creative_angle')}")

    print(f"\n  --- RESEARCH CONTEXT (first 700 chars) ---")
    print("  " + (final.get("research") or "")[:700].replace("\n", "\n  "))

    print(f"\n  --- GENERATED CONTENT ---")
    print("  " + (final.get("content") or "").replace("\n", "\n  "))

    if final.get("feedback"):
        print(f"\n  --- ENFORCER FEEDBACK ---")
        print("  " + final["feedback"][:900].replace("\n", "\n  "))

    return final


def reset(business_id):
    """Clear previous demo data so repeated runs start clean."""
    import re

    import redis
    from sqlalchemy import text

    from database import BrandDocument, BrandMetrics, Generation, get_db_session

    with get_db_session() as session:
        for model in (Generation, BrandMetrics, BrandDocument):
            deleted = session.query(model).filter_by(business_id=business_id).delete()
            if deleted:
                print(f"  cleared {deleted} row(s) from {model.__tablename__}")
        session.commit()

        # llama-index creates one table per (business_id, content_type),
        # prefixed with data_ by PGVectorStore.
        safe = re.sub(r"[^a-z0-9_]", "_", business_id.lower())
        rows = session.execute(text(
            "SELECT tablename FROM pg_tables WHERE tablename LIKE :pat"
        ), {"pat": f"data_vectors_{safe}%"}).fetchall()
        for (table,) in rows:
            session.execute(text(f'DROP TABLE IF EXISTS "{table}" CASCADE'))
            print(f"  dropped vector table {table}")
        session.commit()

    r = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    keys = r.keys(f"*{business_id}*")
    if keys:
        r.delete(*keys)
        print(f"  cleared {len(keys)} redis key(s)")


def main():
    from database import init_db

    banner("SETUP")
    init_db()
    business_id, user_id = ensure_user()
    print(f"  business_id = {business_id}")
    print(f"  user_id     = {user_id}")
    reset(business_id)

    banner("PHASE 1 - ingest Meridian brand voice documents")
    for filename, content_type in VOICE_DOCS:
        ingest(business_id, user_id, filename, content_type)

    print()
    for _, content_type in VOICE_DOCS:
        synthesize(business_id, content_type)

    banner("RUN A - generate WITH Parallel web search (use_search=True)")
    run_a = generate(
        business_id=business_id,
        user_id=user_id,
        content_type="social",
        topic=(
            "Teaser campaign for our new series Nightfall Protocol, tapping into "
            "the current wave of interest in slow-burn conspiracy thrillers"
        ),
        format_type="social caption set",
        use_search=True,
    )

    banner("PHASE 2 - ingest the Nightfall Protocol product document")
    ingest(business_id, user_id, PRODUCT_DOC[0], PRODUCT_DOC[1])
    synthesize(business_id, PRODUCT_DOC[1])

    banner("RUN B - generate WITHOUT web search, grounded in the product doc (use_search=False)")
    run_b = generate(
        business_id=business_id,
        user_id=user_id,
        content_type="press_release",
        topic="Nightfall Protocol season one announcement",
        format_type="press release",
        use_search=False,
    )

    banner("SUMMARY")
    for label, result in (("RUN A (Parallel)", run_a), ("RUN B (RAG)", run_b)):
        print(
            f"  {label:<20} approved={result.get('approved')!s:<6} "
            f"score={result.get('score')}/10  iterations={result.get('iteration')}"
        )


if __name__ == "__main__":
    main()
