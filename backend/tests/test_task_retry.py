"""A failed worker task waits 1s, 2s, then 4s, and stops after the third retry."""

from __future__ import annotations

from app.tasks import celery_app, durable_task

_NAMES = (
    "dispatch_campaign",
    "dispatch_due_campaigns",
    "import_contacts",
    "render_report",
    "billing_reconcile",
    "probe_missing_cdr",
    "sweep_qualifications",
    "ensure_audit_partitions",
)


def test_worker_tasks_retry_three_times_with_backoff() -> None:
    for name in _NAMES:
        task = celery_app.tasks[name]
        assert task.max_retries == 3
        assert task.retry_backoff is True
        assert task.retry_backoff_max == 60
        assert task.retry_jitter is False
        assert task.autoretry_for == (Exception,)


def test_the_third_retry_is_the_last_attempt() -> None:
    attempts = {"n": 0}
    countdowns: list[object] = []

    @durable_task(name="echodesk.retry.probe")
    def probe() -> str:
        attempts["n"] += 1
        if attempts["n"] < 4:
            raise RuntimeError("not yet")
        return "ready"

    original = probe.retry

    def spy(*args: object, **kwargs: object) -> object:
        countdowns.append(kwargs.get("countdown"))
        return original(*args, **kwargs)

    probe.retry = spy  # type: ignore[method-assign]
    previous = celery_app.conf.task_eager_propagates
    celery_app.conf.task_eager_propagates = False
    try:
        result = probe.apply()
        assert result.result == "ready"
        assert attempts["n"] == 4
        assert countdowns == [1, 2, 4]
    finally:
        celery_app.conf.task_eager_propagates = previous
        celery_app.tasks.pop("echodesk.retry.probe", None)
