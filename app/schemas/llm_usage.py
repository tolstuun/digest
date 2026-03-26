import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


class LlmUsageOut(BaseModel):
    id: uuid.UUID
    stage_name: str
    model_name: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: Optional[Decimal] = None
    related_object_id: Optional[uuid.UUID] = None
    created_at: datetime

    model_config = {"from_attributes": True}
