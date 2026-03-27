from pathlib import Path
import time
import unicodedata

from app.agents.base import AgentContext
from app.core.config import get_settings
from app.schemas.chat import OrchestratorResponse
from app.services.llm import GeminiService


settings = get_settings()


class FAQAgent:
    def __init__(self) -> None:
        self.llm = GeminiService()
        self._cache: dict[str, tuple[str, float]] = {}
        self._ttl_seconds = 900  # 15 min

    def _knowledge_base(self) -> str:
        path = Path(settings.faq_kb_path)
        return path.read_text(encoding="utf-8") if path.exists() else ""

    @staticmethod
    def _normalize(text: str) -> str:
        normalized = unicodedata.normalize("NFKD", text.lower().strip())
        return "".join(char for char in normalized if not unicodedata.combining(char))

    @classmethod
    def _reply_from_rules(cls, text: str) -> str | None:
        normalized = cls._normalize(text)
        if any(token in normalized for token in ["oi", "ola", "bom dia", "boa tarde", "boa noite"]):
            return (
                "Ola! Posso te ajudar com agendamento, horarios, convenios, documentos e orientacoes iniciais. "
                "Me diga o que voce precisa."
            )
        if any(token in normalized for token in ["horario", "horarios", "atendimento"]):
            return "A clinica atende de segunda a sexta, das 08:00 as 18:00."
        if any(token in normalized for token in ["endereco", "localizacao", "localizacao"]):
            return "Estamos na Rua Exemplo, 123, Centro."
        if any(token in normalized for token in ["convenio", "convenios", "unimed", "plano", "aceita", "atendem"]):
            return "Consulte a equipe para confirmar cobertura e elegibilidade do seu plano."
        if any(token in normalized for token in ["exame", "exames", "preparo"]):
            return "Se houver preparo especifico, ele sera informado no momento do agendamento."
        if any(token in normalized for token in ["duvida", "duvidas", "ajuda", "informacao", "informacoes"]):
            return (
                "Posso te orientar sobre horarios, endereco, convenios, preparo de exames e agendamentos. "
                "Se preferir, escreva sua duvida em uma frase."
            )
        return None

    async def handle(self, context: AgentContext) -> OrchestratorResponse:
        normalized = context.incoming_text.strip().lower()
        now = time.time()
        if normalized in self._cache:
            reply_cached, ts = self._cache[normalized]
            if now - ts < self._ttl_seconds:
                return OrchestratorResponse(intent="faq", reply_text=reply_cached)
            self._cache.pop(normalized, None)

        rule_based = self._reply_from_rules(context.incoming_text)
        if rule_based:
            self._cache[normalized] = (rule_based, now)
            return OrchestratorResponse(intent="faq", reply_text=rule_based)

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
        self._cache[normalized] = (reply, now)
        return OrchestratorResponse(intent="faq", reply_text=reply)
