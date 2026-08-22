from __future__ import annotations

import logging

from app.core.config import get_settings
from app.integrations.whatsapp import WhatsAppClient
from app.orchestrator.graph import ChatOrchestrator
from app.repositories.message_repository import MessageRepository
from app.repositories.patient_repository import PatientRepository
from app.schemas.events import IncomingMessage, WebhookProcessResult
from app.services.media_service import MediaService
from app.services.memory.redis_memory import RedisConversationMemory
from app.services.notification_service import NotificationService


logger = logging.getLogger(__name__)


settings = get_settings()


class HandleIncomingWebhookUseCase:
    def __init__(
        self,
        session,
        message_repository: MessageRepository | None = None,
        patient_repository: PatientRepository | None = None,
        orchestrator: ChatOrchestrator | None = None,
        whatsapp_client: WhatsAppClient | None = None,
        media_service: MediaService | None = None,
        memory: RedisConversationMemory | None = None,
        notification_service: NotificationService | None = None,
    ) -> None:
        self.session = session
        self.message_repository = message_repository or MessageRepository()
        self.patient_repository = patient_repository or PatientRepository()
        self.orchestrator = orchestrator or ChatOrchestrator(self.message_repository)
        self.whatsapp_client = whatsapp_client or WhatsAppClient()
        self.media_service = media_service or MediaService()
        self.memory = memory or RedisConversationMemory()
        self.notification = notification_service or NotificationService()

    async def execute(self, message: IncomingMessage) -> dict[str, str | bool | None]:
        patient = await self.patient_repository.get_or_create_by_phone(self.session, phone=message.sender_phone, name=message.sender_name)
        await self.patient_repository.maybe_resume_ai(self.session, patient=patient)

        # Mantém o mapeamento patient_id → chat_id real do WAHA atualizado.
        # WAHA pode usar @lid (Link ID) em vez de @c.us para o mesmo número;
        # as tasks Celery consultam este mapeamento para gravar estado Redis
        # na chave correta e evitar que a confirmação/feedback caia no lugar errado.
        if message.sender_chat_id:
            self.memory.update_patient_chat_id(patient.id, message.sender_chat_id)

        if message.from_me:
            # Ignora webhooks "from_me" gerados por mensagens automáticas do próprio bot
            # (notificações proativas, bulk send para equipe, etc.).
            # is_bot_sent_id verifica um Redis SET com TTL de 5 min — muito mais robusto
            # que comparar apenas o último ID (last_bot_reply_id), que falhava em envios bulk.
            if message.message_id and self.memory.is_bot_sent_id(message.sender_chat_id, message.message_id):
                return WebhookProcessResult(status="ignored", deduplicated=False, llm_used=False).model_dump()
            await self.patient_repository.pause_ai_for_human_takeover(self.session, patient=patient)
            await self.message_repository.log_message(
                self.session,
                patient_id=patient.id,
                direction="outbound",
                content=(message.text or "[mensagem humana no chat]").strip(),
                external_id=message.message_id,
                intent="human_takeover",
                commit=False,
            )
            await self.session.commit()
            return WebhookProcessResult(status="paused_human", deduplicated=False, llm_used=False).model_dump()

        if await self.message_repository.inbound_exists(self.session, message.message_id):
            return WebhookProcessResult(status="duplicate", deduplicated=True, llm_used=False).model_dump()

        normalized_text = await self._normalize_message_text(message)
        if await self.message_repository.inbound_recent_duplicate(self.session, patient_id=patient.id, content=normalized_text):
            return WebhookProcessResult(status="duplicate", deduplicated=True, llm_used=False).model_dump()

        # Encaminha imagem de exame diretamente para médicos e secretária.
        # Feito aqui (use_case) porque é o único ponto do pipeline onde media_url
        # ainda está disponível — o orquestrador recebe apenas o texto normalizado.
        if message.media_type == "image" and message.media_url:
            try:
                await self.notification.notify_exam_image(
                    patient_name=patient.name,
                    patient_phone=patient.phone or "",
                    media_url=message.media_url,
                    media_mime_type=message.media_mime_type,
                    caption=message.media_caption or message.text or None,
                )
            except Exception:
                logger.exception("notify_exam_image_failed patient_id=%s", patient.id)

        await self.message_repository.log_message(
            self.session,
            patient_id=patient.id,
            direction="inbound",
            content=normalized_text,
            external_id=message.message_id,
            commit=False,
        )

        if patient.ai_paused:
            await self.session.commit()
            return WebhookProcessResult(status="ai_paused", deduplicated=False, llm_used=False).model_dump()

        result = await self.orchestrator.run(
            session=self.session,
            patient=patient,
            message=normalized_text,
            external_id=message.message_id,
            session_key=message.sender_chat_id or patient.phone,
        )
        if not result.silent:
            await self.message_repository.log_message(
                self.session,
                patient_id=patient.id,
                direction="outbound",
                content=result.reply_text,
                intent=result.intent,
                commit=False,
            )
        await self.session.commit()
        if not result.silent:
            await self._deliver_whatsapp_reply(chat_id=message.sender_chat_id, reply_text=result.reply_text, reply_to=message.message_id)
        return WebhookProcessResult(status="processed", deduplicated=False, intent=result.intent, llm_used=result.llm_used).model_dump()

    async def _deliver_whatsapp_reply(self, *, chat_id: str, reply_text: str, reply_to: str | None) -> None:
        try:
            if settings.waha_mark_as_seen_before_reply:
                await self.whatsapp_client.send_seen(chat_id=chat_id)
            response = await self.whatsapp_client.send_text(chat_id=chat_id, text=reply_text, reply_to=reply_to)
            message_id = str(response.get("id") or response.get("messageId") or "") if isinstance(response, dict) else ""
            if message_id:
                # Registra no SET de IDs enviados pelo bot (proteção contra bulk)
                self.memory.track_bot_sent_id(chat_id, message_id)
                # Mantém last_bot_reply_id por compatibilidade com código legado
                self.memory.merge_state(chat_id, {"last_bot_reply_id": message_id})
        except Exception:
            return

    async def _normalize_message_text(self, message: IncomingMessage) -> str:
        text = (message.text or "").strip()
        if message.media_type == "audio" and message.media_url:
            processed = await self.media_service.transcribe_audio_from_url(media_url=message.media_url, mime_type=message.media_mime_type, caption=message.media_caption)
            if processed and processed.text:
                return processed.text
            return text or "[Áudio enviado pelo paciente sem transcrição disponível]"

        if message.media_type == "image" and message.media_url:
            fallback = text or message.media_caption or ""
            if fallback:
                return f"[Imagem enviada pelo paciente] {fallback}".strip()
            return "[Imagem enviada pelo paciente sem descrição]"

        return text
