from datetime import datetime
import re

from sqlalchemy import select

from app.agents.base import AgentContext
from app.models.appointment import Appointment
from app.schemas.chat import OrchestratorResponse
from app.services.calendar import GoogleCalendarService


class SchedulingAgent:
    def __init__(self) -> None:
        self.calendar = GoogleCalendarService()

    async def handle(self, context: AgentContext) -> OrchestratorResponse:
        parsed = self._extract_datetime(context.incoming_text)
        if parsed is None:
            return OrchestratorResponse(
                intent="scheduling",
                reply_text=(
                    "Posso agendar sua consulta. Me envie data e horario no formato "
                    "DD/MM/AAAA HH:MM e, se puder, o nome do medico ou especialidade."
                ),
            )

        doctor_name = self._extract_doctor_name(context.incoming_text)
        stmt = select(Appointment).where(Appointment.scheduled_at == parsed)
        existing = (await context.session.execute(stmt)).scalar_one_or_none()
        if existing:
            return OrchestratorResponse(
                intent="scheduling",
                reply_text="Esse horario ja esta ocupado. Me envie outra data ou outro horario.",
            )

        appointment = Appointment(
            patient_id=context.patient.id,
            scheduled_at=parsed,
            doctor_name=doctor_name,
            specialty=None,
            notes=context.incoming_text,
        )
        appointment.google_event_id = self.calendar.create_event(
            patient_name=context.patient.name or "Paciente",
            doctor_name=doctor_name,
            scheduled_at=parsed,
            specialty=None,
            notes=context.incoming_text,
        )
        context.session.add(appointment)
        await context.session.commit()
        await context.session.refresh(appointment)

        return OrchestratorResponse(
            intent="scheduling",
            reply_text=(
                f"Consulta agendada para {parsed.strftime('%d/%m/%Y %H:%M')} com {doctor_name}. "
                "Vou enviar um lembrete automaticamente no dia anterior."
            ),
        )

    @staticmethod
    def _extract_datetime(text: str) -> datetime | None:
        formats = ("%d/%m/%Y %H:%M", "%d-%m-%Y %H:%M")
        match = re.search(r"(\d{2}[/-]\d{2}[/-]\d{4}\s+\d{2}:\d{2})", text)
        if not match:
            return None
        candidate = match.group(1)
        for fmt in formats:
            try:
                return datetime.strptime(candidate, fmt)
            except ValueError:
                continue
        return None

    @staticmethod
    def _extract_doctor_name(text: str) -> str:
        lowered = text.lower()
        if "dr." in lowered:
            idx = lowered.index("dr.")
            return text[idx:].split("\n")[0].strip()
        if "dra." in lowered:
            idx = lowered.index("dra.")
            return text[idx:].split("\n")[0].strip()
        return "clinico geral"
