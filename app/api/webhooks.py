import json
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.orchestrator.graph import ChatOrchestrator
from app.schemas.events import IncomingMessage, WahaWebhookPayload
from app.services.message_service import MessageService
from app.services.patient_service import PatientService
from app.services.rate_limit import enforce_rate_limit
from app.services.security import verify_webhook_signature
from app.services.waha_client import WahaClient


router = APIRouter()
orchestrator = ChatOrchestrator()
waha_client = WahaClient()
logger = logging.getLogger(__name__)


@router.post("/webhooks/waha", dependencies=[Depends(enforce_rate_limit)])
async def receive_waha_webhook(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    body = await verify_webhook_signature(request)
    payload = WahaWebhookPayload.model_validate(json.loads(body or b"{}"))
    message = _extract_incoming_message(payload)
    if message is None:
        return {"status": "ignored"}

    # idempotência: se já processamos esse id, devolve 200 para evitar retry
    if await MessageService.inbound_exists(session, message.message_id):
        return {"status": "duplicate"}

    patient = await PatientService.get_or_create_by_phone(
        session, phone=message.sender_phone, name=message.sender_name
    )
    if await MessageService.inbound_recent_duplicate(
        session, patient_id=patient.id, content=message.text
    ):
        return {"status": "duplicate"}
    await MessageService.log_message(
        session,
        patient_id=patient.id,
        direction="inbound",
        content=message.text,
        external_id=message.message_id,
    )

    try:
        result = await orchestrator.run(session=session, patient=patient, message=message.text)
        reply_text = result.reply_text
        intent = result.intent
    except Exception as exc:  # noqa: BLE001
        # registra o erro e não gera 500 para evitar retries
        logger.exception("orchestrator_error", extra={"message_id": message.message_id, "patient": patient.id})
        reply_text = (
            "No momento estou com instabilidade. Pode mandar novamente em instantes, "
            "ou deixe seu resumo que já te retorno."
        )
        intent = "fallback_error"

    await MessageService.log_message(
        session,
        patient_id=patient.id,
        direction="outbound",
        content=reply_text,
        intent=intent,
    )
    await waha_client.send_text(chat_id=message.sender_phone, text=reply_text)
    return {"status": "processed", "intent": intent}


def _extract_incoming_message(payload: WahaWebhookPayload) -> IncomingMessage | None:
    if payload.event not in {"message", "message.any"}:
        return None
    raw = payload.payload

    # Texto
    text = (
        raw.get("body")
        or raw.get("text")
        or raw.get("message", {}).get("conversation")
        or raw.get("message", {}).get("extendedTextMessage", {}).get("text")
    )
    if not text:
        return None

    # Remetente
    sender = raw.get("from") or raw.get("sender", {}).get("id") or ""
    if sender.endswith("@status") or "status@broadcast" in sender or "broadcast" in sender:
        return None  # ignora status/broadcast/stories
    if raw.get("fromMe"):
        return None  # ignora eco de mensagens próprias
    if sender.endswith("@g.us") or "-" in sender:
        return None  # ignora grupos

    sender_name = raw.get("sender", {}).get("pushName") or raw.get("notifyName")

    # ID da mensagem (cobre diferentes formatos do webhook)
    message_id = (
        raw.get("id")
        or raw.get("key", {}).get("id")
        or raw.get("message", {}).get("key", {}).get("id")
    )

    timestamp = raw.get("timestamp") or raw.get("messageTimestamp")
    sent_at = datetime.fromtimestamp(int(timestamp)) if timestamp else None
    if sent_at:
        # Ignora histórico antigo (ex.: replay ao reconectar)
        if sent_at < datetime.utcnow() - timedelta(minutes=3):
            return None

    return IncomingMessage(
        message_id=message_id,
        sender_phone=sender,
        sender_name=sender_name,
        text=text,
        sent_at=sent_at,
    )
