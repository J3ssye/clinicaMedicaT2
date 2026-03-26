from celery import Celery

from app.core.config import get_settings


settings = get_settings()

celery_app = Celery(
    "clinica_chatbot",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
celery_app.conf.timezone = settings.clinic_timezone
celery_app.conf.beat_schedule = {
    "daily-d1-reminders": {
        "task": "app.tasks.reminders.send_day_before_reminders",
        "schedule": 3600.0,
    }
}
celery_app.autodiscover_tasks(["app.tasks"])
