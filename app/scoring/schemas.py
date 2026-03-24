"""
Scoring schemas: input to the LLM and structured editorial output.
"""
from dataclasses import dataclass, field
from typing import Literal, Optional

from pydantic import BaseModel, Field

SectionType = Literal[
    "companies_business",
    "incidents",
    "conferences",
    "regulation",
    "other",
]


@dataclass
class ClusterInput:
    """Minimal cluster context passed to the LLM for editorial scoring."""

    cluster_id: str
    event_type: Optional[str]
    story_count: int
    company_names: list[str]
    amount_text: Optional[str]
    currency: Optional[str]
    canonical_summary_en: Optional[str]
    canonical_summary_ru: Optional[str]
    representative_title: Optional[str]


class ClusterAssessment(BaseModel):
    """Structured editorial assessment returned by the LLM."""

    primary_section: SectionType
    llm_score: float = Field(ge=0.0, le=1.0)
    include_in_digest: bool
    why_it_matters_en: str
    why_it_matters_ru: str
    editorial_notes: str
