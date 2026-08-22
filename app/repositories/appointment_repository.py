from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.appointment import Appointment
from app.models.patient import Patient


class AppointmentRepository:
    async def list_conflicts(
        self,
        session: AsyncSession,
        *,
        doctor_name: str,
        starts_at: datetime,
        ends_at: datetime,
        exclude_appointment_id: int | None = None,
        duration_minutes: int = 60,
    ) -> list[Appointment]:
        """
        Retorna agendamentos que conflitam com o slot [starts_at, ends_at).
        `duration_minutes` representa a duração da consulta sendo verificada e é
        usado para calcular o início do intervalo de busca de conflitos com precisão.
        Isso garante que a disponibilidade consultada seja real — nunca estimada.
        """
        stmt: Select[tuple[Appointment]] = select(Appointment).where(
            Appointment.doctor_name == doctor_name,
            Appointment.status == "scheduled",
            Appointment.scheduled_at < ends_at,
            Appointment.scheduled_at >= starts_at - timedelta(minutes=duration_minutes),
        )
        if exclude_appointment_id is not None:
            stmt = stmt.where(Appointment.id != exclude_appointment_id)
        rows = await session.execute(stmt.order_by(Appointment.scheduled_at.asc(), Appointment.id.asc()))
        return list(rows.scalars().all())

    async def get_active_by_patient(self, session: AsyncSession, *, patient_id: int, limit: int = 5) -> list[Appointment]:
        stmt = (
            select(Appointment)
            .where(Appointment.patient_id == patient_id, Appointment.status == "scheduled")
            .order_by(Appointment.scheduled_at.asc(), Appointment.id.asc())
            .limit(limit)
        )
        return list((await session.execute(stmt)).scalars().all())

    async def get_latest_attended_by_patient(self, session: AsyncSession, *, patient_id: int) -> Appointment | None:
        stmt = (
            select(Appointment)
            .where(Appointment.patient_id == patient_id, Appointment.attended_at.is_not(None))
            .order_by(Appointment.attended_at.desc(), Appointment.id.desc())
            .limit(1)
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def get_by_id(self, session: AsyncSession, appointment_id: int) -> Appointment | None:
        return await session.get(Appointment, appointment_id)

    async def get_next_by_patient_cpf(self, session: AsyncSession, *, cpf: str) -> Appointment | None:
        """Busca a próxima consulta pelo CPF do titular OU do beneficiário (terceiro)."""
        stmt = (
            select(Appointment)
            .join(Patient, Patient.id == Appointment.patient_id)
            .options(selectinload(Appointment.patient))
            .where(
                or_(Patient.cpf == cpf, Appointment.beneficiary_cpf == cpf),
                Appointment.status == "scheduled",
            )
            .order_by(Appointment.scheduled_at.asc(), Appointment.id.asc())
            .limit(1)
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def get_next_by_patient_id(self, session: AsyncSession, *, patient_id: int) -> Appointment | None:
        """Fallback para quando o paciente ainda não tem CPF cadastrado."""
        stmt = (
            select(Appointment)
            .where(Appointment.patient_id == patient_id, Appointment.status == "scheduled")
            .order_by(Appointment.scheduled_at.asc(), Appointment.id.asc())
            .limit(1)
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def list_due_day_before_reminders(self, session: AsyncSession, *, start: datetime, end: datetime) -> list[tuple[Appointment, Patient]]:
        stmt = (
            select(Appointment, Patient)
            .join(Patient, Patient.id == Appointment.patient_id)
            .where(
                Appointment.scheduled_at >= start,
                Appointment.scheduled_at <= end,
                Appointment.status == "scheduled",
                Appointment.reminder_sent_at.is_(None),
            )
        )
        return list((await session.execute(stmt)).all())

    async def list_due_reengagement(self, session: AsyncSession, *, cutoff: datetime) -> list[tuple[Appointment, Patient]]:
        stmt = (
            select(Appointment, Patient)
            .join(Patient, Patient.id == Appointment.patient_id)
            .where(
                Appointment.attended_at.is_not(None),
                Appointment.attended_at <= cutoff,
                Appointment.reengagement_sent_at.is_(None),
            )
            .order_by(Appointment.attended_at.asc())
        )
        return list((await session.execute(stmt)).all())

    async def list_due_cancellation_followup(self, session: AsyncSession, *, cutoff: datetime) -> list[tuple[Appointment, Patient]]:
        stmt = (
            select(Appointment, Patient)
            .join(Patient, Patient.id == Appointment.patient_id)
            .where(
                Appointment.status == "cancelled",
                Appointment.cancelled_at.is_not(None),
                Appointment.cancelled_at <= cutoff,
                Appointment.cancellation_followup_sent_at.is_(None),
            )
        )
        return list((await session.execute(stmt)).all())

    async def list_due_post_consult_feedback(self, session: AsyncSession, *, cutoff: datetime) -> list[tuple[Appointment, Patient]]:
        """Retorna consultas cujo horário marcado (scheduled_at) já passou do cutoff
        (2 h após o horário agendado) e que ainda não receberam o follow-up. Exclui canceladas."""
        stmt = (
            select(Appointment, Patient)
            .join(Patient, Patient.id == Appointment.patient_id)
            .where(
                Appointment.scheduled_at.is_not(None),
                Appointment.scheduled_at <= cutoff,
                Appointment.post_consult_followup_sent_at.is_(None),
                Appointment.status != "cancelled",
            )
        )
        return list((await session.execute(stmt)).all())

    async def create(
        self,
        *,
        session: AsyncSession,
        patient_id: int,
        scheduled_at: datetime,
        doctor_name: str | None,
        specialty: str | None,
        notes: str | None,
        is_third_party: bool = False,
        beneficiary_name: str | None = None,
        beneficiary_cpf: str | None = None,
        beneficiary_birth_date: date | None = None,
    ) -> Appointment:
        appointment = Appointment(
            patient_id=patient_id,
            scheduled_at=scheduled_at,
            doctor_name=doctor_name or "profissional da clínica",
            specialty=specialty,
            notes=notes,
            confirmation_status="confirmed",
            is_third_party=is_third_party,
            beneficiary_name=beneficiary_name,
            beneficiary_cpf=beneficiary_cpf,
            beneficiary_birth_date=beneficiary_birth_date,
        )
        session.add(appointment)
        await session.flush()
        return appointment

    async def mark_cancelled(self, session: AsyncSession, *, appointment: Appointment, reason: str | None = None) -> Appointment:
        appointment.status = "cancelled"
        appointment.cancelled_at = datetime.utcnow()
        appointment.cancellation_reason = reason
        await session.flush()
        return appointment

    async def mark_confirmed(self, session: AsyncSession, *, appointment: Appointment) -> Appointment:
        appointment.confirmation_status = "confirmed"
        await session.flush()
        return appointment

    async def mark_reminder_sent(self, session: AsyncSession, *, appointment: Appointment) -> Appointment:
        appointment.reminder_sent_at = datetime.utcnow()
        await session.flush()
        return appointment

    async def mark_post_consult_sent(self, session: AsyncSession, *, appointment: Appointment) -> Appointment:
        appointment.post_consult_followup_sent_at = datetime.utcnow()
        await session.flush()
        return appointment

    async def mark_reengagement_sent(self, session: AsyncSession, *, appointment: Appointment) -> Appointment:
        appointment.reengagement_sent_at = datetime.utcnow()
        await session.flush()
        return appointment

    async def mark_cancellation_followup_sent(self, session: AsyncSession, *, appointment: Appointment) -> Appointment:
        appointment.cancellation_followup_sent_at = datetime.utcnow()
        await session.flush()
        return appointment
