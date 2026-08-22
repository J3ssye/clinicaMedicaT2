from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings
from app.schemas.chat import ChatMessage


logger = logging.getLogger(__name__)
settings = get_settings()


class RedisConversationMemory:
    def __init__(self) -> None:
        self.enabled = bool(settings.redis_url)
        self.ttl_seconds = settings.conversation_memory_ttl_seconds
        self.history_limit = settings.conversation_memory_history_limit
        self._client: Redis | None = None
        self._connection_attempted = False

    def _get_client(self) -> Redis | None:
        if not self.enabled:
            return None
        if self._connection_attempted:
            return self._client
        self._connection_attempted = True
        try:
            self._client = Redis.from_url(settings.redis_url, decode_responses=True)
            self._client.ping()
        except RedisError:
            logger.exception("redis_memory_unavailable")
            self._client = None
        return self._client

    def _state_key(self, session_key: str) -> str:
        return f"chat:state:{session_key}"

    def _history_key(self, session_key: str) -> str:
        return f"chat:history:{session_key}"

    def get_state(self, session_key: str | None) -> dict[str, Any]:
        if not session_key:
            return {}
        client = self._get_client()
        if client is None:
            return {}
        try:
            raw = client.get(self._state_key(session_key))
            return json.loads(raw) if raw else {}
        except (RedisError, json.JSONDecodeError):
            logger.exception("redis_memory_get_state_failed")
            return {}

    def set_state(self, session_key: str | None, state: dict[str, Any]) -> None:
        if not session_key:
            return
        client = self._get_client()
        if client is None:
            return
        try:
            client.setex(self._state_key(session_key), self.ttl_seconds, json.dumps(state, ensure_ascii=False))
        except RedisError:
            logger.exception("redis_memory_set_state_failed")

    def merge_state(self, session_key: str | None, updates: dict[str, Any]) -> dict[str, Any]:
        current = self.get_state(session_key)
        cleaned_updates = {key: value for key, value in updates.items() if value is not None}
        current.update(cleaned_updates)
        self.set_state(session_key, current)
        return current

    def clear_state_keys(self, session_key: str | None, *keys: str) -> dict[str, Any]:
        state = self.get_state(session_key)
        for key in keys:
            state.pop(key, None)
        self.set_state(session_key, state)
        return state

    def append_history(self, session_key: str | None, *, role: str, content: str) -> None:
        if not session_key or not content.strip():
            return
        client = self._get_client()
        if client is None:
            return
        payload = json.dumps({"role": role, "content": content}, ensure_ascii=False)
        try:
            pipeline = client.pipeline()
            pipeline.rpush(self._history_key(session_key), payload)
            pipeline.ltrim(self._history_key(session_key), -self.history_limit, -1)
            pipeline.expire(self._history_key(session_key), self.ttl_seconds)
            pipeline.execute()
        except RedisError:
            logger.exception("redis_memory_append_history_failed")

    # ------------------------------------------------------------------
    # Mapeamento patient_id → waha_chat_id
    # WAHA pode entregar webhooks com @lid (Link ID interno) em vez de @c.us.
    # Guardamos a chave real usada na última conversa para que as tasks
    # Celery gravem o estado Redis na mesma chave que o orquestrador vai ler.
    # TTL longo (90 dias) porque é dado de identidade, não de sessão.
    # ------------------------------------------------------------------
    _CHAT_ID_TTL = 60 * 60 * 24 * 90  # 90 dias

    # ------------------------------------------------------------------
    # Cooldown pós-fluxo
    # Após um fluxo proativo terminar (confirmação, feedback), grava um TTL
    # curto que sinaliza ao orquestrador para ignorar mensagens ambíguas
    # (fallback, acknowledgements) e evitar reengajamento indesejado.
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Greeting diário
    # Garante que a apresentação da Malu seja enviada apenas uma vez por dia,
    # independentemente do intent que ativou a resposta.
    # TTL de 30h cobre virada de meia-noite com margem de segurança.
    # ------------------------------------------------------------------

    def is_greeted_today(self, session_key: str | None) -> bool:
        if not session_key:
            return False
        client = self._get_client()
        if client is None:
            return False  # fail-open: melhor não cumprimentar que travar
        try:
            today = date.today().isoformat()
            return bool(client.exists(f"chat:greeted:{session_key}:{today}"))
        except RedisError:
            logger.exception("redis_memory_is_greeted_today_failed")
            return False

    def mark_greeted_today(self, session_key: str | None) -> None:
        if not session_key:
            return
        client = self._get_client()
        if client is None:
            return
        try:
            today = date.today().isoformat()
            client.setex(f"chat:greeted:{session_key}:{today}", 60 * 60 * 30, "1")
        except RedisError:
            logger.exception("redis_memory_mark_greeted_today_failed")

    def set_cooldown(self, session_key: str | None, ttl_seconds: int = 1800) -> None:
        if not session_key:
            return
        client = self._get_client()
        if client is None:
            return
        try:
            client.setex(f"chat:cooldown:{session_key}", ttl_seconds, "1")
        except RedisError:
            logger.exception("redis_memory_set_cooldown_failed")

    def is_in_cooldown(self, session_key: str | None) -> bool:
        if not session_key:
            return False
        client = self._get_client()
        if client is None:
            return False
        try:
            return bool(client.exists(f"chat:cooldown:{session_key}"))
        except RedisError:
            logger.exception("redis_memory_is_in_cooldown_failed")
            return False

    def update_patient_chat_id(self, patient_id: int, chat_id: str) -> None:
        client = self._get_client()
        if client is None:
            return
        try:
            client.setex(f"patient:chat_id:{patient_id}", self._CHAT_ID_TTL, chat_id)
        except RedisError:
            logger.exception("redis_memory_update_patient_chat_id_failed")

    def get_patient_chat_id(self, patient_id: int) -> str | None:
        client = self._get_client()
        if client is None:
            return None
        try:
            return client.get(f"patient:chat_id:{patient_id}")
        except RedisError:
            logger.exception("redis_memory_get_patient_chat_id_failed")
            return None

    # ------------------------------------------------------------------
    # Rastreamento de IDs enviados pelo bot
    # Evita que webhooks "from_me" gerados por mensagens automáticas do bot
    # (notificações, proativos, etc.) acionem o mecanismo de pausa de IA.
    # Um SET Redis com TTL curto (5 min) é suficiente para cobrir o tempo
    # entre o envio e o retorno do webhook.
    # ------------------------------------------------------------------
    _BOT_SENT_TTL = 300  # segundos

    def track_bot_sent_id(self, chat_id: str, message_id: str) -> None:
        """Registra um message_id enviado pelo bot para o chat_id informado."""
        if not chat_id or not message_id:
            return
        client = self._get_client()
        if client is None:
            return
        try:
            key = f"chat:bot_sent:{chat_id}"
            pipeline = client.pipeline()
            pipeline.sadd(key, message_id)
            pipeline.expire(key, self._BOT_SENT_TTL)
            pipeline.execute()
        except RedisError:
            logger.exception("redis_track_bot_sent_id_failed chat_id=%s", chat_id)

    def is_bot_sent_id(self, chat_id: str, message_id: str) -> bool:
        """Retorna True se message_id foi enviado pelo bot (e não é intervenção humana)."""
        if not chat_id or not message_id:
            return False
        client = self._get_client()
        if client is None:
            return False
        try:
            return bool(client.sismember(f"chat:bot_sent:{chat_id}", message_id))
        except RedisError:
            logger.exception("redis_is_bot_sent_id_failed chat_id=%s", chat_id)
            return False

    def get_history(self, session_key: str | None, *, limit: int) -> list[ChatMessage]:
        if not session_key:
            return []
        client = self._get_client()
        if client is None:
            return []
        try:
            items = client.lrange(self._history_key(session_key), -limit, -1)
        except RedisError:
            logger.exception("redis_memory_get_history_failed")
            return []
        messages: list[ChatMessage] = []
        for item in items:
            try:
                payload = json.loads(item)
                role = payload.get("role")
                content = (payload.get("content") or "").strip()
                if role in {"user", "assistant", "system"} and content:
                    messages.append(ChatMessage(role=role, content=content))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
        return messages
