"""Cost demo: what the Brand Brain cache actually saves, measured in tokens.

The claim being demonstrated: the Brand Brain is synthesized ONCE from a
brand's past content and then reused. Later generations read it from Redis
instead of paying to rebuild it, so the per-generation cost drops after the
first run and stays down until new documents arrive.

Measured, not asserted. Token counts come from the Gemini SDK boundary (see
model.install_token_capture), so every number below is what was actually
billed rather than an estimate.

  RUN 1  cold cache — Brand Brain must be synthesized, then generate
  RUN 2  warm cache — Brand Brain read from Redis, generate a DIFFERENT topic
                      (different topic so nothing is served from the response
                       cache; this isolates the Brand Brain saving alone)

It also reports Gemini context-cache hits, which are currently zero — see the
note printed at the end. Do not claim prompt caching from this script.

    python scripts/run_cost_demo.py [username]
"""
import logging
import os
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
USERNAME = sys.argv[1] if len(sys.argv) > 1 else "meridian_demo"
CONTENT_TYPE = "social"
TOPIC_A = "Teaser campaign for our new series Nightfall Protocol"
TOPIC_B = "Announcing the return of our flagship series for another season"


def banner(t):
    print(f"\n{SEP}\n{t}\n{SEP}", flush=True)


def generate(business_id, user_id, topic):
    from graph.graph import build_graph
    from graph.state import GraphState
    from search import ParallelSearch

    graph = build_graph(ParallelSearch(api_key=os.environ["PARALLEL_API_KEY"]))
    state = GraphState(
        business_id=business_id, content_type=CONTENT_TYPE, topic=topic,
        format_type="social caption set", user_id=user_id, use_search=False,
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


def report(label, usage, elapsed, final=None):
    print(f"\n  {label}")
    print(f"    LLM calls        : {usage['calls']}")
    print(f"    prompt tokens    : {usage['prompt_tokens']:,}")
    print(f"    output tokens    : {usage['output_tokens']:,}")
    print(f"    cached tokens    : {usage['cached_tokens']:,}")
    print(f"    elapsed          : {elapsed:.1f}s")
    if final is not None:
        print(f"    score            : {final.get('score')}/10  "
              f"iterations={final.get('iteration')}  approved={final.get('approved')}")


def main():
    from database import User, get_db_session, init_db
    from brand_metrics import BrandMetricsSQL
    from model import TokenUsage

    init_db()
    with get_db_session() as ses:
        user = ses.query(User).filter(User.username == USERNAME).first()
        if not user:
            print(f"No user {USERNAME!r}. Pass a username as the first argument.")
            return
        business_id, user_id = user.business_id, user.id

    analyzer = BrandMetricsSQL(business_id=business_id, content_type=CONTENT_TYPE)

    banner(f"RUN 1 — COLD: Brand Brain must be built ({USERNAME}/{CONTENT_TYPE})")
    analyzer.invalidate_cache(soft=False)   # hard eviction, nothing servable
    TokenUsage.reset()
    t0 = time.monotonic()
    brain = analyzer.build_and_cache_context()
    synth_elapsed = time.monotonic() - t0
    synthesis = TokenUsage.snapshot()
    print(f"  Brand Brain synthesized: {len(brain):,} chars in {synth_elapsed:.1f}s")
    report("Brand Brain synthesis alone", synthesis, synth_elapsed)

    TokenUsage.reset()
    run1 = generate(business_id, user_id, TOPIC_A)
    gen1 = TokenUsage.snapshot()
    report("Generation with the brain now warm", gen1, run1["_elapsed"], run1)

    banner("RUN 2 — WARM: Brand Brain read from cache, new topic")
    TokenUsage.reset()
    run2 = generate(business_id, user_id, TOPIC_B)
    gen2 = TokenUsage.snapshot()
    report("Generation (no synthesis needed)", gen2, run2["_elapsed"], run2)

    banner("WHAT THE CACHE SAVED")
    first_total = synthesis["prompt_tokens"] + synthesis["output_tokens"]
    print(f"  Brand Brain synthesis cost, paid ONCE:")
    print(f"    {synthesis['prompt_tokens']:,} prompt + {synthesis['output_tokens']:,} output "
          f"= {first_total:,} tokens, {synth_elapsed:.1f}s")
    print(f"\n  Every later generation reuses that brain from Redis:")
    print(f"    synthesis calls on run 2 : 0")
    print(f"    tokens avoided per run   : {first_total:,}")
    print(f"    seconds avoided per run  : {synth_elapsed:.1f}")
    if first_total:
        g2 = gen2["prompt_tokens"] + gen2["output_tokens"]
        print(f"\n  Without the cache, every run would pay synthesis on top of generation:")
        print(f"    generation only        : {g2:,} tokens")
        print(f"    generation + synthesis : {g2 + first_total:,} tokens")
        print(f"    saving per generation  : {100.0 * first_total / (g2 + first_total):.0f}%")

    banner("SELF-HEALING LOOP")
    for label, r in (("RUN 1", run1), ("RUN 2", run2)):
        iters = r.get("iteration")
        print(f"  {label}: {iters} iteration(s), final score {r.get('score')}/10, "
              f"approved={r.get('approved')}")
        if iters and iters > 1:
            print(f"     -> the Enforcer rejected {iters - 1} draft(s) and the Writer revised")
    print("\n  Cache TTL: 24h in Redis, plus a Postgres copy that survives a Redis flush.")
    print("  A new uploaded document invalidates it and queues ONE debounced rebuild,")
    print("  so ten uploads cost one synthesis, not ten.")

    banner("HONEST NOTE ON PROMPT CACHING")
    total_cached = synthesis["cached_tokens"] + gen1["cached_tokens"] + gen2["cached_tokens"]
    print(f"  Gemini context-cache hits across all three phases: {total_cached}")
    if total_cached == 0:
        print("  Zero. Gemini is re-billing every repeated prefix in full.")
        print("  The prompts are now ORDERED for caching (static instructions first,")
        print("  then brand-stable blocks, then per-call material), but the pinned")
        print("  google-generativeai 0.5.4 predates the caching API, so nothing is")
        print("  cached yet. Do NOT claim prompt-level caching savings in the demo —")
        print("  the Brand Brain saving above is real and measured; this one is not.")


if __name__ == "__main__":
    main()
