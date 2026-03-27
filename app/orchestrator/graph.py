from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient
from app.schemas.chat import OrchestratorResponse
from app.services.conversation_service import ConversationService


class ChatOrchestrator:
    def __init__(self) -> None:
        self.conversation_service = ConversationService()

    async def run(
        self,
        *,
        session: AsyncSession,
        patient: Patient,
        message: str,
        external_id: str | None = None,
    ) -> OrchestratorResponse:
        result = await self.conversation_service.process_user_message(
            session=session,
            patient=patient,
            message=message,
            external_id=external_id,
        )
        return OrchestratorResponse(reply_text=result.reply_text, messages=result.messages)
