from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.orchestrator.graph import ChatOrchestrator
from app.repositories.message_repository import MessageRepository
from app.repositories.patient_repository import PatientRepository
from app.schemas.chat import OrchestratorResponse


class HandleChatMessageUseCase:
    def __init__(
        self,
        session: AsyncSession,
        patient_repository: PatientRepository | None = None,
        message_repository: MessageRepository | None = None,
        orchestrator: ChatOrchestrator | None = None,
    ) -> None:
        self.session = session
        self.patient_repository = patient_repository or PatientRepository()
        self.message_repository = message_repository or MessageRepository()
        self.orchestrator = orchestrator or ChatOrchestrator(self.message_repository)

    async def execute(
        self,
        *,
        session_id: str,
        patient_name: str | None,
        message: str,
    ) -> OrchestratorResponse:
        patient = await self.patient_repository.get_or_create_by_phone(
            self.session,
            phone=session_id,
            name=patient_name,
        )
        await self.message_repository.log_message(
            self.session,
            patient_id=patient.id,
            direction="inbound",
            content=message,
            commit=False,
        )
        result = await self.orchestrator.run(
            session=self.session,
            patient=patient,
            message=message,
        )
        await self.message_repository.log_message(
            self.session,
            patient_id=patient.id,
            direction="outbound",
            content=result.reply_text,
            intent=result.intent,
            commit=False,
        )
        await self.session.commit()
        return result
