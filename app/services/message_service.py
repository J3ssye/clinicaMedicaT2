from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message import Message


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
    ) -> Message:
        message = Message(
            patient_id=patient_id,
            direction=direction,
            content=content,
            external_id=external_id,
            intent=intent,
        )
        session.add(message)
        await session.commit()
        await session.refresh(message)
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
