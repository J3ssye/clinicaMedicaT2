from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.patient import Patient


settings = get_settings()


class PatientRepository:
    @staticmethod
    def normalize_phone(phone: str) -> str:
        if not phone:
            return ""
        digits = "".join(ch for ch in phone if ch.isdigit())
        return digits or phone.strip().lower()

    @staticmethod
    def normalize_cpf(cpf: str | None) -> str | None:
        if not cpf:
            return None
        digits = "".join(ch for ch in cpf if ch.isdigit())
        return digits or None

    async def get_or_create_by_phone(self, session: AsyncSession, *, phone: str, name: str | None = None) -> Patient:
        normalized = self.normalize_phone(phone)
        result = await session.execute(select(Patient).where(Patient.phone == normalized))
        patient = result.scalar_one_or_none()
        if patient is not None:
            if name and not patient.name:
                patient.name = name
                await session.flush()
            return patient

        patient = Patient(phone=normalized, name=name)
        session.add(patient)
        await session.flush()
        return patient

    async def get_by_cpf(self, session: AsyncSession, *, cpf: str | None) -> Patient | None:
        normalized = self.normalize_cpf(cpf)
        if not normalized:
            return None
        result = await session.execute(select(Patient).where(Patient.cpf == normalized))
        return result.scalar_one_or_none()

    async def update_profile(
        self,
        session: AsyncSession,
        *,
        patient: Patient,
        name: str | None = None,
        cpf: str | None = None,
        birth_date=None,
    ) -> Patient:
        if name:
            patient.name = name.strip()
        if cpf:
            patient.cpf = self.normalize_cpf(cpf)
        if birth_date is not None:
            patient.birth_date = birth_date
        await session.flush()
        return patient

    async def pause_ai_for_human_takeover(self, session: AsyncSession, *, patient: Patient) -> Patient:
        now = datetime.now(UTC).replace(tzinfo=None)
        patient.ai_paused = True
        patient.paused_reason = "human_takeover"
        patient.ai_paused_at = now
        patient.ai_resume_at = now + timedelta(hours=settings.ai_human_pause_hours)
        patient.resumed_at = None
        await session.flush()
        return patient

    async def maybe_resume_ai(self, session: AsyncSession, *, patient: Patient) -> Patient:
        now = datetime.now(UTC).replace(tzinfo=None)
        if patient.ai_paused and patient.ai_resume_at and patient.ai_resume_at <= now:
            patient.ai_paused = False
            patient.paused_reason = None
            patient.resumed_at = now
            patient.ai_resume_at = None
            await session.flush()
        return patient
