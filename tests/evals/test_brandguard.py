"""
tests/evals/test_brandguard.py
──────────────────────────────────────────────────────────────────────────────
DeepEval build-loop eval suite for BrandGuard AI (LangGraph).

Integration:  deepeval.integrations.langchain.CallbackHandler
              Auto-traces every node, LLM call, and tool call — no @observe.

Run:
    deepeval test run tests/evals/test_brandguard.py
or:
    pytest tests/evals/test_brandguard.py -v
──────────────────────────────────────────────────────────────────────────────
"""
import os
import sys
import pytest
from unittest.mock import MagicMock
from functools import partial
from dotenv import load_dotenv

load_dotenv()

# ── DeepEval imports ──────────────────────────────────────────────────────────
from deepeval import assert_test
from deepeval.dataset import EvaluationDataset, Golden
from deepeval.test_case import LLMTestCase, LLMTestCaseParams
from deepeval.metrics import GEval, AnswerRelevancyMetric, FaithfulnessMetric
from deepeval.models import DeepEvalBaseLLM

# ── App imports (root on sys.path via conftest.py) ────────────────────────────
from model import ChatGemini
from graph.graph import build_graph
from search import ParallelSearch
from brand_rag import BrandRAG
from brand_metrics import BrandMetricsSQL
from learning_memory import FeedbackPortSQL
import nodes.deployer
from contextlib import contextmanager

@contextmanager
def _mock_db_session():
    yield MagicMock()

nodes.deployer.get_db_session = _mock_db_session


# ═══════════════════════════════════════════════════════════════════════════════
# 1.  Gemini-backed evaluator LLM for DeepEval metrics
# ═══════════════════════════════════════════════════════════════════════════════

class GeminiEvaluatorLLM(DeepEvalBaseLLM):
    """
    Wraps langchain-google-genai so DeepEval metrics can use Gemini.
    Supports optional structured output (Pydantic schema) for GEval internals.
    """

    def __init__(self):
        from langchain_google_genai import ChatGoogleGenerativeAI
        self._llm = ChatGoogleGenerativeAI(
            model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            google_api_key=os.getenv("GOOGLE_API_KEY", ""),
            temperature=0,
            max_output_tokens=4096,
        )

    def load_model(self):
        return self._llm

    def generate(self, prompt: str, schema=None):
        from langchain_core.messages import HumanMessage
        if schema is not None:
            structured = self._llm.with_structured_output(schema)
            result = structured.invoke([HumanMessage(content=prompt)])
            if isinstance(result, dict):
                return schema(**result)
            return result
        return self._llm.invoke([HumanMessage(content=prompt)]).content

    async def a_generate(self, prompt: str, schema=None):
        from langchain_core.messages import HumanMessage
        if schema is not None:
            structured = self._llm.with_structured_output(schema)
            result = await structured.ainvoke([HumanMessage(content=prompt)])
            # result may be a dict — wrap it in the schema if so
            if isinstance(result, dict):
                return schema(**result)
            return result
        result = await self._llm.ainvoke([HumanMessage(content=prompt)])
        return result.content

    def get_model_name(self) -> str:
        return "gemini/gemini-2.5-flash"


# Singleton — instantiated once at collection time
_evaluator = GeminiEvaluatorLLM()


# ═══════════════════════════════════════════════════════════════════════════════
# 2.  Mocked infrastructure dependencies
#     (replaces Postgres / PGVector / Redis so evals run without Docker)
# ═══════════════════════════════════════════════════════════════════════════════

BRAND_VOICE_EXAMPLES = """
Brand Voice Examples (BrandGuard AI — "EcoThread"):

Blog:
  "We believe every stitch tells a story. When you wear EcoThread, you're
   wearing a commitment — to the planet, to craft, and to you."

Social:
  "Big news. Our new collection just dropped — and it's made entirely from
   ocean-recovered fibres. Wear the change. Shop now."

Email:
  "Hi [Name], here's what our community built this month. Every purchase
   you made planted a tree. Together, we've planted 12,000 of them."
"""

