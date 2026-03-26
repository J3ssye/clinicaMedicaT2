from app.models.appointment import Appointment
from app.models.patient import Patient


class ReminderAgent:
    @staticmethod
    def compose(appointment: Appointment, patient: Patient) -> str:
        schedule = appointment.scheduled_at.strftime("%d/%m/%Y %H:%M")
        return (
            f"Ola, {patient.name or 'paciente'}! Este e um lembrete da sua consulta em {schedule} "
            f"com {appointment.doctor_name}. Se precisar remarcar, responda esta mensagem."
        )
