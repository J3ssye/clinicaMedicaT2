from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.schemas.events import IncomingMessage
from app.use_cases.handle_incoming_webhook import HandleIncomingWebhookUseCase


class DummySession:
    def __init__(self) -> None:
        self.committed = False

    async def commit(self) -> None:
        self.committed = True

    async def flush(self) -> None:
        return None


class FakePatientRepository:
    async def get_or_create_by_phone(self, session, *, phone: str, name: str | None = None):
        return SimpleNamespace(id=1, phone=phone, name=name)


class FakeMessageRepository:
    async def inbound_exists(self, session, external_id):
        return False

    async def inbound_recent_duplicate(self, session, *, patient_id, content, window_seconds=180):
        return False

    async def outbound_recent_duplicate(self, session, *, patient_id, content, window_seconds=30):
        return False

    async def log_message(
        self,
        session,
        *,
        patient_id,
        direction,
        content,
        external_id=None,
        intent=None,
        channel="whatsapp",
        commit=True,
    ):
        return SimpleNamespace(id=1)


class FakeOrchestrator:
    async def run(self, **kwargs):
        return SimpleNamespace(reply_text="Resposta pronta", intent="faq", llm_used=True)


class FailingWhatsAppClient:
    def __init__(self) -> None:
        self.seen_calls: list[str] = []
        self.text_calls: list[tuple[str, str, str | None]] = []

    async def send_seen(self, chat_id: str):
        self.seen_calls.append(chat_id)

    async def send_text(self, chat_id: str, text: str, *, reply_to: str | None = None):
        self.text_calls.append((chat_id, text, reply_to))
        raise RuntimeError("WAHA indisponivel")


@pytest.mark.asyncio
async def test_execute_keeps_processing_when_waha_reply_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.use_cases.handle_incoming_webhook.settings.waha_mark_as_seen_before_reply",
        True,
    )

    use_case = HandleIncomingWebhookUseCase(
        session=DummySession(),
        message_repository=FakeMessageRepository(),
        patient_repository=FakePatientRepository(),
        orchestrator=FakeOrchestrator(),
        whatsapp_client=FailingWhatsAppClient(),
    )

    result = await use_case.execute(
        IncomingMessage(
            message_id="msg-1",
            sender_phone="5562999999999",
            sender_chat_id="5562999999999@c.us",
            sender_name="Paciente",
            text="Oi",
        )
    )

    assert result["status"] == "processed"
    assert result["deduplicated"] is False
    assert result["llm_used"] is True