BRAND_METRICS_JSON = """{
  "tone": "warm, conversational, purposeful",
  "perspective": "first-person plural (we/our) + second-person (you/your)",
  "style": "short punchy sentences, active voice, no jargon",
  "signature_phrases": ["we believe", "our community", "together", "you deserve", "wear the change"],
  "formatting": "short paragraphs, ends with inclusive CTA or community stat",
  "avoid": ["passive voice", "third-person corporate speak", "feature dumps"]
}"""


def _mock_search() -> MagicMock:
    s = MagicMock(spec=ParallelSearch)
    s.search.return_value = (
        "Consumer research 2024: 78 % of shoppers say brand authenticity "
        "influences purchase decisions. Sustainability messaging lifts "
        "engagement by 34 % on social channels."
    )
    return s


def _mock_rag() -> MagicMock:
    r = MagicMock(spec=BrandRAG)
    r.query.return_value = BRAND_VOICE_EXAMPLES
    return r


def _mock_analyzer() -> MagicMock:
    a = MagicMock(spec=BrandMetricsSQL)
    a.get_context.return_value = BRAND_METRICS_JSON
    return a


def _mock_memory() -> MagicMock:
    m = MagicMock(spec=FeedbackPortSQL)
    m.get_patterns.return_value = {
        "approved": [
            {"angle": "community-first storytelling"},
            {"angle": "benefit-led openings with emotional hook"},
        ],
        "rejected": [
            {"angle": "feature dump without human context"},
        ],
    }
    m.save.return_value = None
    return m


# ═══════════════════════════════════════════════════════════════════════════════
# 3.  App LLM + compiled graph  (built once per session)
# ═══════════════════════════════════════════════════════════════════════════════

_app_llm = ChatGemini(
    model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
    api_key=os.getenv("GOOGLE_API_KEY", ""),
    temperature=0.7,
)

import graph.deps as _deps_module

_mocked_rag = _mock_rag()
_mocked_analyzer = _mock_analyzer()
_mocked_memory = _mock_memory()

_deps_module.resolve_deps = lambda business_id, content_type: (
    _mocked_rag, _mocked_analyzer, _mocked_memory
)

_graph = build_graph(search=_mock_search())


# ═══════════════════════════════════════════════════════════════════════════════
# 4.  Goldens — one per content-generation scenario
# ═══════════════════════════════════════════════════════════════════════════════

GOLDENS = [
    Golden(
        input="Write a blog post about our new sustainable packaging initiative",
        expected_output=(
            "A warm, community-focused blog post using we/our and you/your perspective, "
            "short active sentences, no jargon, mentioning sustainability and community "
            "impact, ending with an inclusive call-to-action."
        ),
        context=[BRAND_VOICE_EXAMPLES],
    ),
    Golden(
        input="Write a social media post announcing our latest product launch",
        expected_output=(
            "A punchy, action-oriented social post using we/you perspective, "
            "short sentences, active voice, ending with a call-to-action that "
            "includes the reader."
        ),
        context=[BRAND_VOICE_EXAMPLES],
    ),
    Golden(
        input="Write an email to customers about our improved support response times",
        expected_output=(
            "A professional yet warm email using we/you perspective, "
            "stating specific improvements clearly, grateful tone toward the community."
        ),
        context=[BRAND_VOICE_EXAMPLES],
    ),
]

dataset = EvaluationDataset(goldens=GOLDENS)


# ═══════════════════════════════════════════════════════════════════════════════
# 5.  DeepEval metrics
# ═══════════════════════════════════════════════════════════════════════════════

