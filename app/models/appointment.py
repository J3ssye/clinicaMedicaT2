from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Appointment(Base):
    __tablename__ = "consultas"
    __table_args__ = (
        Index("ix_consultas_patient_scheduled_at", "patient_id", "scheduled_at"),
        Index("ix_consultas_doctor_scheduled_at", "doctor_name", "scheduled_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("pacientes.id"), index=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    doctor_name: Mapped[str] = mapped_column(String(255))
    specialty: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="scheduled", index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    google_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    confirmation_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancellation_followup_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    post_consult_followup_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reengagement_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    scheduling_failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Campos de terceiro: quando a consulta é agendada para um familiar/dependente
    # (não o próprio dono do número de WhatsApp).
    # Mantemos os dados do beneficiário aqui para NÃO sobrescrever o perfil do titular.
    is_third_party: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    beneficiary_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    beneficiary_cpf: Mapped[str | None] = mapped_column(String(11), nullable=True, index=True)
    beneficiary_birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    patient = relationship("Patient", back_populates="appointments")
