from __future__ import annotations

import re
import unicodedata

from app.agents.base import AgentContext
from app.repositories.appointment_repository import AppointmentRepository
from app.schemas.chat import OrchestratorResponse
from app.services.memory.redis_memory import RedisConversationMemory


def _norm(text: str) -> str:
    nkfd = unicodedata.normalize("NFKD", text.lower().strip())
    return "".join(ch for ch in nkfd if not unicodedata.combining(ch))


def _is_yes(text: str) -> bool:
    return bool(re.search(
        r"\bsim\b|\bconfirm|\bvou\b|\bpresente\b|\bclaro\b|\bpode\b|\bafirm|\bestare\b|\bestarei\b",
        text,
    ))


def _is_no(text: str) -> bool:
    return bool(re.search(
        r"\bnao\b|\bneg|\bcancela|\bimpossivel\b|\bnao consigo\b|\bnao poderei\b|\bnao irei\b|\bnao vou\b|\bnao da\b",
        text,
    ))


class ConfirmationAgent:
    """Gerencia o fluxo de confirmação de consulta disparado pela task D-1."""

    def __init__(
        self,
        appointment_repository: AppointmentRepository | None = None,
        memory: RedisConversationMemory | None = None,
    ) -> None:
        self.appointment_repository = appointment_repository or AppointmentRepository()
        self.memory = memory or RedisConversationMemory()

    async def handle(self, context: AgentContext) -> OrchestratorResponse:
        session_key = context.conversation_metadata.get("session_key")
        state = self.memory.get_state(session_key)
        step = state.get("step", "awaiting_confirmation")
        appointment_id_raw = state.get("appointment_id")
        normalized = _norm(context.incoming_text)

        if step == "awaiting_confirmation":
            return await self._step_confirmation(context, session_key, appointment_id_raw, normalized)

        if step == "awaiting_reschedule_decision":
            return await self._step_reschedule_decision(context, session_key, appointment_id_raw, normalized)

        # Estado desconhecido — limpa e libera
        self.memory.clear_state_keys(session_key, "active_flow", "appointment_id", "step")
        return OrchestratorResponse(
            intent="d1_confirmation",
            reply_text="Como posso te ajudar?",
            llm_used=False,
        )

    async def _step_confirmation(
        self,
        context: AgentContext,
        session_key: str,
        appointment_id_raw: str | None,
        normalized: str,
    ) -> OrchestratorResponse:
        if _is_yes(normalized):
            await self._confirm_appointment(context, appointment_id_raw)
            self.memory.clear_state_keys(session_key, "active_flow", "appointment_id", "step")
            self.memory.set_cooldown(session_key, ttl_seconds=1800)  # 30 min
            return OrchestratorResponse(
                intent="d1_confirmation",
                reply_text="Ótimo! Sua presença está confirmada. Te esperamos amanhã 😊",
                llm_used=False,
            )

        # Paciente quer remarcar diretamente — pula a pergunta intermediária
        wants_reschedule = any(
            t in normalized for t in ("remarcar", "reagendar", "trocar", "mudar", "outro horario", "outro dia")
        )
        if wants_reschedule:
            self.memory.clear_state_keys(session_key, "active_flow", "appointment_id", "step")
            self.memory.merge_state(session_key, {"active_flow": "scheduling"})
            return OrchestratorResponse(
                intent="scheduling",
                reply_text="Claro! Qual data e horário seriam melhores para você?",
                llm_used=False,
            )

        if _is_no(normalized):
            self.memory.merge_state(session_key, {"step": "awaiting_reschedule_decision"})
            return OrchestratorResponse(
                intent="d1_confirmation",
                reply_text=(
                    "Entendido. Você deseja *remarcar* para outro horário "
                    "ou prefere *cancelar* a consulta?"
                ),
                llm_used=False,
            )

        return OrchestratorResponse(
            intent="d1_confirmation",
            reply_text=(
                "Por favor, responda *SIM* para confirmar sua consulta de amanhã "
                "ou *NÃO* caso não possa comparecer."
            ),
            llm_used=False,
        )

    async def _step_reschedule_decision(
        self,
        context: AgentContext,
        session_key: str,
        appointment_id_raw: str | None,
        normalized: str,
    ) -> OrchestratorResponse:
        wants_reschedule = (
            _is_yes(normalized)
            or any(t in normalized for t in ("remarcar", "reagendar", "outro horario", "outro dia"))
        )
        wants_cancel = (
            _is_no(normalized)
            or any(t in normalized for t in ("cancelar", "cancela", "nao quero", "desmarcar"))
        )

        if wants_reschedule:
            # Transfere para o fluxo de agendamento existente
            self.memory.clear_state_keys(session_key, "active_flow", "appointment_id", "step")
            self.memory.merge_state(session_key, {"active_flow": "scheduling"})
            return OrchestratorResponse(
                intent="scheduling",
                reply_text="Claro! Qual data e horário seriam melhores para você?",
                llm_used=False,
            )

        if wants_cancel:
            await self._cancel_appointment(context, appointment_id_raw)
            self.memory.clear_state_keys(session_key, "active_flow", "appointment_id", "step")
            self.memory.set_cooldown(session_key, ttl_seconds=1800)  # 30 min
            return OrchestratorResponse(
                intent="d1_confirmation",
                reply_text="Consulta cancelada. Quando quiser reagendar, é só me chamar 😊",
                llm_used=False,
            )

        return OrchestratorResponse(
            intent="d1_confirmation",
            reply_text="Responda *REMARCAR* para escolher outro horário ou *CANCELAR* para desmarcar.",
            llm_used=False,
        )

    async def _confirm_appointment(
        self, context: AgentContext, appointment_id_raw: str | None
    ) -> None:
        if not appointment_id_raw:
            return
        try:
            appointment_id = int(appointment_id_raw)
        except (ValueError, TypeError):
            return
        appointment = await self.appointment_repository.get_by_id(context.session, appointment_id)
        if appointment:
            await self.appointment_repository.mark_confirmed(context.session, appointment=appointment)

    async def _cancel_appointment(
        self, context: AgentContext, appointment_id_raw: str | None
    ) -> None:
        if not appointment_id_raw:
            return
        try:
            appointment_id = int(appointment_id_raw)
        except (ValueError, TypeError):
            return
        appointment = await self.appointment_repository.get_by_id(context.session, appointment_id)
        if appointment:
            await self.appointment_repository.mark_cancelled(
                context.session,
                appointment=appointment,
                reason="cancelado pelo paciente via confirmação D-1",
            )
