"""Generalization / overfitting check.

Ingests a second brand whose voice is the deliberate opposite of Meridian's
(VOLT: exclamation marks, emoji, Title Case, second person, hard CTAs) under a
separate business_id but the SAME content_type, then:

  1. compares the two synthesized Brand Brains for divergence
  2. generates a social post for VOLT
  3. measures the generated output against both brands' mechanical signatures

The question it answers: does the pipeline learn a per-brand voice, or has it
absorbed one house style that every brand comes out sounding like?

Needs Postgres + Redis up and GOOGLE_API_KEY set. Runs the graph in-process.

    python scripts/run_generalization_check.py
"""
import logging
import os
import re
import sys
import uuid

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

# This script deliberately handles brands that use emoji, and Windows redirects
# stdout as cp1252 — which raises UnicodeEncodeError the moment such output is
# printed. Force UTF-8 so the log survives the content it is meant to inspect.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
for noisy in ("httpx", "urllib3", "sqlalchemy.engine", "fastembed", "llama_index", "opik"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

from observability import get_trace_callbacks

DOWNLOADS = r"C:\Users\HomePC\Downloads"

VOLT_DOCS = [
    "contrast-volt-social-1.txt",
    "contrast-volt-social-2.txt",
    "contrast-volt-social-3.txt",
]
CONTENT_TYPE = "social"
SEP = "=" * 78


def banner(text):
    print(f"\n{SEP}\n{text}\n{SEP}", flush=True)


# --------------------------------------------------------------------------- #
#  Mechanical fingerprint — brand-agnostic, computed the same way for both      #
# --------------------------------------------------------------------------- #
EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF\U0001F1E6-\U0001F1FF]"
)


def fingerprint(text: str) -> dict:
    words = text.split()
    letters = [c for c in text if c.isalpha()]
    caps_words = [w for w in words if len(w) > 2 and w.isupper()]
    return {
        "exclamations_per_100w": 100.0 * text.count("!") / max(len(words), 1),
        "emoji_count": len(EMOJI.findall(text)),
        "caps_words": len(caps_words),
        "uppercase_ratio": (sum(1 for c in letters if c.isupper()) / max(len(letters), 1)),
        "second_person": len(re.findall(r"\b(you|your|you're|yours)\b", text, re.I)),
        "first_person_plural": len(re.findall(r"\b(we|we're|our|us|we'd|we've)\b", text, re.I)),
    }


def show_fingerprint(label, fp):
    print(
        f"  {label:<22} "
        f"excl/100w={fp['exclamations_per_100w']:5.1f}  "
        f"emoji={fp['emoji_count']:3d}  "
        f"CAPS={fp['caps_words']:3d}  "
        f"upper={fp['uppercase_ratio']:.2f}  "
        f"you={fp['second_person']:3d}  "
        f"we={fp['first_person_plural']:3d}",
        flush=True,
    )


def read_doc(filename):
    with open(os.path.join(DOWNLOADS, filename), "r", encoding="utf-8") as fh:
        return fh.read()


