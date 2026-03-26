from pathlib import Path

from app.agents.base import AgentContext
from app.core.config import get_settings
from app.schemas.chat import OrchestratorResponse
from app.services.llm import GeminiService


settings = get_settings()


class FAQAgent:
    def __init__(self) -> None:
        self.llm = GeminiService()

    def _knowledge_base(self) -> str:
        path = Path(settings.faq_kb_path)
        return path.read_text(encoding="utf-8") if path.exists() else ""

    async def handle(self, context: AgentContext) -> OrchestratorResponse:
        kb = self._knowledge_base()
        prompt = (
            "Voce e o agente de FAQ da clinica. Responda de forma objetiva, simpatica e segura. "
            "Se a resposta nao estiver na base, diga que vai encaminhar para a equipe."
            f"\n\nBase FAQ:\n{kb}"
        )
        reply = self.llm.draft_reply(prompt, context.incoming_text)
        if not reply:
            reply = (
                "Posso ajudar com horario de atendimento, localizacao, convenio e preparo de exames. "
                "Se sua duvida for especifica, encaminho para nossa equipe."
            )
        return OrchestratorResponse(intent="faq", reply_text=reply)
