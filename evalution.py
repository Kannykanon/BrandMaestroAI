import pytest
from deepeval import evaluate
from deepeval.metrics import (
    AnswerRelevancyMetric,
    FaithfulnessMetric,
    ContextualRelevancyMetric,
    GEval
)
from deepeval.test_case import LLMTestCase, LLMTestCaseParams
from deepeval.dataset import EvaluationDataset

from model import LLMSingleton

model = LLMSingleton.get()
# ---------------------------------------------------------------------------
# Brand Voice Consistency — Custom GEval
# ---------------------------------------------------------------------------

brand_voice_metric = GEval(
    name="Brand Voice Consistency",
    criteria="""Evaluate whether the generated content matches the brand voice
    demonstrated in the brand examples provided.
    
    Consider:
    1. Tone — does it match the brand tone (formal/casual/authoritative)?
    2. Style — does sentence structure match brand patterns?
    3. Vocabulary — does it use similar language and phrases?
    4. Perspective — does it use the same perspective (we/you/they)?
    5. Personality — does it feel like the same brand?
    
    Score 1 if content clearly matches brand voice.
    Score 0 if content deviates significantly from brand voice.""",
    evaluation_params=[
        LLMTestCaseParams.INPUT,
        LLMTestCaseParams.ACTUAL_OUTPUT,
        LLMTestCaseParams.EXPECTED_OUTPUT
    ],
    threshold=0.7
)


# ---------------------------------------------------------------------------
# RAG Retrieval Quality
# ---------------------------------------------------------------------------

contextual_relevancy_metric = ContextualRelevancyMetric(
    threshold=0.7,
    model = model,
    include_reason=True
)


# ---------------------------------------------------------------------------
# Content Faithfulness
# ---------------------------------------------------------------------------

faithfulness_metric = FaithfulnessMetric(
    threshold=0.7,
    model=model,
    include_reason=True
)


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

# Brand voice test cases
brand_voice_test_cases = [
    LLMTestCase(
        input="Write a blog post about our new product launch",
        actual_output="""
            We're thrilled to introduce our latest innovation — a product 
            designed with you in mind. After months of listening to your 
            feedback, we've built something that truly makes a difference.
            Here's what you need to know.
        """,
        expected_output="""
            We are excited to share our newest product with our community.
            Built from your insights and shaped by your needs, this launch
            marks a new chapter for us. Let us walk you through it.
        """,
        retrieval_context=[
            """Our brand always speaks directly to our customers using 
            'we' and 'you'. We maintain a warm, conversational tone while 
            staying professional. We celebrate milestones with our community."""
        ]
    ),
    LLMTestCase(
        input="Write a social media post about our sustainability initiative",
        actual_output="""
            Big news — we're going green. Our new sustainability initiative 
            means every product you buy plants a tree. Together, we're 
            building a better tomorrow. Join us.
        """,
        expected_output="""
            Exciting update from our team. We have launched our sustainability
            program. Every purchase contributes to a greener planet. 
            We are committed to making a difference with you by our side.
        """,
        retrieval_context=[
            """Brand voice is action-oriented and community focused. 
            We use short punchy sentences for social content. 
            Always end with a call to action that includes the reader."""
        ]
    )
]

# RAG retrieval test cases
rag_test_cases = [
    LLMTestCase(
        input="What is the brand tone for blog content?",
        actual_output="""
            The brand tone for blog content is warm and conversational 
            while maintaining professionalism. Content uses first person 
            plural (we/our) and directly addresses the reader as 'you'.
            Sentences are clear and concise, avoiding jargon.
        """,
        retrieval_context=[
            """Blog content should feel like a conversation with a knowledgeable 
            friend. Use we and you throughout. Keep sentences under 20 words. 
            Avoid technical jargon unless explaining it immediately after.""",
            """Our brand voice across all content types prioritizes clarity 
            and warmth. We never talk down to our audience. Every piece of 
            content should leave the reader feeling informed and valued."""
        ]
    ),
    LLMTestCase(
        input="How should we structure an ad for a product launch?",
        actual_output="""
            Product launch ads should lead with the key benefit, follow with 
            a brief explanation, and end with a clear call to action. 
            Keep copy under 50 words for digital ads. Use active voice throughout.
        """,
        retrieval_context=[
            """Ad copy formula: Hook (benefit first) → Brief explanation → 
            CTA. Digital ads max 50 words. Print ads max 100 words. 
            Always active voice. Never start with 'We' in ad headlines.""",
            """Successful past ads focused on customer transformation not 
            product features. Lead with what changes for the customer, 
            not what the product does technically."""
        ]
    )
]

