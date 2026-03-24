import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class EventClusterAssessmentOut(BaseModel):
    event_cluster_id: uuid.UUID
    primary_section: Optional[str] = None
    include_in_digest: Optional[bool] = None
    rule_score: Optional[float] = None
    llm_score: Optional[float] = None
    final_score: Optional[float] = None
    why_it_matters_en: Optional[str] = None
    why_it_matters_ru: Optional[str] = None
    editorial_notes: Optional[str] = None
    model_name: Optional[str] = None
    assessed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
