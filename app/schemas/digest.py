import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel


class DigestEntryOut(BaseModel):
    id: uuid.UUID
    digest_run_id: uuid.UUID
    event_cluster_id: Optional[uuid.UUID] = None
    rank: int
    final_score: Optional[float] = None
    title: Optional[str] = None
    canonical_summary_en: Optional[str] = None
    canonical_summary_ru: Optional[str] = None
    why_it_matters_en: Optional[str] = None
    why_it_matters_ru: Optional[str] = None
    source_url: Optional[str] = None
    source_name: Optional[str] = None
    final_summary: Optional[str] = None
    final_why_it_matters: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DigestRunOut(BaseModel):
    id: uuid.UUID
    digest_date: date
    section_name: str
    status: str
    total_candidate_clusters: int
    total_included_clusters: int
    generated_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DigestRunDetail(DigestRunOut):
    entries: list[DigestEntryOut]
