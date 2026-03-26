from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ReminderLog(Base):
    __tablename__ = "lembretes_enviados"
    __table_args__ = (UniqueConstraint("appointment_id", "kind", name="uq_reminder_kind"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    appointment_id: Mapped[int] = mapped_column(ForeignKey("consultas.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="D-1")
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
