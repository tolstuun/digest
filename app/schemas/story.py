import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class StoryOut(BaseModel):
    id: uuid.UUID
    raw_item_id: uuid.UUID
    source_id: uuid.UUID
    title: Optional[str]
    url: Optional[str]
    canonical_url: Optional[str]
    published_at: Optional[datetime]
    normalized_at: datetime
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
