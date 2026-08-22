from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.feedback import Feedback


class FeedbackRepository:
    async def get_latest_for_patient(self, session: AsyncSession, patient_id: int) -> Feedback | None:
        stmt = (
            select(Feedback)
            .where(Feedback.patient_id == patient_id)
            .order_by(Feedback.created_at.desc(), Feedback.id.desc())
            .limit(1)
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def create(
        self,
        *,
        session: AsyncSession,
        patient_id: int,
        raw_message: str,
        summary: str,
        rating: int | None = None,
        appointment_id: int | None = None,
    ) -> Feedback:
        feedback = Feedback(
            patient_id=patient_id,
            appointment_id=appointment_id,
            rating=rating,
            raw_message=raw_message,
            summary=summary,
        )
        session.add(feedback)
        await session.flush()
        return feedback
