from app.agents.base import AgentContext
from app.schemas.chat import OrchestratorResponse
from app.services.llm import GeminiService


class TriageAgent:
    def __init__(self) -> None:
        self.llm = GeminiService()

    async def handle(self, context: AgentContext) -> OrchestratorResponse:
        prompt = (
            "Voce faz triagem conservadora para clinica medica. Nunca diagnostique. "
            "Identifique sinais de alerta e recomende pronto atendimento ou SAMU em urgencia. "
            "Caso nao seja urgente, oriente procurar consulta medica."
        )
        reply = self.llm.draft_reply(prompt, context.incoming_text)
        text = context.incoming_text.lower()
        urgent = any(
            token in text
            for token in ["falta de ar", "desmaio", "dor no peito", "convuls", "sangramento intenso"]
        )
        if not reply:
            if urgent:
                reply = (
                    "Seus sintomas podem indicar urgencia. Procure atendimento imediato ou ligue 192 "
                    "se houver risco iminente."
                )
            else:
                reply = (
                    "Nao consigo diagnosticar por aqui, mas posso registrar sua queixa e orientar o "
                    "agendamento com um medico. Se os sintomas piorarem, procure atendimento imediato."
                )
        return OrchestratorResponse(intent="triage", reply_text=reply, escalate_to_human=urgent)
