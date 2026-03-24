import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class DigestPublicationOut(BaseModel):
    id: uuid.UUID
    digest_page_id: uuid.UUID
    channel_type: str
    target: str
    message_text: str
    provider_message_id: Optional[str]
    status: str
    published_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
