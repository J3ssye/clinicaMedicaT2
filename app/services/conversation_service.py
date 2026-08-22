from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient
from app.orchestrator.graph import ChatOrchestrator
from app.schemas.chat import ChatMessage


@dataclass
class ConversationResult:
    reply_text: str
    messages: list[ChatMessage]


class ConversationService:
    def __init__(self) -> None:
        self.orchestrator = ChatOrchestrator()

    async def process_user_message(
        self,
        *,
        session: AsyncSession,
        patient: Patient,
        message: str,
        external_id: str | None = None,
    ) -> ConversationResult:
        result = await self.orchestrator.run(
            session=session,
            patient=patient,
            message=message,
            external_id=external_id,
        )
        return ConversationResult(reply_text=result.reply_text, messages=result.messages)
