from pydantic import EmailStr, BaseModel, Field, field_validator
from typing import Optional

class GenerateRequest(BaseModel):
    business_id: str
    content_type: str = Field(
        ..., pattern="^(blog|social|ad|proposal|press_release|trailer_copy|talent_bio|synopsis)$"
    )
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
    content_type: str = Field(
        ..., pattern="^(blog|social|ad|proposal|press_release|trailer_copy|talent_bio|synopsis)$"
    )
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