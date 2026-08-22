from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.core.config import get_settings


settings = get_settings()
SCOPES = ["https://www.googleapis.com/auth/calendar"]
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CalendarCreateResult:
    event_id: str | None
    status: str
    detail: str | None = None


@dataclass(slots=True)
class CalendarEventSummary:
    event_id: str | None
    patient_name: str | None
    doctor_name: str | None
    scheduled_at: datetime
    summary: str | None = None


class CalendarService:
    def __init__(self) -> None:
        self.enabled = bool(settings.google_service_account_json and settings.google_calendar_id)
        self._service = None
        self._timezone = ZoneInfo(settings.clinic_timezone)

    def _get_service(self):
        if not self.enabled:
            logger.warning("google_calendar_not_enabled")
            return None
        if self._service is None:
            try:
                info = json.loads(settings.google_service_account_json or "{}")
                credentials = service_account.Credentials.from_service_account_info(
                    info,
                    scopes=SCOPES,
                )
                self._service = build(
                    "calendar",
                    "v3",
                    credentials=credentials,
                    cache_discovery=False,
                )
            except Exception:
                logger.exception("google_calendar_service_init_failed")
                return None
        return self._service

    def is_configured(self) -> bool:
        return self._get_service() is not None

    def is_slot_available(
        self,
        *,
        scheduled_at: datetime,
        duration_minutes: int,
        exclude_event_id: str | None = None,
    ) -> bool:
        service = self._get_service()
        if service is None:
            return True

        start_dt, end_dt = self._build_window(scheduled_at, duration_minutes)
        try:
            response = (
                service.events()
                .list(
                    calendarId=settings.google_calendar_id,
                    timeMin=start_dt.isoformat(),
                    timeMax=end_dt.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
        except HttpError:
            logger.exception("google_calendar_list_failed")
            return True

        for item in response.get("items", []):
            if item.get("status") == "cancelled":
                continue
            if exclude_event_id and item.get("id") == exclude_event_id:
                continue
            return False
        return True

    def create_event(
        self,
        *,
        patient_name: str,
        doctor_name: str,
        scheduled_at: datetime,
        specialty: str | None,
        notes: str | None,
        duration_minutes: int | None = None,
        patient_phone: str | None = None,
    ) -> CalendarCreateResult:
        service = self._get_service()
        if service is None:
            return CalendarCreateResult(
                event_id=None,
                status="calendar_unavailable",
                detail="Serviço do Google Calendar indisponível ou não configurado.",
            )

        start_dt, end_dt = self._build_window(
            scheduled_at,
            duration_minutes or settings.default_appointment_duration_minutes,
        )
        event = self._build_event_body(
            patient_name=patient_name,
            patient_phone=patient_phone,
            doctor_name=doctor_name,
            specialty=specialty,
            notes=notes,
            start_dt=start_dt,
            end_dt=end_dt,
        )
        try:
            created = (
                service.events()
                .insert(calendarId=settings.google_calendar_id, body=event)
                .execute()
            )
        except HttpError as exc:
            reason = getattr(exc, "reason", None) or str(exc)
            logger.exception(
                "google_calendar_insert_failed",
                extra={
                    "calendar_id": settings.google_calendar_id,
                    "doctor_name": doctor_name,
                    "scheduled_at": start_dt.isoformat(),
                    "reason": reason,
                },
            )
            return CalendarCreateResult(
                event_id=None,
                status="insert_failed",
                detail=str(reason),
            )
        logger.info(
            "google_calendar_event_created",
            extra={
                "calendar_id": settings.google_calendar_id,
                "doctor_name": doctor_name,
                "scheduled_at": start_dt.isoformat(),
                "event_id": created.get("id"),
            },
        )
        return CalendarCreateResult(event_id=created.get("id"), status="created")

    def update_event(
        self,
        *,
        event_id: str,
        patient_name: str,
        doctor_name: str,
        scheduled_at: datetime,
        specialty: str | None,
        notes: str | None,
        duration_minutes: int | None = None,
        patient_phone: str | None = None,
    ) -> CalendarCreateResult:
        service = self._get_service()
        if service is None:
            return CalendarCreateResult(event_id=None, status="calendar_unavailable", detail="Serviço do Google Calendar indisponível ou não configurado.")
        start_dt, end_dt = self._build_window(
            scheduled_at,
            duration_minutes or settings.default_appointment_duration_minutes,
        )
        event = self._build_event_body(
            patient_name=patient_name,
            patient_phone=patient_phone,
            doctor_name=doctor_name,
            specialty=specialty,
            notes=notes,
            start_dt=start_dt,
            end_dt=end_dt,
        )
        try:
            updated = (
                service.events()
                .update(calendarId=settings.google_calendar_id, eventId=event_id, body=event)
                .execute()
            )
        except HttpError as exc:
            reason = getattr(exc, "reason", None) or str(exc)
            logger.exception(
                "google_calendar_update_failed",
                extra={
                    "calendar_id": settings.google_calendar_id,
                    "doctor_name": doctor_name,
                    "scheduled_at": start_dt.isoformat(),
                    "reason": reason,
                    "event_id": event_id,
                },
            )
            return CalendarCreateResult(event_id=event_id, status="update_failed", detail=str(reason))
        return CalendarCreateResult(event_id=updated.get("id") or event_id, status="updated")

    def delete_event(self, *, event_id: str) -> bool:
        service = self._get_service()
        if service is None:
            return False
        try:
            service.events().delete(calendarId=settings.google_calendar_id, eventId=event_id).execute()
            return True
        except HttpError:
            logger.exception("google_calendar_delete_failed", extra={"event_id": event_id})
            return False

    def list_future_events_by_patient_name(self, *, patient_name: str, limit: int = 5) -> list[CalendarEventSummary]:
        service = self._get_service()
        normalized = (patient_name or "").strip()
        if service is None or not normalized:
            return []
        now = datetime.now(tz=self._timezone)
        try:
            response = (
                service.events()
                .list(
                    calendarId=settings.google_calendar_id,
                    timeMin=now.isoformat(),
                    maxResults=limit * 3,
                    singleEvents=True,
                    orderBy="startTime",
                    q=normalized,
                )
                .execute()
            )
        except HttpError:
            logger.exception("google_calendar_search_by_patient_failed", extra={"patient_name": normalized})
            return []

        results: list[CalendarEventSummary] = []
        normalized_lower = normalized.lower()
        for item in response.get("items", []):
            if item.get("status") == "cancelled":
                continue
            haystack = " ".join(
                str(part or "")
                for part in (
                    item.get("summary"),
                    item.get("description"),
                )
            ).lower()
            if normalized_lower not in haystack:
                continue
            start_raw = ((item.get("start") or {}).get("dateTime") or (item.get("start") or {}).get("date"))
            if not start_raw:
                continue
            try:
                scheduled_at = datetime.fromisoformat(start_raw.replace("Z", "+00:00")).astimezone(self._timezone)
            except ValueError:
                continue
            results.append(
                CalendarEventSummary(
                    event_id=item.get("id"),
                    patient_name=self._extract_label_value(item.get("description"), "Paciente"),
                    doctor_name=self._extract_label_value(item.get("description"), "Médico"),
                    scheduled_at=scheduled_at,
                    summary=item.get("summary"),
                )
            )
            if len(results) >= limit:
                break
        return results

    def _build_event_body(
        self,
        *,
        patient_name: str,
        patient_phone: str | None,
        doctor_name: str,
        specialty: str | None,
        notes: str | None,
        start_dt: datetime,
        end_dt: datetime,
    ) -> dict[str, object]:
        return {
            "summary": f"Consulta - {patient_name or 'Paciente'} - {doctor_name}",
            "location": settings.clinic_address,
            "description": (
                f"Clínica: {settings.clinic_name}\n"
                f"Paciente: {patient_name or '-'}\n"
                f"Telefone: {patient_phone or '-'}\n"
                f"Médico: {doctor_name}\n"
                f"Especialidade: {specialty or '-'}\n"
                f"Observações: {notes or '-'}"
            ),
            "start": {
                "dateTime": start_dt.isoformat(),
                "timeZone": settings.clinic_timezone,
            },
            "end": {
                "dateTime": end_dt.isoformat(),
                "timeZone": settings.clinic_timezone,
            },
            "extendedProperties": {
                "private": {
                    "source": "clinica-chatbot",
                    "doctor_name": doctor_name,
                    "patient_phone": patient_phone or "",
                }
            },
        }

    @staticmethod
    def _extract_label_value(description: str | None, label: str) -> str | None:
        if not description:
            return None
        prefix = f"{label}:"
        for line in description.splitlines():
            if line.startswith(prefix):
                value = line.split(":", 1)[1].strip()
                return value or None
        return None

    def _build_window(self, scheduled_at: datetime, duration_minutes: int) -> tuple[datetime, datetime]:
        start_dt = scheduled_at
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=self._timezone)
        else:
            start_dt = start_dt.astimezone(self._timezone)
        end_dt = start_dt + timedelta(minutes=duration_minutes)
        return start_dt, end_dt
