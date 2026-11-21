"""Queue entry points. The HTTP service performs the same idempotent work inline unless CELERY_ENQUEUE=true."""

from __future__ import annotations

from app.tasks import celery_app


@celery_app.task(name="dispatch_campaign")
def dispatch_campaign(campaign_id: str, tenant_id: str) -> str:
    return f"queued:{tenant_id}:{campaign_id}"
