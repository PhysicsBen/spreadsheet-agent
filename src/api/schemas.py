"""Pydantic v2 request/response models for the FastAPI layer."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from core.config import settings


class SessionListItem(BaseModel):
    session_id: UUID
    filename: str
    created_at: datetime
    file_size_bytes: int


class SessionResponse(SessionListItem):
    workbook_meta: dict[str, Any]


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=settings.max_question_chars)
    thread_id: UUID | None = None


class QuerySource(BaseModel):
    table_id: str
    sheet: str
    tool_used: str


class QueryResponse(BaseModel):
    answer: str
    thread_id: UUID
    session_id: UUID
    model: str
    tokens_used: int
    sources: list[QuerySource] = Field(default_factory=list)
    needs_clarification: bool = False
