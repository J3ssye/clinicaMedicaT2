from __future__ import annotations

import re

from app.agents.base import AgentContext
from app.core.config import get_settings
from app.repositories.feedback_repository import FeedbackRepository
from app.schemas.chat import OrchestratorResponse
from app.services.memory.redis_memory import RedisConversationMemory


settings = get_settings()

_SKIP_COMMENT_TOKENS = {"não", "nao", "n", "nada", "-", "ok", "tudo bem", "tudo bom", "nenhum"}


def _extract_rating(text: str) -> int | None:
    """Extrai nota 1-5 do texto.
    Prioridade 1 — dígito explícito: "5", "5 ⭐", "5⭐" → 5
    Prioridade 2 — contagem de estrelas puras: "⭐⭐⭐⭐⭐" → 5
    O dígito sempre vence para evitar que o emoji decorativo vire a nota.
    """
    # Remove emoji e pontuação para isolar o dígito
    clean = re.sub(r"[^\w\s]", " ", text)
    match = re.search(r"\b([1-5])\b", clean)
    if match:
        return int(match.group(1))
    # Só conta estrelas quando não há dígito (ex: resposta puramente emoji)
    star_count = text.count("⭐") + text.count("🌟")
    if 1 <= star_count <= 5:
        return star_count
    return None


class FeedbackAgent:
    def __init__(
        self,
        repository: FeedbackRepository | None = None,
        memory: RedisConversationMemory | None = None,
    ) -> None:
        self.repository = repository or FeedbackRepository()
        self.memory = memory or RedisConversationMemory()

    async def handle(self, context: AgentContext) -> OrchestratorResponse:
        session_key = context.conversation_metadata.get("session_key")
        state = self.memory.get_state(session_key)

        if state.get("active_flow") == "post_consult_feedback":
            return await self._handle_flow(context, state, session_key)

        return await self._handle_unsolicited(context)

    # ------------------------------------------------------------------
    # Fluxo solicitado (disparado pela task pós-consulta)
    # ------------------------------------------------------------------

    async def _handle_flow(
        self, context: AgentContext, state: dict, session_key: str
    ) -> OrchestratorResponse:
        step = state.get("step", "awaiting_rating")
        appointment_id_raw = state.get("appointment_id")
        appointment_id = int(appointment_id_raw) if appointment_id_raw else None

        if step == "awaiting_rating":
            return await self._step_rating(context, state, session_key, appointment_id)
        if step == "awaiting_comment":
            return await self._step_comment(context, state, session_key, appointment_id)

        self.memory.clear_state_keys(session_key, "active_flow", "appointment_id", "step")
        return OrchestratorResponse(
            intent="post_consult_feedback",
            reply_text="Obrigada pelo seu retorno! 😊",
            llm_used=False,
        )

    async def _step_rating(
        self, context: AgentContext, state: dict, session_key: str, appointment_id: int | None
    ) -> OrchestratorResponse:
        rating = _extract_rating(context.incoming_text)

        if rating is None:
            return OrchestratorResponse(
                intent="post_consult_feedback",
                reply_text="Por favor, responda com um número de *1 a 5* (1 = muito ruim, 5 = excelente) ⭐",
                llm_used=False,
            )

        if rating >= 4:
            await self.repository.create(
                session=context.session,
                patient_id=context.patient.id,
                rating=rating,
                raw_message=context.incoming_text,
                summary="avaliação pós-consulta",
                appointment_id=appointment_id,
            )
            # Salva doctor_name antes de limpar o estado
            doctor_name = state.get("doctor_name") or ""
            self.memory.clear_state_keys(session_key, "active_flow", "appointment_id", "step", "doctor_name")
            self.memory.set_cooldown(session_key, ttl_seconds=7200)  # 2h
            review_link = self._resolve_review_link(doctor_name)
            extra = (
                f" Se quiser, também pode nos avaliar publicamente aqui: {review_link}"
                if review_link
                else ""
            )
            return OrchestratorResponse(
                intent="post_consult_feedback",
                reply_text=f"Que ótimo! Fico muito feliz com a sua nota 😊{extra}",
                llm_used=False,
            )

        # Nota baixa (1–3): pede comentário antes de persistir
        self.memory.merge_state(session_key, {"step": "awaiting_comment", "pending_rating": str(rating)})
        return OrchestratorResponse(
            intent="post_consult_feedback",
            reply_text=(
                "Sentimos muito pela experiência 😔 "
                "Pode nos contar o que aconteceu? Seu relato é muito importante para melhorarmos."
            ),
            llm_used=False,
        )

    async def _step_comment(
        self, context: AgentContext, state: dict, session_key: str, appointment_id: int | None
    ) -> OrchestratorResponse:
        rating_raw = state.get("pending_rating")
        rating = int(rating_raw) if rating_raw else None
        skip = context.incoming_text.strip().lower() in _SKIP_COMMENT_TOKENS

        await self.repository.create(
            session=context.session,
            patient_id=context.patient.id,
            rating=rating,
            raw_message=context.incoming_text if not skip else "(sem comentário)",
            # Summary sinaliza para a equipe que a entrada precisa de revisão humana
            summary="avaliação pós-consulta — nota baixa, requer revisão",
            appointment_id=appointment_id,
        )
        self.memory.clear_state_keys(
            session_key, "active_flow", "appointment_id", "step", "pending_rating"
        )
        self.memory.set_cooldown(session_key, ttl_seconds=7200)  # 2h
        return OrchestratorResponse(
            intent="post_consult_feedback",
            reply_text="Anotamos seu relato e vamos verificar o que aconteceu. Obrigada por nos avisar 🙏",
            llm_used=False,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_review_link(doctor_name: str) -> str | None:
        """Retorna o link de avaliação pública correto para o médico.
        Prioriza o link específico do médico; fallback para o link genérico."""
        dn = doctor_name.lower()
        if "vitoria" in dn or "vitória" in dn or "cunha" in dn:
            return settings.google_review_link_vitoria or settings.google_review_link or None
        if "lucas" in dn or "cirilo" in dn:
            return settings.google_review_link_lucas or settings.google_review_link or None
        # Médico desconhecido — usa o genérico se disponível
        return settings.google_review_link or None

    # ------------------------------------------------------------------
    # Feedback espontâneo (paciente menciona "feedback", "nota", "retorno")
    # ------------------------------------------------------------------

    async def _handle_unsolicited(self, context: AgentContext) -> OrchestratorResponse:
        lowered = context.incoming_text.lower()
        if "consult" in lowered and "feedback" in lowered:
            latest = await self.repository.get_latest_for_patient(context.session, context.patient.id)
            if latest:
                rating = latest.rating if latest.rating is not None else "não informada"
                return OrchestratorResponse(
                    intent="feedback",
                    reply_text=f"Localizei seu feedback mais recente. Nota: {rating}.",
                    llm_used=False,
                )
            return OrchestratorResponse(
                intent="feedback",
                reply_text="Não encontrei feedback anterior vinculado ao seu cadastro.",
                llm_used=False,
            )

        rating = _extract_rating(context.incoming_text)
        await self.repository.create(
            session=context.session,
            patient_id=context.patient.id,
            rating=rating,
            raw_message=context.incoming_text,
            summary="feedback espontâneo via canal conversacional",
        )
        review_link = self._resolve_review_link("") if rating and rating >= 4 else None
        extra = f" Você também pode nos avaliar publicamente aqui: {review_link}" if review_link else ""
        return OrchestratorResponse(
            intent="feedback",
            reply_text=f"Obrigado pelo seu retorno. Sua opinião é muito importante para nós.{extra}",
            llm_used=False,
        )
