"""Search-only check: a brand with NO uploaded documents at all.

The scenario: a user wants copy about a product or brand but has nothing to
upload. Everything the piece is built from has to come from live web search.

Every grounding surface the pipeline normally relies on is empty here — no RAG
index, no extracted metrics, no Brand Brain, no asset bank, no structural
examples — so this exercises whether the system degrades gracefully or falls
over. Each stage is probed individually before the full graph runs, so a
failure points at the stage that caused it.

Needs Postgres + Redis, GOOGLE_API_KEY and PARALLEL_API_KEY.

    python scripts/run_search_only_check.py
"""
import logging
import os
import sys
import traceback
import uuid

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s %(message)s")

SEP = "=" * 78
RESULTS = []


def banner(t):
    print(f"\n{SEP}\n{t}\n{SEP}", flush=True)


def stage(label, fn):
    """Run one stage, recording pass/fail instead of aborting the whole script."""
    try:
        out = fn()
        print(f"  PASS  {label}")
        RESULTS.append((label, True))
        return out
    except Exception as e:
        print(f"  FAIL  {label}\n          {type(e).__name__}: {e}")
        traceback.print_exc()
        RESULTS.append((label, False))
        return None


def main():
    from database import User, get_db_session, init_db
    from auth import hash_password

    banner("SETUP — a brand with zero uploaded documents")
    init_db()
    with get_db_session() as session:
        user = session.query(User).filter(User.username == "searchonly_demo").first()
        if not user:
            user = User(
                first_name="Search", last_name="Only", username="searchonly_demo",
                email="demo@searchonly.test",
                password=hash_password("demo-password-not-a-secret"),
            )
            session.add(user)
            session.commit()
            session.refresh(user)
        business_id, user_id = user.business_id, user.id

    from database import BrandDocument, BrandMetrics
    with get_db_session() as session:
        docs = session.query(BrandDocument).filter_by(business_id=business_id).count()
        mets = session.query(BrandMetrics).filter_by(business_id=business_id).count()
    print(f"  business_id = {business_id}")
    print(f"  documents   = {docs}   metrics rows = {mets}   (both must be 0)")

    content_type = "blog"

    banner("STAGE PROBES — each grounding surface with nothing behind it")

    from graph.deps import resolve_deps
    rag, analyzer, memory = stage("resolve_deps on an empty business",
                                  lambda: resolve_deps(business_id, content_type)) or (None, None, None)

    if rag is not None:
        r = stage("rag.query() with no index", lambda: rag.query("anything at all"))
        print(f"          -> returned {type(r).__name__}, {len(r or '')} chars")

    if analyzer is not None:
        ctx = stage("analyzer.get_context() with no metrics", lambda: analyzer.get_context())
        print(f"          -> returned {len((ctx or ''))} chars")

        from utils.brand_profile import extract_permitted_claims
        from utils.enforcement import run_preflight_checks
        from utils.brand_profile import extract_asset_bank, extract_brand_name, extract_section
        c = ctx or ""
        stage("_extract_permitted_claims on empty brain", lambda: extract_permitted_claims(c))
        stage("_extract_brand_name on empty brain", lambda: extract_brand_name(c))
        stage("_extract_asset_bank on empty brain", lambda: extract_asset_bank(c))
        stage("_extract_section on empty brain", lambda: extract_section(c, "OPENING PATTERN"))
        stage("_run_preflight_checks on empty brain",
              lambda: run_preflight_checks("Some sample copy. It has sentences.", c))

    if memory is not None:
        stage("memory.get_patterns() with no history", lambda: memory.get_patterns(content_type))

    banner("FULL GRAPH — use_search=True, nothing uploaded")

    def run_graph():
        from graph.graph import build_graph
        from graph.state import GraphState
        from search import ParallelSearch

        graph = build_graph(ParallelSearch(api_key=os.environ["PARALLEL_API_KEY"]))
        state = GraphState(
            business_id=business_id, content_type=content_type,
            topic="The rise of standing desks in remote work setups and whether they actually help",
            format_type="short blog post", user_id=user_id, use_search=True,
            human_feedback="", regeneration_depth=0, webhook_url=None,
            research="", content="", creative_angle="", iteration=0, approved=False,
            feedback="", flagged_passages="", score=0.0, style_match=0.0, tone_match=0.0,
            structure_match=0.0, signature_match=0.0,
            generation_id=str(uuid.uuid4()), status="pending",
        )
        return graph.invoke(state)

    final = stage("full graph run with use_search=True and no documents", run_graph)

    if final:
        research = final.get("research") or ""
        content = final.get("content") or ""
        print(f"\n  research sections : owned={'YES' if 'OWN DOCUMENTS' in research else 'no'}  "
              f"web={'YES' if 'LIVE WEB SEARCH' in research else 'no'}")
        print(f"  research size     : {len(research)} chars")
        print(f"  approved          : {final.get('approved')}  score={final.get('score')}/10  "
              f"iterations={final.get('iteration')}")
        print(f"  content length    : {len(content)} chars")
        print("\n  --- GENERATED ---")
        print("  " + content.replace("\n", "\n  "))

        RESULTS.append(("produced non-empty content", len(content.strip()) > 0))
        RESULTS.append(("content is not a system error placeholder",
                        "[System Error" not in content))
        RESULTS.append(("web search supplied the research", "LIVE WEB SEARCH" in research))

    banner("VERDICT")
    passed = sum(1 for _, ok in RESULTS if ok)
    for label, ok in RESULTS:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    print(f"\n  {passed}/{len(RESULTS)} checks passed")


if __name__ == "__main__":
    main()
