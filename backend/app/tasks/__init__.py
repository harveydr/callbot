"""Celery application. The worker command is `celery -A app.tasks`."""

from __future__ import annotations

import os

from celery import Celery

celery_app = Celery("echodesk", broker=os.environ.get("REDIS_URL", "redis://redis:6379/0"))
celery_app.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_default_queue="dispatch",
    task_routes={
        "dispatch_campaign": {"queue": "dispatch"},
        "import_contacts": {"queue": "io_heavy"},
        "billing_reconcile": {"queue": "billing"},
        "render_report": {"queue": "report"},
    },
)
app = celery_app
