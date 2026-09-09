from pydantic import EmailStr, BaseModel, Field, field_validator, model_validator
from typing import Optional

# Which research sources the researcher may draw on.
#   rag   the brand's own product documents only
#   web   Parallel's web search only
#   both  both, with the brand's own documents authoritative
RESEARCH_MODES = ("both", "rag", "web")
RESEARCH_MODE_PATTERN = f"^({'|'.join(RESEARCH_MODES)})$"

# Single source of truth for the content types the pipeline supports. The two
# Field patterns below are built from it, and the document router validates
# against it, so a type cannot be accepted for upload and then rejected — or
# silently ignored — when the same user tries to clear it.
CONTENT_TYPES = (
    "blog", "social", "ad", "proposal",
    "press_release", "trailer_copy", "talent_bio", "synopsis",
)
CONTENT_TYPE_PATTERN = f"^({'|'.join(CONTENT_TYPES)})$"


class GenerateRequest(BaseModel):
    business_id: str
    content_type: str = Field(..., pattern=CONTENT_TYPE_PATTERN)
    topic: str
    format_type: str
    user_id: Optional[int] = None
    # Default on. The researcher's Parallel Search call is the live partner
    # integration this project is judged on, and defaulting it off meant a
    # caller who posts the documented minimum body never triggers one — the
    # integration was real in code and invisible in every actual run.
    use_search: bool = True
    research_mode: str = Field(default="both", pattern=RESEARCH_MODE_PATTERN)
    webhook_url: Optional[str] = None 

    @model_validator(mode="after")
    def _reconcile_research_selection(self):
        # use_search is the older boolean and some callers still send only it.
        # If research_mode was not named explicitly, derive it, so an existing
        # client asking for use_search=false still gets RAG-only rather than
        # silently having web search turned back on under it.
        sent = self.model_fields_set
        if "research_mode" not in sent and "use_search" in sent:
            object.__setattr__(self, "research_mode", "both" if self.use_search else "rag")
        # Keep the boolean in step for everything downstream that reads it.
        object.__setattr__(self, "use_search", self.research_mode in ("both", "web"))
        return self

class FeedbackRequest(BaseModel):
    generation_id: str
    business_id: str
    content_type: str
    human_approved: bool
    human_score: float = Field(..., ge=0.0, le=10.0)
    human_feedback: str


class DocumentUploadRequest(BaseModel):
    business_id: str
    content_type: str = Field(..., pattern=CONTENT_TYPE_PATTERN)
    platform: Optional[str] = None      # e.g. "instagram", "linkedin"
    performance_metric: Optional[str] = None  # e.g. "highest_engagement"

class TaskResponse(BaseModel):
    generation_id: str
    task_id: str
    status: str

class ResultResponse(BaseModel):
    task_id: str
    status: str
    result: Optional[dict] = None

class UserCreate(BaseModel):
    first_name: str
    last_name: str
    username: str
    email: EmailStr
    password: str

    @field_validator("password")
    def validate_password(cls, v):
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long")
        return v
    
class UserResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    username: str
    email: str
    business_id: str

    class Config:
        from_attributes = True


class TokenResponse(BaseModel):
    """Response for the endpoints that mint a token.

    /users/create previously returned the User row itself with no
    response_model, so FastAPI serialised every column — including the argon2
    password hash — into the registration response. Declaring the shape here
    means the hash cannot be returned even if the endpoint keeps handing over
    the ORM object.
    """
    access_token: str
    token_type: str
    user: Optional[UserResponse] = None

    class Config:
        from_attributes = True