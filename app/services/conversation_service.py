from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.patient import Patient
from app.schemas.chat import ChatMessage
from app.services.llm import GeminiService
from app.services.message_service import MessageService


settings = get_settings()


@dataclass
class ConversationResult:
    reply_text: str
    messages: list[ChatMessage]


class ConversationService:
    def __init__(self) -> None:
        self.llm = GeminiService()
        self.system_prompt = settings.clinic_assistant_system_prompt

    async def process_user_message(
        self,
        *,
        session: AsyncSession,
        patient: Patient,
        message: str,
        external_id: str | None = None,
    ) -> ConversationResult:
        try:
            await MessageService.log_message(
                session,
                patient_id=patient.id,
                direction="inbound",
                content=message,
                external_id=external_id,
                commit=False,
            )
            llm_messages = await MessageService.build_llm_messages(
                session,
                patient_id=patient.id,
                system_prompt=self.system_prompt,
            )
            reply_text = self.llm.chat(llm_messages).strip()
            if not reply_text:
                raise RuntimeError("assistant_reply_empty")

            await MessageService.log_message(
                session,
                patient_id=patient.id,
                direction="outbound",
                content=reply_text,
                commit=False,
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise

        updated_messages = await MessageService.build_llm_messages(
            session,
            patient_id=patient.id,
            system_prompt=self.system_prompt,
        )
        return ConversationResult(reply_text=reply_text, messages=updated_messages)