def ensure_user(username, first, last, email):
    from database import User, get_db_session
    from auth import hash_password

    with get_db_session() as session:
        user = session.query(User).filter(User.username == username).first()
        if user:
            return user.business_id, user.id
        user = User(
            first_name=first, last_name=last, username=username, email=email,
            password=hash_password("demo-password-not-a-secret"),
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        return user.business_id, user.id


def ingest(business_id, user_id, filename, content_type):
    from brand_metrics import BrandMetricsSQL
    from brand_rag import BrandRAG
    from database import BrandDocument, get_db_session
    from embedding_stategy import build_embedding

    content = read_doc(filename)
    with get_db_session() as session:
        doc = BrandDocument(
            user_id=user_id, business_id=business_id, content_type=content_type,
            file_content=content, filename=filename,
        )
        session.add(doc)
        session.commit()
        session.refresh(doc)
        doc_id = doc.id

    BrandRAG(business_id=business_id, content_type=content_type,
             embedding=build_embedding()).refresh(content)
    analyzer = BrandMetricsSQL(business_id=business_id, content_type=content_type)
    extracted = analyzer.extract_and_save(doc_id=doc_id, doc_content=content)
    analyzer.invalidate_cache()
    print(f"  ingested {filename:<32} chars={len(content):<5} extracted={extracted}", flush=True)


def get_brain(business_id, content_type):
    from brand_metrics import BrandMetricsSQL
    return BrandMetricsSQL(business_id=business_id, content_type=content_type).get_context()


def generate(business_id, user_id, content_type, topic, format_type):
    from graph.graph import build_graph
    from graph.state import GraphState
    from search import ParallelSearch

    graph = build_graph(ParallelSearch(api_key=os.environ["PARALLEL_API_KEY"]))
    state = GraphState(
        business_id=business_id, content_type=content_type, topic=topic,
        format_type=format_type, user_id=user_id, use_search=False,
        human_feedback="", regeneration_depth=0, webhook_url=None,
        research="", content="", creative_angle="", iteration=0, approved=False,
        feedback="", flagged_passages="", score=0.0, style_match=0.0,
        tone_match=0.0, structure_match=0.0, signature_match=0.0,
        generation_id=str(uuid.uuid4()), status="pending",
    )
    final = graph.invoke(state, config={"callbacks": get_trace_callbacks(graph, tags=[content_type])})
    print(f"  approved={final.get('approved')}  score={final.get('score')}/10  "
          f"iterations={final.get('iteration')}", flush=True)
    return final


def brain_terms(brain: str) -> set:
    """Distinctive vocabulary of a brand brain, for overlap comparison."""
    stop = {
        "the", "and", "a", "to", "of", "is", "in", "for", "it", "that", "with", "as",
        "this", "on", "or", "are", "be", "not", "an", "by", "from", "at", "each",
        "brand", "content", "pattern", "style", "tone", "structure", "sentence",
        "use", "uses", "used", "using", "e", "g", "eg", "i", "s", "t",
    }
    words = re.findall(r"[a-z]{4,}", brain.lower())
    return {w for w in words if w not in stop}


def main():
    from database import init_db

    banner("SETUP")
    init_db()
    mer_bid, mer_uid = ensure_user("meridian_demo", "Meridian", "Studios", "demo@meridian.test")
    volt_bid, volt_uid = ensure_user("volt_demo", "Volt", "Energy", "demo@volt.test")
    print(f"  meridian business_id = {mer_bid}")
    print(f"  volt     business_id = {volt_bid}")

    banner("PHASE 1 - ingest VOLT (opposite voice, same content_type)")
    for filename in VOLT_DOCS:
        ingest(volt_bid, volt_uid, filename, CONTENT_TYPE)

    banner("PHASE 2 - source corpus fingerprints (ground truth)")
    mer_source = read_doc("brand-voice-social-captions.txt")
    volt_source = "\n".join(read_doc(f) for f in VOLT_DOCS)
    mer_fp_src, volt_fp_src = fingerprint(mer_source), fingerprint(volt_source)
    show_fingerprint("MERIDIAN source", mer_fp_src)
    show_fingerprint("VOLT source", volt_fp_src)

    banner("PHASE 3 - brand brain divergence")
    mer_brain = get_brain(mer_bid, CONTENT_TYPE)
    volt_brain = get_brain(volt_bid, CONTENT_TYPE)
    print(f"  meridian brain: {len(mer_brain)} chars")
    print(f"  volt     brain: {len(volt_brain)} chars")

    mt, vt = brain_terms(mer_brain), brain_terms(volt_brain)
    jaccard = len(mt & vt) / max(len(mt | vt), 1)
    print(f"\n  vocabulary overlap (Jaccard): {jaccard:.3f}")
    print(f"  terms only in MERIDIAN brain: {len(mt - vt)}")
    print(f"  terms only in VOLT brain:     {len(vt - mt)}")

    for label, brain in (("MERIDIAN", mer_brain), ("VOLT", volt_brain)):
        low = brain.lower()
        print(f"\n  {label} brain mentions:")
        for probe in ("exclamation", "emoji", "lowercase", "call to action",
                      "self-deprecat", "second person", "hedg"):
            print(f"    {probe:<18} {'YES' if probe in low else 'no'}")

    banner("PHASE 4 - generate for VOLT")
    final = generate(
        volt_bid, volt_uid, CONTENT_TYPE,
        topic="Launch of our new Blue Raspberry flavor, available nationwide this Friday",
        format_type="social caption set",
    )
    out = final.get("content") or ""
    print("\n  --- GENERATED (VOLT) ---")
    print("  " + out.replace("\n", "\n  "))

    banner("PHASE 5 - VERDICT: does VOLT output match VOLT, or Meridian?")
    out_fp = fingerprint(out)
    show_fingerprint("MERIDIAN source", mer_fp_src)
    show_fingerprint("VOLT source", volt_fp_src)
    show_fingerprint("VOLT generated", out_fp)

    checks = [
        ("uses exclamation marks like VOLT, not Meridian",
         out_fp["exclamations_per_100w"] > 1.0 and mer_fp_src["exclamations_per_100w"] == 0),
        ("uses emoji like VOLT, not Meridian",
         out_fp["emoji_count"] > 0 and mer_fp_src["emoji_count"] == 0),
        ("second-person dominant like VOLT",
         out_fp["second_person"] >= out_fp["first_person_plural"]),
        ("brains are not near-duplicates (Jaccard < 0.6)", jaccard < 0.6),
    ]
    print()
    passed = 0
    for label, cond in checks:
        print(f"  {'PASS' if cond else 'FAIL'}  {label}")
        passed += bool(cond)
    print(f"\n  {passed}/{len(checks)} generalization checks passed")


if __name__ == "__main__":
    main()
