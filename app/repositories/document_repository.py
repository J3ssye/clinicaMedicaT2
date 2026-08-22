from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document


class DocumentRepository:
    async def get_latest_for_patient(self, session: AsyncSession, patient_id: int) -> Document | None:
        stmt = (
            select(Document)
            .where(Document.patient_id == patient_id)
            .order_by(Document.created_at.desc(), Document.id.desc())
            .limit(1)
        )
        return (await session.execute(stmt)).scalar_one_or_none()
