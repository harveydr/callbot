"""Celery application. The worker command is `celery -A app.tasks`."""

from __future__ import annotations

import os
from datetime import timedelta

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
        "dispatch-due-campaigns": {
            "task": "dispatch_due_campaigns",
            "schedule": timedelta(minutes=1),
        },
        "probe-missing-cdr": {
            "task": "probe_missing_cdr",
            "schedule": timedelta(minutes=1),
        },
    },
    task_routes={
        "dispatch_campaign": {"queue": "dispatch"},
        "dispatch_due_campaigns": {"queue": "dispatch"},
        "import_contacts": {"queue": "io_heavy"},
        "billing_reconcile": {"queue": "billing"},
        "probe_missing_cdr": {"queue": "billing"},
        "render_report": {"queue": "report"},
        "sweep_qualifications": {"queue": "dispatch"},
    },
    task_annotations={
        "dispatch_campaign": {"time_limit": 60},
        "dispatch_due_campaigns": {"time_limit": 60},
        "sweep_qualifications": {"time_limit": 60},
        "import_contacts": {"time_limit": 600},
        "billing_reconcile": {"time_limit": 120},
        "probe_missing_cdr": {"time_limit": 120},
        "render_report": {"time_limit": 300},
    },
)
app = celery_app

from app.tasks import billing_schedule as billing_schedule_task  # noqa: E402,F401
from app.tasks import dispatch as dispatch_task  # noqa: E402,F401
from app.tasks import qualification as qualification_task  # noqa: E402,F401
