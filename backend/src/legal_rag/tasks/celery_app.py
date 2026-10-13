from celery import Celery
from celery.schedules import crontab

from legal_rag.config import settings

config = settings()
celery = Celery("legal_rag", broker=config.redis_url.get_secret_value(), include=["legal_rag.tasks.jobs"])
celery.conf.update(
    timezone="Asia/Saigon",
    enable_utc=True,
    task_serializer="json",
    accept_content=["json"],
    task_ignore_result=True,
    broker_connection_retry_on_startup=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_routes={
        "legal_rag.tasks.answer_query": {"queue": "queries"},
        "legal_rag.tasks.ingest_source": {"queue": "ingestion"},
        "legal_rag.tasks.daily_update": {"queue": "ingestion"},
        "legal_rag.tasks.maintenance": {"queue": "queries"},
    },
    beat_schedule={
        "daily-official-update": {
            "task": "legal_rag.tasks.daily_update",
            "schedule": crontab(hour=2, minute=0),
        },
        "retention-and-timeouts": {"task": "legal_rag.tasks.maintenance", "schedule": 60.0},
    },
)
