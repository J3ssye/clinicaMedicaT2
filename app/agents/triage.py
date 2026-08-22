from __future__ import annotations

from app.agents.base import AgentContext
from app.integrations.llm import LLMService
from app.schemas.chat import OrchestratorResponse


class TriageAgent:
    def __init__(self, llm: LLMService | None = None) -> None:
        self.llm = llm or LLMService()

    async def handle(self, context: AgentContext) -> OrchestratorResponse:
        text = context.incoming_text.lower()
        urgent = any(
            token in text
            for token in ["falta de ar", "desmaio", "dor no peito", "convuls", "sangramento intenso"]
        )
        moderate = any(
            token in text
            for token in ["febre", "vomito", "tontura", "enjoo", "dor", "pressao alta", "mal estar"]
        )

        if urgent:
            return OrchestratorResponse(
                intent="triage",
                reply_text=(
                    "Os sinais relatados podem exigir atendimento imediato. "
                    "Procure pronto atendimento ou emergência agora e, se desejar, a equipe pode apoiar depois no retorno."
                ),
                escalate_to_human=True,
                llm_used=False,
            )

        if moderate:
            return OrchestratorResponse(
                intent="triage",
                reply_text=(
                    "Não consigo avaliar clinicamente por aqui, mas o ideal é uma avaliação médica. "
                    "Se quiser, posso ajudar a seguir para o agendamento."
                ),
                llm_used=False,
            )

        prompt = (
            "Você faz triagem conversacional conservadora para clínica médica. Nunca diagnostique. "
            "Responda em português do Brasil, em no máximo 2 frases, sem prescrever conduta complexa."
        )
        reply = self.llm.draft_reply(prompt, context.incoming_text, history=context.history)
        return OrchestratorResponse(
            intent="triage",
            reply_text=reply or "A equipe pode ajudar a direcionar seu atendimento e seguir com o agendamento.",
            llm_used=bool(reply),
        )
