import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class StoryFactsOut(BaseModel):
    story_id: uuid.UUID
    model_name: str
    extraction_confidence: Optional[float] = None
    extracted_at: Optional[datetime] = None
    source_language: Optional[str] = None
    event_type: Optional[str] = None
    company_names: Optional[list[str]] = None
    person_names: Optional[list[str]] = None
    product_names: Optional[list[str]] = None
    geography_names: Optional[list[str]] = None
    amount_text: Optional[str] = None
    currency: Optional[str] = None
    canonical_summary_en: Optional[str] = None
    canonical_summary_ru: Optional[str] = None

    model_config = {"from_attributes": True}
