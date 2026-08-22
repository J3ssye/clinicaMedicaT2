from __future__ import annotations

import unicodedata

from app.agents.base import AgentContext
from app.core.config import get_settings
from app.integrations.llm import LLMService
from app.schemas.chat import OrchestratorResponse
from app.services.memory.redis_memory import RedisConversationMemory


settings = get_settings()

_SCHEDULING_TOKENS = (
    "agendar", "consulta", "marcar", "horario", "horário",
    "vaga", "medico", "médico", "doutor", "doutora",
)

_SYSTEM_PROMPT = (
    "{base}\n\n"
    "CONTEXTO: O paciente está respondendo a um acompanhamento de saúde após consulta realizada "
    "há aproximadamente {days_ago} dias com {doctor}.\n\n"
    "REGRAS ESPECÍFICAS:\n"
    "- Responda de forma empática e acolhedora, máximo 3 frases curtas.\n"
    "- Nunca emita diagnóstico ou prescrição.\n"
    "- Se o paciente relatar sintomas novos, piora ou dúvida clínica, oriente a agendar avaliação.\n"
    "- Se estiver bem, agradeça o retorno e encerre gentilmente.\n"
    "- Não mencione sistemas externos, Google Calendar ou sincronização."
)


def _norm(text: str) -> str:
    nkfd = unicodedata.normalize("NFKD", text.lower().strip())
    return "".join(ch for ch in nkfd if not unicodedata.combining(ch))


class HealthFollowupAgent:
    """Processa a resposta do paciente ao acompanhamento clínico de 20-30 dias."""

    def __init__(
        self,
        memory: RedisConversationMemory | None = None,
        llm: LLMService | None = None,
    ) -> None:
        self.memory = memory or RedisConversationMemory()
        self.llm = llm or LLMService()

    async def handle(self, context: AgentContext) -> OrchestratorResponse:
        session_key = context.conversation_metadata.get("session_key")
        state = self.memory.get_state(session_key)
        normalized = _norm(context.incoming_text)

        # Paciente quer agendar — transfere para o fluxo de agendamento
        if any(t in normalized for t in _SCHEDULING_TOKENS):
            self.memory.clear_state_keys(session_key, "active_flow", "doctor_name", "days_ago")
            self.memory.merge_state(session_key, {"active_flow": "scheduling"})
            return OrchestratorResponse(
                intent="scheduling",
                reply_text=(
                    "Claro! Posso verificar os horários disponíveis. "
                    "Com qual médico prefere — Dr. Lucas ou Dra. Vitória?"
                ),
                llm_used=False,
            )

        # Usa LLM com contexto clínico para responder livremente
        doctor = state.get("doctor_name", "o médico")
        days_ago = state.get("days_ago", "alguns")
        system_prompt = _SYSTEM_PROMPT.format(
            base=settings.clinic_assistant_system_prompt,
            days_ago=days_ago,
            doctor=doctor,
        )
        reply = self.llm.draft_reply(system_prompt, context.incoming_text, history=context.history)

        # Fluxo encerrado após a resposta: acompanhamento é interação única
        self.memory.clear_state_keys(session_key, "active_flow", "doctor_name", "days_ago")

        return OrchestratorResponse(
            intent="health_followup",
            reply_text=reply or "Obrigada pelo retorno! Qualquer dúvida, é só nos chamar 😊",
            llm_used=bool(reply),
        )
