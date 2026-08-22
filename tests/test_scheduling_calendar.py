from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.agents.scheduling import DRA_VITORIA, SchedulingAgent


def _next_weekday_at(*, weekday: int, hour: int, minute: int, tz_name: str = "America/Sao_Paulo") -> datetime:
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz=tz).replace(tzinfo=None)
    days_ahead = (weekday - now.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    target = (now + timedelta(days=days_ahead)).date()
    return datetime(target.year, target.month, target.day, hour, minute)


class DummySession:
    def __init__(self) -> None:
        self.committed = False

    async def commit(self) -> None:
        self.committed = True

    async def refresh(self, obj) -> None:
        return None

    async def flush(self) -> None:
        return None


class FakeAppointmentRepository:
    def __init__(self) -> None:
        self.created_payload = None
        self.active_appointment = None

    async def create(self, **kwargs):
        self.created_payload = kwargs
        return SimpleNamespace(id=10, google_event_id=None)

    async def get_by_scheduled_at(self, *args, **kwargs):
        return None

    async def get_active_for_patient(self, *args, **kwargs):
        return self.active_appointment

    async def list_active_for_patient(self, *args, **kwargs):
        return []

    async def list_active_by_patient_name(self, *args, **kwargs):
        return []


class FakeCalendarService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.deleted: list[str] = []

    def is_configured(self) -> bool:
        return True

    def is_slot_available(self, *, scheduled_at, duration_minutes, exclude_event_id=None):
        return True

    def create_event(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(event_id="evt-abc", status="created", detail=None)

    def update_event(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(event_id="evt-abc", status="updated", detail=None)

    def delete_event(self, *, event_id: str):
        self.deleted.append(event_id)
        return True

    def list_future_events_by_patient_name(self, *, patient_name: str, limit: int = 5):
        return []


@pytest.mark.asyncio
async def test_schedule_creates_google_calendar_event_after_profile_data(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.agents.scheduling.settings.clinic_timezone",
        "America/Sao_Paulo",
    )

    repository = FakeAppointmentRepository()
    calendar = FakeCalendarService()
    agent = SchedulingAgent(repository=repository, calendar=calendar)

    session_key = "5562999999999@c.us"
    agent.memory.set_state(
        session_key,
        {
            "patient_name": "Maria Silva",
            "insurance_name": "Unimed",
            "date_of_birth": "07/04/1990",
        },
    )

    scheduled_at = _next_weekday_at(weekday=1, hour=15, minute=30)
    context = SimpleNamespace(
        session=DummySession(),
        patient=SimpleNamespace(id=1, phone="5562999999999", name="Maria Silva"),
        incoming_text=f"Dra. Vitória dia {scheduled_at.strftime('%d/%m/%Y')} às {scheduled_at.strftime('%Hh%M')}",
        history=[],
        conversation_metadata={"session_key": session_key},
    )

    result = await agent.handle(context)

    assert result.intent == "scheduling"
    assert "Consulta agendada" in result.reply_text
    assert calendar.calls, "Expected Google Calendar create_event to be called"
    created = calendar.calls[0]
    assert created["doctor_name"] == DRA_VITORIA.canonical_name
    assert created["patient_name"] == "Maria Silva"
    assert created["duration_minutes"] == 30
    assert repository.created_payload is not None
    assert repository.created_payload["doctor_name"] == DRA_VITORIA.canonical_name


@pytest.mark.asyncio
async def test_schedule_prompts_for_profile_fields_before_booking(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.agents.scheduling.settings.clinic_timezone",
        "America/Sao_Paulo",
    )

    agent = SchedulingAgent(repository=FakeAppointmentRepository(), calendar=FakeCalendarService())
    context = SimpleNamespace(
        session=DummySession(),
        patient=SimpleNamespace(id=1, phone="5562999999999", name=None),
        incoming_text="Quero agendar consulta com Dra. Vitória amanhã às 15h30",
        history=[],
        conversation_metadata={"session_key": "5562999999999@c.us"},
    )

    result = await agent.handle(context)

    assert result.intent == "scheduling"
    assert "nome completo" in result.reply_text
    assert "convênio" in result.reply_text
    assert "data de nascimento" in result.reply_text


@pytest.mark.asyncio
async def test_confirmation_uses_pending_schedule_even_with_missing_profile_data(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.agents.scheduling.settings.clinic_timezone",
        "America/Sao_Paulo",
    )

    repository = FakeAppointmentRepository()
    calendar = FakeCalendarService()
    agent = SchedulingAgent(repository=repository, calendar=calendar)

    session_key = "5562999999999@c.us"
    scheduled_at = _next_weekday_at(weekday=1, hour=15, minute=30)
    agent.memory.set_state(
        session_key,
        {
            "pending_action": "confirm_schedule",
            "pending_doctor_name": "Dra. Vitória",
            "pending_scheduled_at": scheduled_at.isoformat(),
            "pending_notes": "Consulta sugerida",
            "patient_name": "Maria Silva",
            "insurance_name": "Unimed",
            "date_of_birth": "07/04/1990",
        },
    )

    context = SimpleNamespace(
        session=DummySession(),
        patient=SimpleNamespace(id=1, phone="5562999999999", name="Maria Silva"),
        incoming_text="Sim",
        history=[],
        conversation_metadata={"session_key": session_key},
    )

    result = await agent.handle(context)

    assert result.intent == "scheduling"
    assert "Consulta agendada" in result.reply_text
    assert calendar.calls, "Expected Google Calendar create_event to be called on confirmation"


@pytest.mark.asyncio
async def test_cancel_removes_google_calendar_event(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.agents.scheduling.settings.clinic_timezone",
        "America/Sao_Paulo",
    )

    repository = FakeAppointmentRepository()
    repository.active_appointment = SimpleNamespace(
        id=11,
        patient_id=1,
        scheduled_at=datetime(2026, 4, 7, 15, 30),
        doctor_name="Dra. Vitória",
        specialty="Clínica geral",
        status="scheduled",
        notes="Agendada",
        google_event_id="evt-xyz",
    )
    calendar = FakeCalendarService()
    agent = SchedulingAgent(repository=repository, calendar=calendar)

    context = SimpleNamespace(
        session=DummySession(),
        patient=SimpleNamespace(id=1, phone="5562999999999", name="Maria Silva"),
        incoming_text="Quero cancelar minha consulta",
        history=[],
        conversation_metadata={"session_key": "5562999999999@c.us"},
    )

    result = await agent.handle(context)

    assert result.intent == "scheduling"
    assert "desmarcada" in result.reply_text.lower()
    assert calendar.deleted == ["evt-xyz"]


@pytest.mark.asyncio
async def test_profile_payload_with_commas_is_parsed(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.agents.scheduling.settings.clinic_timezone",
        "America/Sao_Paulo",
    )

    agent = SchedulingAgent(repository=FakeAppointmentRepository(), calendar=FakeCalendarService())
    session_key = "5562999990000@c.us"
    context = SimpleNamespace(
        session=DummySession(),
        patient=SimpleNamespace(id=1, phone="5562999990000", name=None),
        incoming_text="Gustavo B R Silva, Bradesco, 26/12/2000",
        history=[],
        conversation_metadata={"session_key": session_key},
    )

    result = await agent.handle(context)

    assert result.intent == "scheduling"
    assert "Para agendar" in result.reply_text
    state = agent.memory.get_state(session_key)
    assert state.get("patient_name") == "Gustavo B R Silva"
    assert state.get("insurance_name") == "Bradesco"
    assert state.get("date_of_birth") == "26/12/2000"
