import asyncio
from datetime import datetime, timedelta

from celery import shared_task
from sqlalchemy import and_, select

from app.agents.reminder import ReminderAgent
from app.db.session import SessionLocal
from app.models.appointment import Appointment
from app.models.patient import Patient
from app.models.reminder_log import ReminderLog
from app.services.waha_client import WahaClient


@shared_task(name="app.tasks.reminders.send_day_before_reminders")
def send_day_before_reminders() -> int:
    return asyncio.run(_send_day_before_reminders())


async def _send_day_before_reminders() -> int:
    start = datetime.utcnow() + timedelta(hours=23)
    end = datetime.utcnow() + timedelta(hours=25)
    sent_count = 0
    client = WahaClient()

    async with SessionLocal() as session:
        stmt = (
            select(Appointment, Patient)
            .join(Patient, Patient.id == Appointment.patient_id)
            .where(and_(Appointment.scheduled_at >= start, Appointment.scheduled_at <= end))
        )
        rows = (await session.execute(stmt)).all()
        for appointment, patient in rows:
            existing = await session.execute(
                select(ReminderLog).where(
                    ReminderLog.appointment_id == appointment.id, ReminderLog.kind == "D-1"
                )
            )
            if existing.scalar_one_or_none():
                continue

            text = ReminderAgent.compose(appointment, patient)
            await client.send_text(chat_id=patient.phone, text=text)
            session.add(ReminderLog(appointment_id=appointment.id, kind="D-1"))
            sent_count += 1

        await session.commit()

    return sent_count
