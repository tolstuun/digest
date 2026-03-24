import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class DigestPageOut(BaseModel):
    """Metadata response for digest page list; does not include html_content."""

    id: uuid.UUID
    digest_run_id: uuid.UUID
    slug: str
    title: str
    rendered_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
