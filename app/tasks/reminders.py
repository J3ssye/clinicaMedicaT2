from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.agents.reminder import ReminderAgent
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.integrations.whatsapp import WhatsAppClient
from app.repositories.appointment_repository import AppointmentRepository
from app.services.memory.redis_memory import RedisConversationMemory
from app.tasks.celery_app import celery_app


settings = get_settings()


def _to_person_chat_id(phone: str) -> str:
    raw = (phone or "").strip()
    if "@" in raw:
        return raw
    digits = "".join(ch for ch in raw if ch.isdigit())
    return f"{digits}@c.us" if digits else raw


def _resolve_chat_id(patient_id: int, phone: str, memory: RedisConversationMemory) -> str:
    """Retorna o chat_id real usado pelo WAHA para este paciente.

    WAHA pode entregar webhooks com @lid (Link ID interno) em vez do número
    @c.us. O mapeamento é gravado pelo webhook handler a cada mensagem recebida.
    Se ainda não houver mapeamento (paciente nunca mandou mensagem), usa o
    número no formato padrão @c.us como fallback.
    """
    known = memory.get_patient_chat_id(patient_id)
    return known if known else _to_person_chat_id(phone)


@celery_app.task(name="app.tasks.reminders.send_day_before_reminders")
def send_day_before_reminders() -> int:
    return asyncio.run(_send_day_before_reminders())


@celery_app.task(name="app.tasks.reminders.send_post_consult_followups")
def send_post_consult_followups() -> int:
    return asyncio.run(_send_post_consult_followups())


@celery_app.task(name="app.tasks.reminders.send_reengagement_followups")
def send_reengagement_followups() -> int:
    return asyncio.run(_send_reengagement_followups())


@celery_app.task(name="app.tasks.reminders.send_cancellation_followups")
def send_cancellation_followups() -> int:
    return asyncio.run(_send_cancellation_followups())


async def _send_day_before_reminders() -> int:
    """Envia lembrete D-1 e abre o fluxo de confirmação via Redis."""
    timezone = ZoneInfo(settings.clinic_timezone)
    now_local = datetime.now(tz=timezone).replace(tzinfo=None)
    start = now_local + timedelta(hours=23)
    end = now_local + timedelta(hours=25)
    sent_count = 0
    client = WhatsAppClient()
    repository = AppointmentRepository()
    memory = RedisConversationMemory()

    async with SessionLocal() as session:
        rows = await repository.list_due_day_before_reminders(session, start=start, end=end)
        for appointment, patient in rows:
            chat_id = _resolve_chat_id(patient.id, patient.phone, memory)
            await client.send_text(chat_id=chat_id, text=ReminderAgent.compose(appointment, patient))
            await repository.mark_reminder_sent(session, appointment=appointment)

            # Abre fluxo conversacional para coletar confirmação do paciente
            memory.merge_state(chat_id, {
                "active_flow": "d1_confirmation",
                "appointment_id": str(appointment.id),
                "step": "awaiting_confirmation",
            })

            sent_count += 1
        await session.commit()
    return sent_count


async def _send_post_consult_followups() -> int:
    """Envia mensagem pós-consulta e abre fluxo de coleta de avaliação."""
    sent_count = 0
    client = WhatsAppClient()
    repository = AppointmentRepository()
    memory = RedisConversationMemory()
    cutoff = datetime.utcnow() - timedelta(hours=2)

    async with SessionLocal() as session:
        rows = await repository.list_due_post_consult_feedback(session, cutoff=cutoff)
        for appointment, patient in rows:
            chat_id = _resolve_chat_id(patient.id, patient.phone, memory)
            text = (
                f"Oi, {patient.name or 'tudo bem'}! Espero que esteja bem 💚\n"
                f"Como você avalia o atendimento com {appointment.doctor_name} hoje? "
                f"Responda com uma nota de *1 a 5* ⭐ (1 = muito ruim, 5 = excelente)."
            )
            await client.send_text(chat_id=chat_id, text=text)
            await repository.mark_post_consult_sent(session, appointment=appointment)

            # Abre fluxo conversacional para coletar a nota do paciente
            memory.merge_state(chat_id, {
                "active_flow": "post_consult_feedback",
                "appointment_id": str(appointment.id),
                "step": "awaiting_rating",
                "doctor_name": appointment.doctor_name,
            })

            sent_count += 1
        await session.commit()
    return sent_count


async def _send_reengagement_followups() -> int:
    """Envia acompanhamento clínico 20-30 dias após consulta."""
    sent_count = 0
    client = WhatsAppClient()
    repository = AppointmentRepository()
    memory = RedisConversationMemory()
    cutoff = datetime.utcnow() - timedelta(days=30)

    async with SessionLocal() as session:
        rows = await repository.list_due_reengagement(session, cutoff=cutoff)
        for appointment, patient in rows:
            chat_id = _resolve_chat_id(patient.id, patient.phone, memory)
            days_since = (datetime.utcnow() - appointment.attended_at).days if appointment.attended_at else 30
            text = (
                f"Olá, {patient.name or 'tudo bem'}! Faz cerca de {days_since} dias desde sua consulta "
                f"com {appointment.doctor_name} 💚\n"
                f"Como você está se sentindo? Melhorou dos sintomas que relatou?"
            )
            await client.send_text(chat_id=chat_id, text=text)
            await repository.mark_reengagement_sent(session, appointment=appointment)

            # Abre fluxo conversacional para processar a resposta do paciente
            memory.merge_state(chat_id, {
                "active_flow": "health_followup",
                "doctor_name": appointment.doctor_name,
                "days_ago": str(days_since),
            })

            sent_count += 1
        await session.commit()
    return sent_count


async def _send_cancellation_followups() -> int:
    sent_count = 0
    client = WhatsAppClient()
    repository = AppointmentRepository()
    cutoff = datetime.utcnow() - timedelta(days=3)

    async with SessionLocal() as session:
        rows = await repository.list_due_cancellation_followup(session, cutoff=cutoff)
        for appointment, patient in rows:
            text = (
                f"Olá, {patient.name or 'tudo bem'}! Vi que sua consulta foi cancelada. "
                f"Você ainda deseja agendar?"
            )
            await client.send_text(chat_id=_to_person_chat_id(patient.phone), text=text)
            await repository.mark_cancellation_followup_sent(session, appointment=appointment)
            sent_count += 1
        await session.commit()
    return sent_count
