"""Hybrid research check: product document (RAG) + live web research (Parallel).

The scenario this covers is the realistic one. We already hold the authoritative
product document for a title, and we ALSO want current outside context — how the
genre is being received right now, what comparable titles are doing — folded into
the new piece.

Before this, `use_search` was either/or: turning search on discarded the product
document entirely. researcher_node now always runs RAG over the brand's own
documents and treats web search as additive context on top.

Three runs, so the difference is attributable:
    A. RAG only          (use_search=False)
    B. Web search + RAG  (use_search=True)   <- the hybrid
and both are measured for extractive copying against their own source.

Needs Postgres + Redis, GOOGLE_API_KEY and PARALLEL_API_KEY.

    python scripts/run_hybrid_research_check.py
"""
import logging
import os
import re
import sys
import time
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

from utils.enforcement import MAX_VERBATIM_SPAN_WORDS, find_extractive_spans

DOWNLOADS = r"C:\Users\HomePC\Downloads"
PRODUCT_DOC = "product-document-nightfall-protocol.txt"
SEP = "=" * 78


def banner(t):
    print(f"\n{SEP}\n{t}\n{SEP}", flush=True)


def toks(t):
    return re.findall(r"[a-z0-9']+", t.lower())


def ngram_overlap(gen, src, n):
    g = {tuple(toks(gen)[i:i + n]) for i in range(max(len(toks(gen)) - n + 1, 0))}
    s = {tuple(toks(src)[i:i + n]) for i in range(max(len(toks(src)) - n + 1, 0))}
    return 100.0 * len(g & s) / len(g) if g else 0.0


def run(business_id, user_id, use_search, topic, format_type="press release",
        content_type="press_release"):
    from graph.graph import build_graph
    from graph.state import GraphState
    from search import ParallelSearch

    graph = build_graph(ParallelSearch(api_key=os.environ["PARALLEL_API_KEY"]))
    state = GraphState(
        business_id=business_id, content_type=content_type, topic=topic,
        format_type=format_type, user_id=user_id, use_search=use_search,
        human_feedback="", regeneration_depth=0, webhook_url=None,
        research="", content="", creative_angle="", iteration=0, approved=False,
        feedback="", flagged_passages="", score=0.0, style_match=0.0, tone_match=0.0,
        structure_match=0.0, signature_match=0.0,
        generation_id=str(uuid.uuid4()), status="pending",
    )
    t0 = time.monotonic()
    final = graph.invoke(state)
    final["_elapsed"] = time.monotonic() - t0
    return final


def report(label, final, product_doc):
    research = final.get("research") or ""
    content = final.get("content") or ""

    has_owned = "BRAND'S OWN DOCUMENTS" in research
    has_web = "LIVE WEB SEARCH" in research

    print(f"\n  {label}")
    print(f"    research sections : owned={'YES' if has_owned else 'no'}  web={'YES' if has_web else 'no'}")
    print(f"    research size     : {len(research)} chars")
    print(f"    elapsed           : {final['_elapsed']:.1f}s")
    print(f"    approved          : {final.get('approved')}   score={final.get('score')}/10   "
          f"iterations={final.get('iteration')}")

    spans = find_extractive_spans(content, product_doc)
    print(f"    extractive spans vs product doc (> {MAX_VERBATIM_SPAN_WORDS} words): {len(spans)}")
    for s in spans[:3]:
        print(f"       ({s['length']}w) {s['text'][:90]!r}")
    for n in (4, 5, 8):
        print(f"    {n}-gram overlap vs product doc: {ngram_overlap(content, product_doc, n):5.1f}%")
    return spans


def main():
    from database import User, get_db_session, init_db

    banner("SETUP")
    init_db()
    with get_db_session() as ses:
        u = ses.query(User).filter(User.username == "meridian_demo").first()
        business_id, user_id = u.business_id, u.id
    print(f"  meridian business_id = {business_id}")

    with open(os.path.join(DOWNLOADS, PRODUCT_DOC), encoding="utf-8") as fh:
        product_doc = fh.read()

    topic = ("Nightfall Protocol season one announcement, positioned against the current "
             "wave of interest in slow-burn conspiracy thrillers")

    banner("RUN A — RAG only (use_search=False)")
    a = run(business_id, user_id, use_search=False, topic=topic)
    spans_a = report("RAG only", a, product_doc)
    print("\n  --- CONTENT (A) ---")
    print("  " + (a.get("content") or "").replace("\n", "\n  "))

    banner("RUN B — HYBRID: product document + live Parallel web search (use_search=True)")
    b = run(business_id, user_id, use_search=True, topic=topic)
    spans_b = report("Hybrid", b, product_doc)

    research_b = b.get("research") or ""
    if "LIVE WEB SEARCH" in research_b:
        ext = research_b.split("LIVE WEB SEARCH")[1]
        print("\n  --- EXTERNAL CONTEXT RETRIEVED (first 600 chars) ---")
        print("  " + ext[:600].replace("\n", "\n  "))

    print("\n  --- CONTENT (B) ---")
    print("  " + (b.get("content") or "").replace("\n", "\n  "))

    banner("VERDICT")
    checks = [
        ("hybrid run used BOTH sources",
         "BRAND'S OWN DOCUMENTS" in research_b and "LIVE WEB SEARCH" in research_b),
        ("RAG-only run still grounded in the product doc",
         "BRAND'S OWN DOCUMENTS" in (a.get("research") or "")),
        ("hybrid research is richer than RAG-only",
         len(research_b) > len(a.get("research") or "")),
        ("no extractive copying in RAG-only output", len(spans_a) == 0),
        ("no extractive copying in hybrid output", len(spans_b) == 0),
    ]
    passed = 0
    for label, cond in checks:
        print(f"  {'PASS' if cond else 'FAIL'}  {label}")
        passed += bool(cond)
    print(f"\n  {passed}/{len(checks)} checks passed")


if __name__ == "__main__":
    main()
