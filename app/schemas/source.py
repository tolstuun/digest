import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, field_validator

VALID_SOURCE_TYPES = {"rss", "api", "html", "manual", "newsletter"}


def _validate_source_type(v: str) -> str:
    if v not in VALID_SOURCE_TYPES:
        raise ValueError(f"type must be one of: {', '.join(sorted(VALID_SOURCE_TYPES))}")
    return v


class SourceCreate(BaseModel):
    name: str
    type: str
    url: Optional[str] = None
    enabled: bool = True
    tags: Optional[list[str]] = None
    language: Optional[str] = None
    geography: Optional[str] = None
    priority: int = 0
    notes: Optional[str] = None
    parser_type: Optional[str] = None
    poll_frequency_minutes: Optional[int] = None
    section_scope: Optional[list[str]] = None

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        return _validate_source_type(v)


class SourcePatch(BaseModel):
    """All fields optional — only provided fields are updated."""

    name: Optional[str] = None
    type: Optional[str] = None
    url: Optional[str] = None
    enabled: Optional[bool] = None
    tags: Optional[list[str]] = None
    language: Optional[str] = None
    geography: Optional[str] = None
    priority: Optional[int] = None
    notes: Optional[str] = None
    parser_type: Optional[str] = None
    poll_frequency_minutes: Optional[int] = None
    section_scope: Optional[list[str]] = None

    @field_validator("type", mode="before")
    @classmethod
    def validate_type(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            return _validate_source_type(v)
        return v


class SourceOut(BaseModel):
    id: uuid.UUID
    name: str
    type: str
    url: Optional[str]
    enabled: bool
    tags: Optional[list[str]]
    language: Optional[str]
    geography: Optional[str]
    priority: int
    notes: Optional[str]
    parser_type: Optional[str]
    poll_frequency_minutes: Optional[int]
    last_polled_at: Optional[datetime]
    last_success_at: Optional[datetime]
    last_error: Optional[str]
    section_scope: Optional[list[str]]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