# Faithfulness test cases
faithfulness_test_cases = [
    LLMTestCase(
        input="Write an ad about our eco-friendly packaging",
        actual_output="""
            Our new packaging is 100% recyclable and made from 
            post-consumer materials. Every box saves 2 liters of water 
            compared to traditional packaging. Good for you, great for 
            the planet.
        """,
        retrieval_context=[
            """New packaging initiative details: 100% recyclable materials,
            sourced from post-consumer waste. Each unit production saves 
            approximately 2 liters of water versus industry standard packaging.
            Certified by GreenSeal organization."""
        ]
    ),
    LLMTestCase(
        input="Write a blog intro about our customer support improvements",
        actual_output="""
            This year we reduced our average response time from 24 hours 
            to just 2 hours. Our support team grew by 40% and we introduced 
            live chat across all platforms. Here is what changed and why.
        """,
        retrieval_context=[
            """Customer support improvements 2024: Response time reduced from 
            24 hours to 2 hours average. Team size increased by 40%. 
            Live chat launched on website, mobile app and social channels."""
        ]
    )
]


# ---------------------------------------------------------------------------
# Evaluation Datasets
# ---------------------------------------------------------------------------

brand_voice_dataset = EvaluationDataset(test_cases=brand_voice_test_cases)
rag_dataset         = EvaluationDataset(test_cases=rag_test_cases)
faithfulness_dataset = EvaluationDataset(test_cases=faithfulness_test_cases)


# ---------------------------------------------------------------------------
# Pytest tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("test_case", brand_voice_test_cases)
def test_brand_voice_consistency(test_case):
    """
    Evaluates whether generated content matches brand voice
    demonstrated in brand documents.
    """
    assert brand_voice_metric.measure(test_case) >= 0.7, (
        f"Brand voice consistency failed: {brand_voice_metric.reason}"
    )


@pytest.mark.parametrize("test_case", rag_test_cases)
def test_rag_retrieval_quality(test_case):
    """
    Evaluates whether RAG retrieves contextually relevant
    chunks for the given query.
    """
    assert contextual_relevancy_metric.measure(test_case) >= 0.7, (
        f"RAG retrieval quality failed: {contextual_relevancy_metric.reason}"
    )


@pytest.mark.parametrize("test_case", faithfulness_test_cases)
def test_content_faithfulness(test_case):
    """
    Evaluates whether generated content stays faithful
    to the retrieved context without hallucinating.
    """
    assert faithfulness_metric.measure(test_case) >= 0.7, (
        f"Faithfulness failed: {faithfulness_metric.reason}"
    )


# ---------------------------------------------------------------------------
# Full evaluation run
# ---------------------------------------------------------------------------

def run_full_evaluation():
    """
    Run all evaluations and print summary report.
    """
    print("\n" + "="*60)
    print("BRANDGUARD AI — DEEPEVAL EVALUATION REPORT")
    print("="*60)

    # Brand voice
    print("\n1. BRAND VOICE CONSISTENCY")
    print("-"*40)
    brand_voice_results = evaluate(
        test_cases=brand_voice_test_cases,
        metrics=[brand_voice_metric]
    )
    for result in brand_voice_results:
        print(f"Score: {result.metrics_data[0].score:.2f}")
        print(f"Reason: {result.metrics_data[0].reason}")

    # RAG quality
    print("\n2. RAG RETRIEVAL QUALITY")
    print("-"*40)
    rag_results = evaluate(
        test_cases=rag_test_cases,
        metrics=[contextual_relevancy_metric]
    )
    for result in rag_results:
        print(f"Score: {result.metrics_data[0].score:.2f}")
        print(f"Reason: {result.metrics_data[0].reason}")

    # Faithfulness
    print("\n3. CONTENT FAITHFULNESS")
    print("-"*40)
    faithfulness_results = evaluate(
        test_cases=faithfulness_test_cases,
        metrics=[faithfulness_metric]
    )
    for result in faithfulness_results:
        print(f"Score: {result.metrics_data[0].score:.2f}")
        print(f"Reason: {result.metrics_data[0].reason}")

    print("\n" + "="*60)
    print("EVALUATION COMPLETE")
    print("="*60)


if __name__ == "__main__":
    run_full_evaluation()