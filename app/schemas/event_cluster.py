import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class EventClusterOut(BaseModel):
    id: uuid.UUID
    cluster_key: str
    event_type: Optional[str] = None
    representative_story_id: Optional[uuid.UUID] = None
    story_count: int
    story_ids: list[uuid.UUID]
    created_at: datetime
    updated_at: datetime
