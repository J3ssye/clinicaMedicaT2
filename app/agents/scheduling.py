from datetime import datetime
import re

from sqlalchemy import select

from app.agents.base import AgentContext
from app.models.appointment import Appointment
from app.schemas.chat import OrchestratorResponse
from app.services.calendar import GoogleCalendarService
from app.services.llm import GeminiService


class SchedulingAgent:
    def __init__(self) -> None:
        self.calendar = GoogleCalendarService()
        self.llm = GeminiService()

    async def handle(self, context: AgentContext) -> OrchestratorResponse:
        parsed = self._extract_datetime(context.incoming_text)
        if parsed is None:
            fallback = (
                "Posso agendar sua consulta. Me envie data e horario no formato "
                "DD/MM/AAAA HH:MM e, se puder, o nome do medico ou especialidade."
            )
            reply = self._draft_scheduling_reply(
                patient_message=context.incoming_text,
                guidance=(
                    "Ainda nao ha data e horario validos para concluir o agendamento. "
                    "Peca a data e o horario no formato DD/MM/AAAA HH:MM e, se possivel, medico ou especialidade."
                ),
                fallback=fallback,
            )
            return OrchestratorResponse(intent="scheduling", reply_text=reply)

        doctor_name = self._extract_doctor_name(context.incoming_text)
        stmt = select(Appointment).where(Appointment.scheduled_at == parsed)
        existing = (await context.session.execute(stmt)).scalar_one_or_none()
        if existing:
            reply = self._draft_scheduling_reply(
                patient_message=context.incoming_text,
                guidance=(
                    f"O horario {parsed.strftime('%d/%m/%Y %H:%M')} ja esta ocupado. "
                    "Peca outra data ou horario."
                ),
                fallback="Esse horario ja esta ocupado. Me envie outra data ou outro horario.",
            )
            return OrchestratorResponse(intent="scheduling", reply_text=reply)

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

        fallback = (
            f"Consulta agendada para {parsed.strftime('%d/%m/%Y %H:%M')} com {doctor_name}. "
            "Vou enviar um lembrete automaticamente no dia anterior."
        )
        reply = self._draft_scheduling_reply(
            patient_message=context.incoming_text,
            guidance=(
                f"Consulta confirmada para {parsed.strftime('%d/%m/%Y %H:%M')} com {doctor_name}. "
                "Informe que o lembrete sera enviado automaticamente no dia anterior."
            ),
            fallback=fallback,
        )
        return OrchestratorResponse(intent="scheduling", reply_text=reply)

    def _draft_scheduling_reply(self, *, patient_message: str, guidance: str, fallback: str) -> str:
        prompt = (
            "Voce e a atendente virtual da clinica para agendamentos no WhatsApp. "
            "Responda em portugues do Brasil, em no maximo 2 frases, com tom cordial e objetivo. "
            f"\n\nContexto operacional:\n{guidance}"
        )
        reply = self.llm.draft_reply(prompt, patient_message)
        return reply or fallback

    @staticmethod
    def _extract_datetime(text: str) -> datetime | None:
        formats = ("%d/%m/%Y %H:%M", "%d-%m-%Y %H:%M")
        normalized = re.sub(r"\b[aà]s\b", " ", text, flags=re.IGNORECASE)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        match = re.search(r"(\d{2}[/-]\d{2}[/-]\d{4}\s+\d{2}:\d{2})", normalized)
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
        match = re.search(r"\b(dra?|dr)\s+[a-zA-ZÀ-ÿ][\wÀ-ÿ-]*", text, flags=re.IGNORECASE)
        if match:
            return match.group(0).strip()
        return "clinico geral"
