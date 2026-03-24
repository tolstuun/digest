"""
Extraction schemas: input to LLM and structured output from LLM.
"""
from dataclasses import dataclass
from typing import Literal, Optional

from pydantic import BaseModel, Field

EventType = Literal[
    "funding",
    "mna",
    "earnings",
    "executive_change",
    "partnership",
    "product_launch",
    "breach",
    "conference",
    "regulation",
    "other",
    "unknown",
]


@dataclass
class StoryInput:
    """Minimal representation of a story passed to the LLM."""

    story_id: str
    title: Optional[str]
    text: Optional[str]
    url: Optional[str]


class ExtractionResult(BaseModel):
    """Structured facts extracted by the LLM."""

    source_language: str
    event_type: EventType
    company_names: list[str]
    person_names: list[str]
    product_names: list[str]
    geography_names: list[str]
    amount_text: Optional[str] = None
    currency: Optional[str] = None
    canonical_summary_en: str
    canonical_summary_ru: str
    extraction_confidence: float = Field(ge=0.0, le=1.0)
