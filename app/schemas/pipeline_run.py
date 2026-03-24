import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class PipelineRunStepOut(BaseModel):
    id: uuid.UUID
    pipeline_run_id: uuid.UUID
    step_name: str
    status: str
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    error_message: Optional[str]
    details_json: Optional[Dict[str, Any]]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PipelineRunOut(BaseModel):
    id: uuid.UUID
    run_date: date
    trigger_type: str
    status: str
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    error_message: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PipelineRunDetail(PipelineRunOut):
    steps: List[PipelineRunStepOut] = []
