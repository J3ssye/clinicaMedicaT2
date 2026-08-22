from __future__ import annotations

from app.core.config import get_settings
from app.models.appointment import Appointment
from app.models.patient import Patient


settings = get_settings()


class ReminderAgent:
    @staticmethod
    def compose(appointment: Appointment, patient: Patient) -> str:
        patient_name = patient.name or "paciente"
        hour_text = appointment.scheduled_at.strftime("%H:%M")
        doctor_name = appointment.doctor_name or "equipe médica"
        return (
            f"Olá, {patient_name}! Passando para lembrar que sua consulta está agendada para amanhã, às {hour_text}, "
            f"com {doctor_name}, na {settings.clinic_name}.\n\n"
            f"Você confirma sua presença? Responda *SIM* para confirmar ou *NÃO* caso não possa comparecer."
        )
