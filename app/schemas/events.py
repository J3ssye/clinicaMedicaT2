from __future__ import annotations

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
    sender_chat_id: str
    sender_name: str | None = None
    text: str
    sent_at: datetime | None = None
    from_me: bool = False
    is_group: bool = False
    is_status: bool = False
    raw_event: str | None = None
    media_type: str | None = None
    media_url: str | None = None
    media_mime_type: str | None = None
    media_caption: str | None = None


class WebhookProcessResult(BaseModel):
    status: str
    deduplicated: bool = False
    intent: str | None = None
    llm_used: bool = False
