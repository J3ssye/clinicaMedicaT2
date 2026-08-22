from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Feedback(Base):
    __tablename__ = "feedbacks"
    __table_args__ = (Index("ix_feedbacks_patient_created", "patient_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("pacientes.id"), index=True)
    appointment_id: Mapped[int | None] = mapped_column(ForeignKey("consultas.id"), nullable=True, index=True)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw_message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    patient = relationship("Patient", back_populates="feedbacks")
