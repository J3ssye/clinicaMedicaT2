import json
from datetime import datetime

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

    patient = await PatientService.get_or_create_by_phone(
        session, phone=message.sender_phone, name=message.sender_name
    )
    await MessageService.log_message(
        session,
        patient_id=patient.id,
        direction="inbound",
        content=message.text,
        external_id=message.message_id,
    )

    result = await orchestrator.run(session=session, patient=patient, message=message.text)
    await MessageService.log_message(
        session,
        patient_id=patient.id,
        direction="outbound",
        content=result.reply_text,
        intent=result.intent,
    )
    await waha_client.send_text(chat_id=message.sender_phone, text=result.reply_text)
    return {"status": "processed", "intent": result.intent}


def _extract_incoming_message(payload: WahaWebhookPayload) -> IncomingMessage | None:
    if payload.event not in {"message", "message.any"}:
        return None
    raw = payload.payload
    text = (
        raw.get("body")
        or raw.get("text")
        or raw.get("message", {}).get("conversation")
        or raw.get("message", {}).get("extendedTextMessage", {}).get("text")
    )
    if not text:
        return None
    sender = raw.get("from") or raw.get("sender", {}).get("id") or ""
    sender_name = raw.get("sender", {}).get("pushName") or raw.get("notifyName")
    timestamp = raw.get("timestamp")
    sent_at = datetime.fromtimestamp(int(timestamp)) if timestamp else None
    return IncomingMessage(
        message_id=raw.get("id"),
        sender_phone=sender,
        sender_name=sender_name,
        text=text,
        sent_at=sent_at,
    )
