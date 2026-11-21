"""Celery application. The worker command is `celery -A app.tasks`."""

from __future__ import annotations

import os

from celery import Celery
from celery.schedules import crontab

celery_app = Celery("echodesk", broker=os.environ.get("REDIS_URL", "redis://redis:6379/0"))
celery_app.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_default_queue="dispatch",
    timezone="Asia/Shanghai",
    enable_utc=True,
    beat_schedule={
        "sweep-qualifications": {
            "task": "sweep_qualifications",
            "schedule": crontab(hour=0, minute=10),
        },
        "reconcile-vendor-bills": {
            "task": "billing_reconcile",
            "schedule": crontab(hour=2, minute=0),
        },
    },
    task_routes={
        "dispatch_campaign": {"queue": "dispatch"},
        "import_contacts": {"queue": "io_heavy"},
        "billing_reconcile": {"queue": "billing"},
        "render_report": {"queue": "report"},
        "sweep_qualifications": {"queue": "dispatch"},
    },
)
app = celery_app

from app.tasks import billing_schedule as billing_schedule_task  # noqa: E402,F401
from app.tasks import qualification as qualification_task  # noqa: E402,F401
