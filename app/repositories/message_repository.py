from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message import Message
from app.schemas.chat import ChatMessage


def _derive_sender_type(direction: str, intent: str | None) -> str:
    """Classifica o remetente com base na direção e intent da mensagem."""
    if direction == "inbound":
        return "patient"
    if intent == "human_takeover":
        return "human_agent"
    return "bot"


class MessageRepository:
    async def log_message(
        self,
        session: AsyncSession,
        *,
        patient_id: int | None,
        direction: str,
        content: str,
        external_id: str | None = None,
        intent: str | None = None,
        channel: str = "whatsapp",
        processing_status: str | None = None,
        commit: bool = True,
    ) -> Message:
        now = datetime.utcnow()
        message = Message(
            patient_id=patient_id,
            direction=direction,
            content=content,
            external_id=external_id,
            intent=intent,
            channel=channel,
            sender_type=_derive_sender_type(direction, intent),
            conversation_date=now.date(),
            created_at=now,
            processing_status=processing_status,
        )
        session.add(message)
        if commit:
            await session.commit()
            await session.refresh(message)
        else:
            await session.flush()
        return message

    async def inbound_exists(self, session: AsyncSession, external_id: str | None) -> bool:
        if not external_id:
            return False
        stmt = select(Message.id).where(
            Message.direction == "inbound",
            Message.external_id == external_id,
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def inbound_recent_duplicate(
        self,
        session: AsyncSession,
        *,
        patient_id: int | None,
        content: str,
        window_seconds: int = 180,
    ) -> bool:
        if patient_id is None:
            return False
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


    async def outbound_recent_duplicate(
        self,
        session: AsyncSession,
        *,
        patient_id: int | None,
        content: str,
        window_seconds: int = 30,
    ) -> bool:
        if patient_id is None:
            return False
        cutoff = datetime.utcnow() - timedelta(seconds=window_seconds)
        stmt = (
            select(Message.id)
            .where(
                Message.direction == "outbound",
                Message.patient_id == patient_id,
                Message.content == content,
                Message.created_at >= cutoff,
            )
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def get_conversation_history(
        self,
        session: AsyncSession,
        *,
        patient_id: int | None,
        limit: int = 100,
    ) -> list[Message]:
        """
        Retorna mensagens do paciente desde o início do dia atual (UTC).
        Persiste até `limit` mensagens em ordem cronológica crescente.

        A recuperação por dia inteiro garante que o contexto da conversa
        não seja perdido entre mensagens espaçadas ao longo do mesmo dia.
        """
        if patient_id is None:
            return []
        today_midnight = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        stmt = (
            select(Message)
            .where(
                Message.patient_id == patient_id,
                Message.created_at >= today_midnight,
            )
            .order_by(Message.created_at.asc(), Message.id.asc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

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
