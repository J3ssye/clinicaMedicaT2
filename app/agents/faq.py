from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
import time
import unicodedata

from app.agents.base import AgentContext
from app.core.config import get_settings
from app.integrations.llm import LLMService
from app.schemas.chat import OrchestratorResponse


settings = get_settings()


class FAQAgent:
    def __init__(self, llm: LLMService | None = None) -> None:
        self.llm = llm or LLMService()
        self._cache: dict[str, tuple[str, float]] = {}

    def _knowledge_base(self) -> str:
        path = Path(settings.faq_kb_path)
        return path.read_text(encoding="utf-8") if path.exists() else ""

    @staticmethod
    def _normalize(text: str) -> str:
        normalized = unicodedata.normalize("NFKD", text.lower().strip())
        no_accents = "".join(char for char in normalized if not unicodedata.combining(char))
        return re.sub(r"\s+", " ", no_accents)

    def _find_relevant_excerpt(self, question: str, kb: str) -> str | None:
        q_tokens = [t for t in re.findall(r"[a-zA-ZÀ-ÿ0-9]+", self._normalize(question)) if len(t) > 2]
        if not q_tokens:
            return None
        token_counts = Counter(q_tokens)
        best_excerpt = None
        best_score = 0
        for block in [chunk.strip() for chunk in kb.split("\n\n") if chunk.strip()]:
            normalized_block = self._normalize(block)
            score = sum(token_counts[token] for token in token_counts if token in normalized_block)
            if score > best_score:
                best_score = score
                best_excerpt = block
        # Perguntas curtas (≤ 3 tokens) têm menos palavras para pontuar — threshold menor.
        min_score = 1 if len(q_tokens) <= 3 else 2
        return best_excerpt if best_score >= min_score else None

    async def handle(self, context: AgentContext) -> OrchestratorResponse:
        normalized = self._normalize(context.incoming_text)
        now = time.time()

        # Cache só para perguntas sem histórico ativo (evita resposta engessada em conversas longas)
        if not context.history:
            cached = self._cache.get(normalized)
            if cached and now - cached[1] < settings.faq_cache_ttl_seconds:
                return OrchestratorResponse(intent="faq", reply_text=cached[0], llm_used=False)

        kb = self._knowledge_base()
        # Trecho relevante do KB é fornecido ao LLM como contexto, não colado diretamente
        excerpt = self._find_relevant_excerpt(context.incoming_text, kb)
        context_block = f"\n\nTrecho mais relevante da base de conhecimento:\n{excerpt}" if excerpt else ""

        fallback_static = "Posso te ajudar com informações da clínica, valores, local de atendimento e próximos passos para agendamento."
        if not settings.llm_enable_faq_generation_fallback:
            return OrchestratorResponse(intent="faq", reply_text=fallback_static, llm_used=False)

        prompt = (
            f"{settings.clinic_assistant_system_prompt}\n\n"
            "BASE DE CONHECIMENTO COMPLETA:\n"
            f"{kb}"
            f"{context_block}\n\n"
            "REGRAS DE RESPOSTA:\n"
            "- Use a base de conhecimento para responder. Seja clara, acolhedora e completa — nunca truncada.\n"
            "- Se a pergunta pedir justificativa (ex.: 'por que precisa do CPF?', 'como funciona o agendamento?'), "
            "explique de forma humana e contextualizada, usando as informações do sistema.\n"
            "- Se a informação solicitada NÃO estiver na base de conhecimento, informe honestamente que "
            "não tem essa informação disponível no momento e sugira contato direto com a clínica ou ofereça "
            "agendamento se fizer sentido — NUNCA invente dados, valores, horários ou disponibilidade.\n"
            "- Reserve o encaminhamento explícito para a equipe ('vou encaminhar', 'entrarão em contato') "
            "APENAS para: receita médica, imagens clínicas, exceções de agenda e situações que exijam "
            "decisão humana imediata. Para perguntas informativas sem resposta no KB, responda que não tem "
            "essa informação no momento sem prometer acionamento da equipe.\n"
            "- Nunca copie a base de conhecimento literalmente; reformule de forma conversacional.\n"
            "- Feche sempre com um próximo passo objetivo quando fizer sentido."
        )
        reply = self.llm.draft_reply(prompt, context.incoming_text, history=context.history)
        final_reply = reply or fallback_static
        if not context.history:
            self._cache[normalized] = (final_reply, now)
        return OrchestratorResponse(intent="faq", reply_text=final_reply, llm_used=bool(reply))
