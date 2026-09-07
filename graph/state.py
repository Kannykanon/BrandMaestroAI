from typing import TypedDict, Optional

class GraphState(TypedDict):
    # Input
    business_id: str
    content_type: str
    topic: str
    format_type: str
    user_id: Optional[int]
    use_search: bool

    webhook_url: Optional[str]
    human_feedback: Optional[str]
    # How many reject -> regenerate hops led to this run. 0 for a first pass.
    regeneration_depth: Optional[int]


    # Researcher output
    research: str

    # Writer output
    content: str
    creative_angle: str
    iteration: int

    # Enforcer output
    approved: bool
    score: float
    feedback: str
    flagged_passages: str
    style_match: float
    tone_match: float
    structure_match: float
    signature_match: float

    # Final output
    generation_id: str
    status: str
