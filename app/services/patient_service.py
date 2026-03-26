from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient


class PatientService:
    @staticmethod
    async def get_or_create_by_phone(
        session: AsyncSession, phone: str, name: str | None = None
    ) -> Patient:
        normalized = "".join(ch for ch in phone if ch.isdigit() or ch == "+")
        result = await session.execute(select(Patient).where(Patient.phone == normalized))
        patient = result.scalar_one_or_none()
        if patient:
            if name and not patient.name:
                patient.name = name
                await session.commit()
                await session.refresh(patient)
            return patient

        patient = Patient(phone=normalized, name=name)
        session.add(patient)
        await session.commit()
        await session.refresh(patient)
        return patient
