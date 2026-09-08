from pydantic import EmailStr, BaseModel, Field, field_validator
from typing import Optional

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
    use_search: bool = False
    webhook_url: Optional[str] = None 

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