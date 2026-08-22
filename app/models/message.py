from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Message(Base):
    __tablename__ = "mensagens"
    __table_args__ = (
        Index("ix_mensagens_patient_direction_created", "patient_id", "direction", "created_at"),
        Index("ix_mensagens_external_channel", "external_id", "channel"),
        Index("ix_mensagens_patient_date", "patient_id", "conversation_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    patient_id: Mapped[int | None] = mapped_column(ForeignKey("pacientes.id"), nullable=True, index=True)
    direction: Mapped[str] = mapped_column(String(16), index=True)
    channel: Mapped[str] = mapped_column(String(32), default="whatsapp")
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    # Campos adicionados para ampliar contexto e rastreabilidade
    # sender_type: "patient" | "bot" | "human_agent"
    sender_type: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    # conversation_date: data local da mensagem (para queries por dia)
    conversation_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)

    # processing_status: resultado do processamento da mensagem no webhook.
    # Valores possíveis:
    #   "processed"          — mensagem processada normalmente pelo orquestrador
    #   "ignored_stale"      — mensagem muito antiga, não gerou resposta (backlog/replay)
    #   "ignored_duplicate"  — mensagem já processada anteriormente (idempotência)
    processing_status: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)

    patient = relationship("Patient", back_populates="messages")
