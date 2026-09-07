"""Validation after the prompt reorder, telemetry, and doc_role separation.

Three things to confirm, in order of risk:

  1. Reordering the enforcer/writer prompts (static instructions first, so a
     cacheable prefix exists) did not change scoring behaviour.
  2. Reclassifying the Nightfall product document as role="reference" removes
     it from the Brand Brain, so a press kit stops teaching Meridian its voice.
  3. Token telemetry now reports real numbers, including whether any prompt
     caching is happening.

    python scripts/run_post_change_validation.py
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

SEP = "=" * 78
DOWNLOADS = r"C:\Users\HomePC\Downloads"


def banner(t):
    print(f"\n{SEP}\n{t}\n{SEP}", flush=True)


def toks(t):
    return re.findall(r"[a-z0-9']+", t.lower())


def fingerprint(text):
    words = toks(text)
    letters = [c for c in text if c.isalpha()]
    return {
        "excl": round(100.0 * text.count("!") / max(len(words), 1), 1),
        "upper": round(sum(1 for c in letters if c.isupper()) / max(len(letters), 1), 2),
        "we": len(re.findall(r"\b(we|we're|our|us|we'd|we've)\b", text, re.I)),
    }


def generate(business_id, user_id, content_type, topic, format_type, use_search=False):
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


def main():
    from database import (User, BrandDocument, BrandMetrics, get_db_session, init_db,
                          DOC_ROLE_REFERENCE)
    from brand_metrics import BrandMetricsSQL
    from model import TokenUsage

    banner("SETUP")
    init_db()
    with get_db_session() as ses:
        mer = ses.query(User).filter(User.username == "meridian_demo").first()
        mer_bid, mer_uid = mer.business_id, mer.id
    print(f"  meridian business_id = {mer_bid}")

    # ---------------------------------------------------------------- #
    banner("PART 1 — reclassify the product document as role=reference")

    with get_db_session() as ses:
        doc = ses.query(BrandDocument).filter(
            BrandDocument.business_id == mer_bid,
            BrandDocument.filename.like("product-document%"),
        ).first()
        if not doc:
            print("  product document not found — skipping")
            doc_id, content_type = None, None
        else:
            doc_id, content_type = doc.id, doc.content_type
            print(f"  doc id={doc_id} '{doc.filename}' role {doc.doc_role!r} -> {DOC_ROLE_REFERENCE!r}")
            doc.doc_role = DOC_ROLE_REFERENCE
            ses.commit()

    if doc_id is not None:
        with get_db_session() as ses:
            removed = ses.query(BrandMetrics).filter_by(
                business_id=mer_bid, doc_id=doc_id
            ).delete()
            ses.commit()
        print(f"  removed {removed} voice profile row(s) sourced from the product document")

        an = BrandMetricsSQL(business_id=mer_bid, content_type=content_type)
        an.invalidate_cache(soft=False)
        brain = an.build_and_cache_context()
        print(f"  rebuilt '{content_type}' brand brain: {len(brain)} chars "
              f"(was 12360 with the product doc in it)")

    # ---------------------------------------------------------------- #
    banner("PART 2 — press release, now voice-only brain + product doc via RAG")
    TokenUsage.reset()
    pr = generate(
        mer_bid, mer_uid, "press_release",
        "Nightfall Protocol season one announcement", "press release",
    )
    print(f"  approved={pr.get('approved')}  score={pr.get('score')}/10  "
          f"iterations={pr.get('iteration')}  elapsed={pr['_elapsed']:.1f}s")
    print(f"  token usage: {TokenUsage.snapshot()}")
    print("\n  --- PRESS RELEASE ---")
    print("  " + (pr.get("content") or "").replace("\n", "\n  "))

    src_pr = open(os.path.join(DOWNLOADS, "brand-voice-press-release.txt"), encoding="utf-8").read()
    print(f"\n  fingerprint  source press release: {fingerprint(src_pr)}")
    print(f"  fingerprint  generated:            {fingerprint(pr.get('content') or '')}")

    # ---------------------------------------------------------------- #
    banner("PART 3 — social regression (baseline was 10.0/10, style 1.0, 1 iteration)")
    TokenUsage.reset()
    soc = generate(
        mer_bid, mer_uid, "social",
        "Teaser campaign for our new series Nightfall Protocol", "social caption set",
    )
    print(f"  approved={soc.get('approved')}  score={soc.get('score')}/10  "
          f"style={soc.get('style_match')}  iterations={soc.get('iteration')}  "
          f"elapsed={soc['_elapsed']:.1f}s")
    print(f"  token usage: {TokenUsage.snapshot()}")
    print("\n  --- SOCIAL ---")
    print("  " + (soc.get("content") or "").replace("\n", "\n  "))

    # ---------------------------------------------------------------- #
    banner("VERDICT")
    checks = [
        ("press release still generates and is approved", bool(pr.get("approved"))),
        ("press release scores >= 7.0", float(pr.get("score") or 0) >= 7.0),
        ("social still generates and is approved", bool(soc.get("approved"))),
        ("social scores >= 8.0 (baseline 10.0)", float(soc.get("score") or 0) >= 8.0),
        ("telemetry reported real token counts", TokenUsage.snapshot()["prompt_tokens"] > 0),
    ]
    for label, cond in checks:
        print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    print(f"\n  {sum(1 for _, c in checks if c)}/{len(checks)} passed")
    print(f"\n  cumulative token usage this run: {TokenUsage.snapshot()}")
    print("  cached_tokens = 0 means Gemini re-billed every repeated prefix in full.")


if __name__ == "__main__":
    main()
