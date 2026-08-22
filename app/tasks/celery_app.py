from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "clinica_chatbot",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.tasks.reminders"],
)

celery_app.conf.update(
    timezone=getattr(settings, "clinic_timezone", "America/Sao_Paulo"),
    enable_utc=True,
    imports=("app.tasks.reminders",),
    beat_schedule={
        "daily-d1-reminders": {
            "task": "app.tasks.reminders.send_day_before_reminders",
            "schedule": 3600.0,
        },
        "daily-post-consult-followups": {
            "task": "app.tasks.reminders.send_post_consult_followups",
            "schedule": 7200.0,
        },
        "daily-cancellation-followups": {
            "task": "app.tasks.reminders.send_cancellation_followups",
            "schedule": 7200.0,
        },
        "daily-reengagement-8am": {
            "task": "app.tasks.reminders.send_reengagement_followups",
            "schedule": crontab(hour=8, minute=0),
        },
    },
)