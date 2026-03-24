import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, field_validator

VALID_SOURCE_TYPES = {"rss", "api", "html", "manual", "newsletter"}


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

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        if v not in VALID_SOURCE_TYPES:
            raise ValueError(
                f"type must be one of: {', '.join(sorted(VALID_SOURCE_TYPES))}"
            )
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
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
