from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient
from app.schemas.chat import ChatMessage


@dataclass(slots=True)
class AgentContext:
    session: AsyncSession
    patient: Patient
    incoming_text: str
    history: list[ChatMessage] = field(default_factory=list)
    conversation_metadata: dict[str, str] = field(default_factory=dict)
