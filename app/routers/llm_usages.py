"""
Read-only endpoint for LLM usage records.
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.llm_usage import LlmUsage
from app.schemas.llm_usage import LlmUsageOut

router = APIRouter(prefix="/llm-usages", tags=["llm-usages"])


@router.get("/", response_model=list[LlmUsageOut])
def list_llm_usages(
    stage_name: Optional[str] = Query(None, description="Filter by stage name"),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[LlmUsageOut]:
    q = db.query(LlmUsage).order_by(LlmUsage.created_at.desc())
    if stage_name:
        q = q.filter(LlmUsage.stage_name == stage_name)
    return q.limit(limit).all()