brand_voice_metric = GEval(
    name="Brand Voice Consistency",
    criteria=(
        "Evaluate whether the generated marketing content matches the brand voice.\n"
        "Brand voice rules:\n"
        "  1. Uses we/our and you/your perspective — NOT third-person.\n"
        "  2. Warm, conversational tone — NOT corporate or robotic.\n"
        "  3. Short, punchy sentences — no long complex constructions.\n"
        "  4. Active voice throughout.\n"
        "  5. Ends with or contains a community-inclusive call-to-action.\n"
        "Score 1.0 if all five criteria are clearly met.\n"
        "Score 0.0 if the majority of criteria fail."
    ),
    evaluation_params=[
        LLMTestCaseParams.INPUT,
        LLMTestCaseParams.ACTUAL_OUTPUT,
        LLMTestCaseParams.EXPECTED_OUTPUT,
    ],
    threshold=0.7,
    model=_evaluator,
)

answer_relevancy_metric = AnswerRelevancyMetric(
    threshold=0.7,
    model=_evaluator,
    include_reason=True,
)

faithfulness_metric = FaithfulnessMetric(
    threshold=0.7,
    model=_evaluator,
    include_reason=True,
)


# ═══════════════════════════════════════════════════════════════════════════════
# 6.  Graph runner helper
# ═══════════════════════════════════════════════════════════════════════════════

_CONTENT_TYPES = {
    0: "blog",
    1: "social_media",
    2: "email",
}


def _run_graph(golden: Golden, idx: int) -> str:
    """
    Invoke the compiled LangGraph with a CallbackHandler so every node/LLM call
    is traced automatically by DeepEval's LangGraph integration.
    Returns the final generated content string.
    """
    initial_state = {
        "business_id": "ecothread-001",
        "content_type": _CONTENT_TYPES.get(idx, "blog"),
        "topic": golden.input,
        "format_type": "standard",
        "user_id": None,
        "use_search": False,          # RAG path → exercises researcher→writer→enforcer
        "document_context": "",
        "research": "",
        "content": "",
        "creative_angle": "",
        "iteration": 0,
        "approved": False,
        "feedback": "",
        "score": 0.0,
        "generation_id": "",
        "status": "",
    }

    result = _graph.invoke(
        initial_state,
        config={},
        
    )
    return result.get("content", "")


# ═══════════════════════════════════════════════════════════════════════════════
# 7.  Pytest test functions  (deepeval test run picks these up)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("golden,idx", list(zip(dataset.goldens, range(len(dataset.goldens)))))
def test_brand_voice_consistency(golden: Golden, idx: int):
    """
    METRIC: Brand Voice Consistency (GEval, threshold=0.7)
    Checks tone, perspective, sentence style, active voice, CTA.
    """
    content = _run_graph(golden, idx)
    test_case = LLMTestCase(
        input=golden.input,
        actual_output=content,
        expected_output=golden.expected_output,
        retrieval_context=golden.context or [],
    )
    assert_test(test_case=test_case, metrics=[brand_voice_metric])


@pytest.mark.parametrize("golden,idx", list(zip(dataset.goldens, range(len(dataset.goldens)))))
def test_answer_relevancy(golden: Golden, idx: int):
    """
    METRIC: Answer Relevancy (threshold=0.7)
    Checks the output actually addresses the requested topic/task.
    """
    content = _run_graph(golden, idx)
    test_case = LLMTestCase(
        input=golden.input,
        actual_output=content,
    )
    assert_test(test_case=test_case, metrics=[answer_relevancy_metric])


@pytest.mark.parametrize("golden,idx", list(zip(dataset.goldens, range(len(dataset.goldens)))))
def test_faithfulness(golden: Golden, idx: int):
    """
    METRIC: Faithfulness (threshold=0.7)
    Checks content doesn't hallucinate beyond the retrieved brand examples context.
    """
    content = _run_graph(golden, idx)
    test_case = LLMTestCase(
        input=golden.input,
        actual_output=content,
        retrieval_context=golden.context or [BRAND_VOICE_EXAMPLES],
    )
    assert_test(test_case=test_case, metrics=[faithfulness_metric])
