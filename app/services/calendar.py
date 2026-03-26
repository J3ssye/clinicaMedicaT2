from __future__ import annotations

import json
from datetime import datetime, timedelta

from google.oauth2 import service_account
from googleapiclient.discovery import build

from app.core.config import get_settings


settings = get_settings()
SCOPES = ["https://www.googleapis.com/auth/calendar"]


class GoogleCalendarService:
    def __init__(self) -> None:
        self.enabled = bool(settings.google_service_account_json)
        self._service = None

    def _get_service(self):
        if not self.enabled:
            return None
        if self._service is None:
            info = json.loads(settings.google_service_account_json)
            credentials = service_account.Credentials.from_service_account_info(
                info, scopes=SCOPES
            )
            self._service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
        return self._service

    def create_event(
        self,
        patient_name: str,
        doctor_name: str,
        scheduled_at: datetime,
        specialty: str | None,
        notes: str | None,
    ) -> str | None:
        service = self._get_service()
        if service is None:
            return None

        event = {
            "summary": f"Consulta - {patient_name or 'Paciente'}",
            "description": f"Medico: {doctor_name}\nEspecialidade: {specialty or '-'}\n{notes or ''}",
            "start": {"dateTime": scheduled_at.isoformat(), "timeZone": settings.clinic_timezone},
            "end": {
                "dateTime": (scheduled_at + timedelta(minutes=30)).isoformat(),
                "timeZone": settings.clinic_timezone,
            },
        }
        created = (
            service.events()
            .insert(calendarId=settings.google_calendar_id, body=event)
            .execute()
        )
        return created.get("id")
