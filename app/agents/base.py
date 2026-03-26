from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient


@dataclass
class AgentContext:
    session: AsyncSession
    patient: Patient
    incoming_text: str
