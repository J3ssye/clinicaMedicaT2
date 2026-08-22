"""
Contatos internos da equipe FIDEM para notificações, escalonamentos e validações.

Estes contatos são usados pelo sistema para:
- Alertar sobre pacientes menores de 8 anos
- Escalar casos fora do fluxo padrão
- Notificar sobre receitas, dúvidas de agenda e análises manuais

Nunca expor estes contatos ao paciente, exceto quando o fluxo já prevê isso
intencionalmente.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TeamContact:
    name: str
    phone: str   # Somente dígitos, formato Brasil: 55 + DDD + número
    role: str    # doctor | secretary | developer

    @property
    def chat_id(self) -> str:
        """Formato de chat_id para o WhatsApp/WAHA."""
        return f"{self.phone}@c.us"

    def __str__(self) -> str:
        return f"{self.name} ({self.role})"


# -------------------------------------------------------------------
# Contatos configurados
# Formato: 55 (país) + DDD + número local (sem hífens ou espaços)
# -------------------------------------------------------------------
TEAM_CONTACTS: list[TeamContact] = [
    TeamContact(name="Dr. Lucas",    phone="556282382298",  role="doctor"),
    TeamContact(name="Dra. Vitória", phone="556281865059",  role="doctor"),
    TeamContact(name="Hyza",         phone="556284022170",  role="secretary"),
    TeamContact(name="Jessye",       phone="5562982122035", role="developer"),
]

DOCTORS:   list[TeamContact] = [c for c in TEAM_CONTACTS if c.role == "doctor"]
SECRETARY: TeamContact | None = next((c for c in TEAM_CONTACTS if c.role == "secretary"), None)
DEVELOPER: TeamContact | None = next((c for c in TEAM_CONTACTS if c.role == "developer"), None)

# Contatos que recebem alertas operacionais críticos (menores, escalonamentos)
ALERT_CONTACTS: list[TeamContact] = [
    *DOCTORS,
    *([SECRETARY] if SECRETARY else []),
]
