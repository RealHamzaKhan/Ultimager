"""Pydantic schemas for request / response validation."""
from pydantic import BaseModel, Field
from typing import Optional


class SessionCreate(BaseModel):
    title: str
    description: str
    rubric: str
    max_score: int = Field(default=100, ge=1)
    reference_solution: Optional[str] = None
    test_cases: Optional[str] = None
    run_command: Optional[str] = None


class OverridePayload(BaseModel):
    score: float = Field(..., ge=0)
    comments: Optional[str] = None
    is_reviewed: bool = False


class GradingProgress(BaseModel):
    session_id: int
    status: str
    total_students: int
    graded_count: int
    current_student: Optional[str] = None
