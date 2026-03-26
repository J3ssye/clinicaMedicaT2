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
