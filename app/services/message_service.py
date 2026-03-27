from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message import Message
from app.schemas.chat import ChatMessage


class MessageService:
    @staticmethod
    async def log_message(
        session: AsyncSession,
        *,
        patient_id: int | None,
        direction: str,
        content: str,
        external_id: str | None = None,
        intent: str | None = None,
        commit: bool = True,
    ) -> Message:
        message = Message(
            patient_id=patient_id,
            direction=direction,
            content=content,
            external_id=external_id,
            intent=intent,
        )
        session.add(message)
        if commit:
            await session.commit()
            await session.refresh(message)
        else:
            await session.flush()
        return message

    @staticmethod
    async def inbound_exists(session: AsyncSession, external_id: str | None) -> bool:
        if not external_id:
            return False
        stmt = select(Message.id).where(
            Message.direction == "inbound", Message.external_id == external_id
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none() is not None

    @staticmethod
    async def inbound_recent_duplicate(
        session: AsyncSession,
        *,
        patient_id: int | None,
        content: str,
        window_seconds: int = 180,
    ) -> bool:
        """Prevent repeated replies when WAHA reenvia histórico ou retries.
        Considera mesmo paciente e mesmo texto dentro da janela."""
        if patient_id is None:
            return False
        from datetime import datetime, timedelta

        cutoff = datetime.utcnow() - timedelta(seconds=window_seconds)
        stmt = (
            select(Message.id)
            .where(
                Message.direction == "inbound",
                Message.patient_id == patient_id,
                Message.content == content,
                Message.created_at >= cutoff,
            )
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none() is not None

    @staticmethod
    async def get_conversation_history(
        session: AsyncSession,
        *,
        patient_id: int | None,
        limit: int = 20,
    ) -> list[Message]:
        if patient_id is None:
            return []
        stmt = (
            select(Message)
            .where(Message.patient_id == patient_id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        history = list(result.scalars().all())
        history.reverse()
        return history

    @staticmethod
    def to_chat_messages(messages: list[Message]) -> list[ChatMessage]:
        role_by_direction = {"inbound": "user", "outbound": "assistant"}
        conversation: list[ChatMessage] = []
        for message in messages:
            role = role_by_direction.get(message.direction)
            if role is None or not message.content:
                continue
            conversation.append(ChatMessage(role=role, content=message.content))
        return conversation

    @classmethod
    async def build_llm_messages(
        cls,
        session: AsyncSession,
        *,
        patient_id: int | None,
        system_prompt: str,
        limit: int = 20,
    ) -> list[ChatMessage]:
        history = await cls.get_conversation_history(session, patient_id=patient_id, limit=limit)
        return [ChatMessage(role="system", content=system_prompt), *cls.to_chat_messages(history)]
