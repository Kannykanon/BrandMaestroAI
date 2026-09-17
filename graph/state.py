from typing import TypedDict, Optional

class GraphState(TypedDict):
    # Input
    business_id: str
    content_type: str
    topic: str
    format_type: str
    user_id: Optional[int]
    use_search: bool
    # "both" | "rag" | "web". Which research sources the researcher may
    # draw on: the brand's own product documents through RAG, Parallel's
    # web search, or both. use_search above is the older boolean and is
    # kept in step with this for callers that still send it.
    research_mode: str

    webhook_url: Optional[str]
    human_feedback: Optional[str]
    # How many reject -> regenerate hops led to this run. 0 for a first pass.
    regeneration_depth: Optional[int]
    # The rejected draft this run was asked to replace, if any.
    parent_generation_id: Optional[str]


    # Researcher output
    research: str

    # Writer output
    content: str
    # The best draft this run has produced, and what it scored. A revision is
    # not an improvement by definition: one run scored 6.3, 6.3, 7.5, 7.5, 6.0
    # and then failed a deterministic gate, and the draft delivered was the last
    # one rather than the 7.5. Carried forward so the round that wandered cannot
    # cost the run the round that worked.
    best_content: Optional[str]
    best_score: Optional[float]
    best_iteration: Optional[int]
    best_was_clean: Optional[bool]
    creative_angle: str
    iteration: int

    # Enforcer output
    approved: bool
    score: float
    feedback: str
    flagged_passages: str
    # Every gate this generation has already been corrected for. Carried
    # across revision rounds so the writer stops trading one violation for
    # another: see the standing-constraints block in WRITER_REVISION.
    violation_history: list[str]
    style_match: float
    tone_match: float
    structure_match: float
    signature_match: float

    # Final output
    generation_id: str
    status: str
