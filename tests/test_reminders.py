from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import app.tasks.reminders as reminders


class FakeScalarResult:
    def __init__(self, value) -> None:
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeExecuteResult:
    def __init__(self, rows=None, scalar_value=None) -> None:
        self._rows = rows or []
        self._scalar_value = scalar_value

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._scalar_value


class FakeSession:
    def __init__(self, rows, reminder_exists=False) -> None:
        self.rows = rows
        self.reminder_exists = reminder_exists
        self.added = []
        self.committed = False
        self.execute_calls = 0

    async def execute(self, stmt):
        self.execute_calls += 1
        if self.execute_calls == 1:
            return FakeExecuteResult(rows=self.rows)
        return FakeExecuteResult(scalar_value=SimpleNamespace(id=1) if self.reminder_exists else None)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeWhatsAppClient:
    instances: list["FakeWhatsAppClient"] = []

    def __init__(self) -> None:
        self.sent = []
        FakeWhatsAppClient.instances.append(self)

    async def send_text(self, chat_id: str, text: str):
        self.sent.append((chat_id, text))


@pytest.mark.asyncio
async def test_send_day_before_reminders_uses_patient_phone(monkeypatch) -> None:
    appointment = SimpleNamespace(id=7, scheduled_at=datetime.utcnow() + timedelta(hours=24), doctor_name="Dra. Vitória")
    patient = SimpleNamespace(id=3, name="Maria Silva", phone="5562999999999")
    session = FakeSession(rows=[(appointment, patient)], reminder_exists=False)

    monkeypatch.setattr(reminders, "SessionLocal", lambda: FakeSessionContext(session))
    monkeypatch.setattr(reminders, "WhatsAppClient", FakeWhatsAppClient)
    monkeypatch.setattr(reminders, "ReminderAgent", SimpleNamespace(compose=lambda a, p: f"Lembrete para {p.phone}"))

    sent_count = await reminders._send_day_before_reminders()

    assert sent_count == 1
    assert FakeWhatsAppClient.instances[0].sent == [("5562999999999@c.us", "Lembrete para 5562999999999")]
    assert session.committed is True


@pytest.mark.asyncio
async def test_send_day_before_reminders_skips_duplicate(monkeypatch) -> None:
    appointment = SimpleNamespace(id=8, scheduled_at=datetime.utcnow() + timedelta(hours=24), doctor_name="Dra. Vitória")
    patient = SimpleNamespace(id=4, name="Maria Silva", phone="5562888888888")
    session = FakeSession(rows=[(appointment, patient)], reminder_exists=True)

    monkeypatch.setattr(reminders, "SessionLocal", lambda: FakeSessionContext(session))
    monkeypatch.setattr(reminders, "WhatsAppClient", FakeWhatsAppClient)
    monkeypatch.setattr(reminders, "ReminderAgent", SimpleNamespace(compose=lambda a, p: "duplicado"))

    sent_count = await reminders._send_day_before_reminders()

    assert sent_count == 0
    assert FakeWhatsAppClient.instances[-1].sent == []
