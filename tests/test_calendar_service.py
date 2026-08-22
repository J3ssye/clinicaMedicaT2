from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from app.core.config import get_settings
from app.integrations.calendar import CalendarService


class FakeEventsResource:
    def __init__(self) -> None:
        self.insert_calls: list[dict[str, object]] = []
        self.list_calls: list[dict[str, object]] = []
        self.update_calls: list[dict[str, object]] = []
        self.delete_calls: list[dict[str, object]] = []
        self.next_insert_result = {"id": "evt-123"}
        self.next_list_result = {"items": []}

    def insert(self, *, calendarId: str, body: dict[str, object]):
        self.insert_calls.append({"calendarId": calendarId, "body": body})
        return SimpleNamespace(execute=lambda: self.next_insert_result)

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        return SimpleNamespace(execute=lambda: self.next_list_result)

    def update(self, *, calendarId: str, eventId: str, body: dict[str, object]):
        self.update_calls.append({"calendarId": calendarId, "eventId": eventId, "body": body})
        return SimpleNamespace(execute=lambda: {"id": eventId})

    def delete(self, *, calendarId: str, eventId: str):
        self.delete_calls.append({"calendarId": calendarId, "eventId": eventId})
        return SimpleNamespace(execute=lambda: None)


class FakeCalendarServiceClient:
    def __init__(self) -> None:
        self.events_resource = FakeEventsResource()

    def events(self):
        return self.events_resource


@pytest.fixture()
def calendar_service(monkeypatch) -> CalendarService:
    monkeypatch.setattr(get_settings(), "google_service_account_json", '{"type":"service_account"}')
    monkeypatch.setattr(get_settings(), "google_calendar_id", "calendar-id")
    monkeypatch.setattr(get_settings(), "clinic_address", "Rua 9A, 160")
    monkeypatch.setattr(get_settings(), "clinic_name", "Centro Médico Valéria Frota")
    service = CalendarService()
    client = FakeCalendarServiceClient()
    service._service = client
    service.enabled = True
    return service


def test_create_event_builds_expected_payload(calendar_service: CalendarService) -> None:
    result = calendar_service.create_event(
        patient_name="Maria Silva",
        patient_phone="5562999999999",
        doctor_name="Dra. Vitória",
        scheduled_at=datetime(2026, 4, 7, 15, 30),
        specialty="Clínica geral",
        notes="Primeira consulta",
        duration_minutes=30,
    )

    assert result.status == "created"
    assert result.event_id == "evt-123"
    call = calendar_service._service.events_resource.insert_calls[0]
    body = call["body"]
    assert call["calendarId"] == "calendar-id"
    assert body["summary"] == "Consulta - Maria Silva - Dra. Vitória"
    assert body["location"] == "Rua 9A, 160"
    assert "Paciente: Maria Silva" in body["description"]
    assert "Médico: Dra. Vitória" in body["description"]
    assert body["start"]["timeZone"] == "America/Sao_Paulo"
    assert body["end"]["timeZone"] == "America/Sao_Paulo"


def test_is_slot_available_uses_calendar_window(calendar_service: CalendarService) -> None:
    available = calendar_service.is_slot_available(
        scheduled_at=datetime(2026, 4, 7, 15, 30),
        duration_minutes=30,
    )

    assert available is True
    call = calendar_service._service.events_resource.list_calls[0]
    assert call["calendarId"] == "calendar-id"
    assert call["singleEvents"] is True
    assert call["orderBy"] == "startTime"


def test_delete_event_calls_google_calendar(calendar_service: CalendarService) -> None:
    assert calendar_service.delete_event(event_id="evt-999") is True
    call = calendar_service._service.events_resource.delete_calls[0]
    assert call == {"calendarId": "calendar-id", "eventId": "evt-999"}


def test_create_event_returns_unavailable_when_service_fails(monkeypatch) -> None:
    service = CalendarService()
    service.enabled = True

    class BrokenClient:
        def events(self):
            raise RuntimeError("boom")

    service._service = BrokenClient()
    monkeypatch.setattr(get_settings(), "google_service_account_json", '{"type":"service_account"}')
    monkeypatch.setattr(get_settings(), "google_calendar_id", "calendar-id")

    result = service.create_event(
        patient_name="Maria Silva",
        patient_phone="5562999999999",
        doctor_name="Dra. Vitória",
        scheduled_at=datetime(2026, 4, 7, 15, 30),
        specialty="Clínica geral",
        notes="Primeira consulta",
        duration_minutes=30,
    )

    assert result.status in {"insert_failed", "calendar_unavailable"}


def test_update_event_calls_google_calendar(calendar_service: CalendarService) -> None:
    result = calendar_service.update_event(
        event_id="evt-321",
        patient_name="Maria Silva",
        patient_phone="5562999999999",
        doctor_name="Dra. Vitória",
        scheduled_at=datetime(2026, 4, 7, 15, 30),
        specialty="Clínica geral",
        notes="Remarcação",
        duration_minutes=30,
    )

    assert result.status == "updated"
    call = calendar_service._service.events_resource.update_calls[0]
    assert call["calendarId"] == "calendar-id"
    assert call["eventId"] == "evt-321"
    assert call["body"]["summary"] == "Consulta - Maria Silva - Dra. Vitória"
