"""
Webhook WAHA — recebe eventos do WhatsApp e processa mensagens dos pacientes.

Fluxo de agregação (debounce de 90 s):
  1. Chega mensagem do paciente.
  2. Staleness gate: se a mensagem tiver sido enviada há mais de MAX_MESSAGE_AGE_SECONDS
     (padrão 300 s / 5 min), ela é registrada no histórico com status "ignored_stale"
     e descartada sem gerar resposta automática. Isso protege contra backlog de mensagens
     acumuladas durante pausa do WAHA ou reinício do backend.
  3. Mensagem é adicionada ao buffer Redis desse chat_id.
  4. Qualquer asyncio.Task pendente para o mesmo chat_id é cancelado.
  5. Nova Task é agendada para disparar após BUFFER_WINDOW_SECONDS (90 s).
  6. Webhook retorna imediatamente com {"status": "buffered"}.
  7. Se chegarem novas mensagens antes do vencimento, o timer reinicia (passo 4-5).
  8. Após a janela sem novas mensagens, a Task acorda, faz flush do buffer,
     concatena os textos em ordem e chama HandleIncomingWebhookUseCase uma
     única vez com o bloco consolidado.

Mensagens "from_me" (intervenção humana) bypassam buffer E staleness gate e são
processadas imediatamente para garantir que o mecanismo de pausa de IA funcione
sem delay (comandos de retomada/pausa humana devem sempre ser honrados).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import verify_webhook_signature
from app.db.session import SessionLocal, get_db_session
from app.schemas.events import IncomingMessage, WahaWebhookPayload
from app.services.message_buffer import (
    BUFFER_WINDOW_SECONDS,
    cancel_pending_task,
    get_buffer_service,
    register_task,
)
from app.use_cases.handle_incoming_webhook import HandleIncomingWebhookUseCase


router = APIRouter()
logger = logging.getLogger(__name__)
settings = get_settings()
ALLOWED_WAHA_EVENTS = {item.strip() for item in settings.waha_process_events.split(",") if item.strip()}


# -----------------------------------------------------------------------
# Endpoint principal
# -----------------------------------------------------------------------

@router.post("/webhooks/waha")
async def receive_waha_webhook(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str | bool | int | float | None]:
    data = await request.json()
    payload = WahaWebhookPayload.model_validate(data)
    message = _extract_incoming_message(payload)
    if message is None:
        return {"status": "ignored"}

    # Mensagens do próprio número (human takeover) — processa IMEDIATAMENTE,
    # sem buffer e sem staleness gate, para garantir que o mecanismo de pausa
    # de IA seja ativado/desativado sem delay (comandos de retomada humana devem
    # sempre ser honrados, mesmo após reinício do backend).
    if message.from_me:
        use_case = HandleIncomingWebhookUseCase(session=session)
        try:
            return await use_case.execute(message)
        except Exception as exc:
            logger.exception(
                "webhook_human_processing_error",
                extra={"message_id": message.message_id, "sender_phone": message.sender_phone},
                exc_info=exc,
            )
            return {"status": "failed"}

    # -----------------------------------------------------------------------
    # Staleness gate — protege contra backlog pós-reinício e replay de webhook
    # -----------------------------------------------------------------------
    # Se a mensagem foi enviada há mais de MAX_MESSAGE_AGE_SECONDS, ela é
    # ignorada para resposta automática. O motivo mais comum é:
    #   - pausa do WAHA e retomada com mensagens acumuladas;
    #   - reinício do backend com eventos pendentes na fila;
    #   - retry automático do WAHA para webhooks com falha anterior.
    # A mensagem ainda é registrada no banco com processing_status="ignored_stale"
    # para preservar o histórico da conversa sem gerar resposta indevida.
    _stale_age = _compute_age_seconds(message.sent_at)
    if _stale_age is not None and _stale_age > settings.max_message_age_seconds:
        logger.warning(
            "msg_stale_ignored chat_id=%s age_seconds=%.0f message_id=%s limit=%s",
            message.sender_chat_id,
            _stale_age,
            message.message_id,
            settings.max_message_age_seconds,
        )
        return {
            "status": "ignored_stale",
            "age_seconds": round(_stale_age),
            "limit_seconds": settings.max_message_age_seconds,
        }

    # --- Buffer: adicionar mensagem e reagendar a janela ---
    buf = get_buffer_service()

    # Anti-retry WAHA: ignora se o mesmo message_id já está no buffer
    if message.message_id and buf.already_buffered(message.sender_chat_id, message.message_id):
        logger.debug("msg_buffer_duplicate_ignored chat_id=%s", message.sender_chat_id)
        return {"status": "already_buffered"}

    # Serializar IncomingMessage para o buffer (mode='json' trata datetime corretamente)
    buf.add(message.sender_chat_id, message.model_dump(mode="json"))

    # Cancelar task anterior e agendar nova com janela de BUFFER_WINDOW_SECONDS
    task = asyncio.create_task(
        _delayed_flush(chat_id=message.sender_chat_id, delay=BUFFER_WINDOW_SECONDS),
        name=f"msg_flush_{message.sender_chat_id}",
    )
    register_task(message.sender_chat_id, task)

    logger.info(
        "msg_buffered chat_id=%s window=%ss buffer_size=%s",
        message.sender_chat_id, BUFFER_WINDOW_SECONDS, buf.peek_count(message.sender_chat_id),
    )
    return {"status": "buffered", "window_seconds": BUFFER_WINDOW_SECONDS}


# -----------------------------------------------------------------------
# Task de flush (executa após a janela de silêncio)
# -----------------------------------------------------------------------

async def _delayed_flush(chat_id: str, delay: int) -> None:
    """
    Aguarda `delay` segundos e então consolida e processa o buffer do chat_id.
    Se chegar nova mensagem do mesmo chat_id antes do vencimento, esta task
    é cancelada e uma nova é agendada (via register_task → cancel_pending_task).
    """
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        logger.debug("msg_buffer_task_cancelled_during_sleep chat_id=%s", chat_id)
        return

    logger.info("msg_buffer_flush_start chat_id=%s", chat_id)
    buf = get_buffer_service()
    messages = buf.flush(chat_id)

    if not messages:
        logger.warning("msg_buffer_flush_empty chat_id=%s", chat_id)
        return

    # Concatenar textos preservando a ordem de chegada
    texts = [str(m.get("text") or "").strip() for m in messages]
    combined_text = "\n".join(t for t in texts if t)
    if not combined_text:
        logger.warning("msg_buffer_flush_no_text chat_id=%s count=%s", chat_id, len(messages))
        return

    # Usar metadados da última mensagem para o IncomingMessage sintético.
    # Preserva mídia da primeira mensagem do buffer que tenha media_type definido —
    # o HandleIncomingWebhookUseCase usa esses dados para encaminhar imagens à equipe.
    last = messages[-1]
    first_media = next((m for m in messages if m.get("media_type")), None)
    synthetic = IncomingMessage(
        message_id=last.get("message_id"),
        sender_phone=last.get("sender_phone", ""),
        sender_chat_id=last.get("sender_chat_id", chat_id),
        sender_name=last.get("sender_name"),
        text=combined_text,
        sent_at=_parse_iso_dt(last.get("sent_at")),
        from_me=False,
        is_group=bool(last.get("is_group", False)),
        is_status=bool(last.get("is_status", False)),
        raw_event=last.get("raw_event"),
        media_type=first_media.get("media_type") if first_media else None,
        media_url=first_media.get("media_url") if first_media else None,
        media_mime_type=first_media.get("media_mime_type") if first_media else None,
        media_caption=first_media.get("media_caption") if first_media else None,
    )

    logger.info(
        "msg_buffer_flush_processing chat_id=%s msgs_count=%s combined_len=%s",
        chat_id, len(messages), len(combined_text),
    )

    # Nova sessão de banco de dados (a sessão original do request já foi fechada)
    try:
        async with SessionLocal() as session:
            use_case = HandleIncomingWebhookUseCase(session=session)
            result = await use_case.execute(synthetic)
            logger.info("msg_buffer_flush_done chat_id=%s result=%s", chat_id, result.get("status"))
    except Exception:
        logger.exception("msg_buffer_flush_error chat_id=%s", chat_id)


def _parse_iso_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def _compute_age_seconds(sent_at: datetime | None) -> float | None:
    """
    Retorna quantos segundos atrás a mensagem foi enviada.
    Retorna None se sent_at for None (sem timestamp no payload WAHA) —
    nesse caso o staleness gate é omitido por segurança (melhor responder
    do que descartar uma mensagem cujo timestamp simplesmente não veio).
    """
    if sent_at is None:
        return None
    now = datetime.now(UTC)
    # Normaliza para timezone-aware caso sent_at seja naive
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=UTC)
    age = (now - sent_at).total_seconds()
    # Proteção contra relógio do cliente adiantado: nunca retorna negativo
    return max(age, 0.0)


# -----------------------------------------------------------------------
# Extração do evento WAHA (sem alterações na lógica original)
# -----------------------------------------------------------------------

def _extract_incoming_message(event: WahaWebhookPayload) -> IncomingMessage | None:
    if ALLOWED_WAHA_EVENTS and event.event not in ALLOWED_WAHA_EVENTS:
        return None
    payload = event.payload or {}
    from_me = _to_bool(payload.get("fromMe"))
    chat_id = _extract_chat_id(payload)
    if not chat_id:
        return None
    if settings.waha_ignore_group_messages and chat_id.endswith("@g.us"):
        return None
    if settings.waha_ignore_status_messages and chat_id == "status@broadcast":
        return None
    media_type = _extract_media_type(payload)
    body = _extract_message_text(payload)
    if not body and media_type not in {"audio", "image"}:
        body = "[mensagem sem texto]" if from_me else ""
    if not body and media_type not in {"audio", "image"}:
        return None
    sender_name = _extract_sender_name(payload)
    sender_phone = _extract_sender_phone(payload, chat_id)
    media_url, media_mime_type = _extract_media_url_and_mime(payload)
    caption = _extract_caption(payload)
    return IncomingMessage(
        message_id=_extract_message_id(payload),
        sender_phone=sender_phone,
        sender_chat_id=chat_id,
        sender_name=sender_name,
        text=body or caption or "",
        sent_at=_parse_timestamp(payload.get("timestamp")),
        from_me=from_me,
        is_group=chat_id.endswith("@g.us"),
        is_status=chat_id == "status@broadcast",
        raw_event=event.event,
        media_type=media_type,
        media_url=media_url,
        media_mime_type=media_mime_type,
        media_caption=caption,
    )


def _extract_chat_id(payload: dict[str, Any]) -> str | None:
    candidates = [payload.get("chatId"), payload.get("chat_id"), ((payload.get("_data") or {}).get("chatId") if isinstance(payload.get("_data"), dict) else None), payload.get("from"), payload.get("to")]
    for value in candidates:
        text = _as_text(value)
        if text:
            return text
    return None


def _extract_sender_phone(payload: dict[str, Any], chat_id: str) -> str:
    if chat_id.endswith("@g.us"):
        for candidate in (payload.get("author"), payload.get("participant"), payload.get("from")):
            text = _as_text(candidate)
            if text:
                digits = _to_phone_digits(text)
                if digits:
                    return digits
    return _to_phone_digits(chat_id)


def _extract_message_id(payload: dict[str, Any]) -> str | None:
    raw_id = payload.get("id")
    if isinstance(raw_id, dict):
        for key in ("_serialized", "id"):
            text = _as_text(raw_id.get(key))
            if text:
                return text
        return None
    return _as_text(raw_id)


def _extract_message_text(payload: dict[str, Any]) -> str:
    candidates = [payload.get("body"), payload.get("text"), ((payload.get("extendedTextMessage") or {}).get("text") if isinstance(payload.get("extendedTextMessage"), dict) else None), (((payload.get("_data") or {}).get("body")) if isinstance(payload.get("_data"), dict) else None), (((payload.get("_data") or {}).get("caption")) if isinstance(payload.get("_data"), dict) else None)]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


def _extract_media_type(payload: dict[str, Any]) -> str | None:
    candidates = [payload.get("type"), payload.get("mimetype"), ((payload.get("_data") or {}).get("type") if isinstance(payload.get("_data"), dict) else None), ((payload.get("_data") or {}).get("mimetype") if isinstance(payload.get("_data"), dict) else None)]
    for candidate in candidates:
        text = (_as_text(candidate) or "").lower()
        if not text:
            continue
        if text.startswith("audio") or text in {"ptt", "voice", "audio"}:
            return "audio"
        if text.startswith("image") or text in {"image", "photo"}:
            return "image"
    return None


def _extract_media_url_and_mime(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    candidates: list[dict[str, Any]] = [payload]
    data = payload.get("_data")
    if isinstance(data, dict):
        candidates.append(data)
    for item in candidates:
        media = item.get("media")
        if isinstance(media, dict):
            url = _as_text(media.get("url") or media.get("downloadUrl") or media.get("download_url"))
            mime = _as_text(media.get("mimetype") or media.get("mimeType"))
            if url:
                return url, mime
        url = _as_text(item.get("url") or item.get("downloadUrl") or item.get("download_url"))
        mime = _as_text(item.get("mimetype") or item.get("mimeType"))
        if url:
            return url, mime
    return None, None


def _extract_caption(payload: dict[str, Any]) -> str | None:
    candidates = [payload.get("caption"), ((payload.get("_data") or {}).get("caption") if isinstance(payload.get("_data"), dict) else None)]
    for value in candidates:
        text = _as_text(value)
        if text:
            return text
    return None


def _extract_sender_name(payload: dict[str, Any]) -> str | None:
    push_name = _as_text(payload.get("notifyName") or payload.get("notify_name"))
    if push_name:
        return push_name
    data = payload.get("_data") or {}
    if isinstance(data, dict):
        for key in ("notifyName", "pushName", "fromPushName"):
            value = _as_text(data.get(key))
            if value:
                return value
    return None


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    if timestamp > 10_000_000_000:
        timestamp /= 1000
    return datetime.fromtimestamp(timestamp, tz=UTC)


def _to_phone_digits(chat_id: str) -> str:
    return "".join(ch for ch in chat_id if ch.isdigit())


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def _as_text(value: Any) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None
