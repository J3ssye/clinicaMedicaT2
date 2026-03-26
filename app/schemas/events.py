from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class WahaWebhookPayload(BaseModel):
    event: str
    session: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class IncomingMessage(BaseModel):
    message_id: str | None = None
    sender_phone: str
    sender_name: str | None = None
    text: str
    sent_at: datetime | None = None
