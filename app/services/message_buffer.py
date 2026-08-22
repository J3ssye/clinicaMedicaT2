"""
Buffer temporal de mensagens por contato (debounce de 2 minutos).

Estratégia:
- Ao chegar uma mensagem de paciente, ela é adicionada a um buffer Redis
  vinculado ao chat_id (ex.: "msg_buffer:5562999999999@c.us:msgs").
- Um asyncio.Task é agendado para processar o buffer após BUFFER_WINDOW_SECONDS
  sem novas mensagens.
- Se o paciente enviar outra mensagem antes do vencimento da janela, a Task
  anterior é cancelada e uma nova é agendada — reiniciando o temporizador.
- Ao final da janela, os textos são concatenados em ordem e processados de
  uma única vez pelo HandleIncomingWebhookUseCase.

Por que asyncio.Task e não Celery?
  O uvicorn já roda o event-loop assíncrono. asyncio.Task tem zero latência de
  broker e é a opção mais simples que preserva toda a arquitetura atual.
  A desvantagem (perda de tasks em restart do processo) é aceitável dado que
  a janela é curta (2 min) e em dev o --reload já causa reloads pontuais.

Logs de buffer emitidos:
  msg_buffer_added        — mensagem adicionada ao buffer
  msg_buffer_task_scheduled — nova task agendada para o chat_id
  msg_buffer_task_cancelled — task anterior cancelada (nova mensagem chegou)
  msg_buffer_flush_start  — início do processamento após janela
  msg_buffer_flushed      — buffer lido e apagado do Redis
  msg_buffer_flush_done   — processamento concluído com sucesso
  msg_buffer_flush_error  — erro durante o processamento
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings


logger = logging.getLogger(__name__)
settings = get_settings()

# Janela de debounce: 1 minuto e 30 segundos
BUFFER_WINDOW_SECONDS: int = 45

_KEY_PREFIX = "msg_buffer"

# Registro em-processo de tasks asyncio pendentes por chat_id
# (dict compartilhado dentro do mesmo worker uvicorn)
_PENDING_TASKS: dict[str, asyncio.Task] = {}

# Singleton Redis para o buffer (separado do RedisConversationMemory)
_redis_client: Redis | None = None


def _get_redis() -> Redis | None:
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    if not settings.redis_url:
        return None
    try:
        _redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
        _redis_client.ping()
        logger.debug("msg_buffer_redis_connected")
    except RedisError:
        logger.exception("msg_buffer_redis_unavailable")
        _redis_client = None
    return _redis_client


# -----------------------------------------------------------------------
# MessageBufferService — operações no Redis
# -----------------------------------------------------------------------

class MessageBufferService:
    """Armazena mensagens por chat_id em Redis até o flush ser acionado."""

    def __init__(self) -> None:
        self._ttl = BUFFER_WINDOW_SECONDS * 4  # TTL generoso para não perder dados

    # --- chaves Redis ---

    @staticmethod
    def _msgs_key(chat_id: str) -> str:
        return f"{_KEY_PREFIX}:{chat_id}:msgs"

    @staticmethod
    def _dedup_key(chat_id: str) -> str:
        return f"{_KEY_PREFIX}:{chat_id}:dedup"

    @staticmethod
    def _ts_key(chat_id: str) -> str:
        return f"{_KEY_PREFIX}:{chat_id}:last_at"

    # --- operações ---

    def already_buffered(self, chat_id: str, external_id: str | None) -> bool:
        """Verifica se esse message_id já foi adicionado ao buffer (anti-retry WAHA)."""
        if not external_id:
            return False
        client = _get_redis()
        if client is None:
            return False
        try:
            return bool(client.sismember(self._dedup_key(chat_id), external_id))
        except RedisError:
            return False

    def add(self, chat_id: str, payload: dict[str, Any]) -> int:
        """
        Adiciona um payload de mensagem ao buffer e registra o timestamp.
        Retorna o tamanho atual do buffer (número de mensagens).
        """
        client = _get_redis()
        if client is None:
            return 0
        msgs_key  = self._msgs_key(chat_id)
        dedup_key = self._dedup_key(chat_id)
        ts_key    = self._ts_key(chat_id)
        external_id = payload.get("message_id") or ""
        try:
            pipe = client.pipeline()
            pipe.rpush(msgs_key,  json.dumps(payload, ensure_ascii=False, default=str))
            pipe.expire(msgs_key,  self._ttl)
            pipe.set(ts_key, str(time.time()), ex=self._ttl)
            if external_id:
                pipe.sadd(dedup_key, external_id)
                pipe.expire(dedup_key, self._ttl)
            results = pipe.execute()
            size = int(results[0])
            logger.info(
                "msg_buffer_added chat_id=%s buffer_size=%s external_id=%s",
                chat_id, size, external_id or "–",
            )
            return size
        except RedisError:
            logger.exception("msg_buffer_add_failed chat_id=%s", chat_id)
            return 0

    def flush(self, chat_id: str) -> list[dict[str, Any]]:
        """
        Lê atomicamente todas as mensagens do buffer e apaga as chaves.
        Retorna lista de payloads em ordem de chegada.
        """
        client = _get_redis()
        if client is None:
            return []
        msgs_key  = self._msgs_key(chat_id)
        dedup_key = self._dedup_key(chat_id)
        ts_key    = self._ts_key(chat_id)
        try:
            pipe = client.pipeline()
            pipe.lrange(msgs_key, 0, -1)
            pipe.delete(msgs_key, dedup_key, ts_key)
            results = pipe.execute()
            raw_list: list[str] = results[0] or []
            messages: list[dict[str, Any]] = []
            for item in raw_list:
                try:
                    messages.append(json.loads(item))
                except (json.JSONDecodeError, TypeError):
                    continue
            logger.info("msg_buffer_flushed chat_id=%s count=%s", chat_id, len(messages))
            return messages
        except RedisError:
            logger.exception("msg_buffer_flush_failed chat_id=%s", chat_id)
            return []

    def peek_count(self, chat_id: str) -> int:
        """Quantidade de mensagens no buffer sem removê-las."""
        client = _get_redis()
        if client is None:
            return 0
        try:
            return int(client.llen(self._msgs_key(chat_id)) or 0)
        except RedisError:
            return 0


# -----------------------------------------------------------------------
# Gerenciamento das asyncio.Tasks (registro em-processo)
# -----------------------------------------------------------------------

def cancel_pending_task(chat_id: str) -> None:
    """Cancela a task pendente para este chat_id, se houver."""
    task = _PENDING_TASKS.pop(chat_id, None)
    if task and not task.done():
        task.cancel()
        logger.debug("msg_buffer_task_cancelled chat_id=%s", chat_id)


def register_task(chat_id: str, task: asyncio.Task) -> None:
    """
    Cancela qualquer task anterior e registra a nova.
    Sempre chamar cancel_pending_task antes para evitar vazamento.
    """
    cancel_pending_task(chat_id)
    _PENDING_TASKS[chat_id] = task
    logger.debug(
        "msg_buffer_task_scheduled chat_id=%s window=%ss",
        chat_id, BUFFER_WINDOW_SECONDS,
    )


# Singleton do serviço (criado uma vez por processo)
_buffer_service: MessageBufferService | None = None


def get_buffer_service() -> MessageBufferService:
    global _buffer_service
    if _buffer_service is None:
        _buffer_service = MessageBufferService()
    return _buffer_service
