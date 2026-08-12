"""
app/workers/celery_app.py — Celery application factory.

Broker  : Redis DB-1  (task queue)
Backend : Redis DB-1  (result store)
DB-0    : Yahoo! API rate-limit token bucket (managed separately)
DB-2    : tenant metadata cache              (managed separately)
"""

from __future__ import annotations

from celery import Celery

from app.config import settings

# ── Derive Redis URLs per DB index ────────────────────────────────────────────
def _redis_url(db: int) -> str:
    base = settings.redis_url.rsplit("/", 1)[0]
    return f"{base}/{db}"


_BROKER_URL  = _redis_url(1)   # DB-1: task queue
_BACKEND_URL = _redis_url(1)   # DB-1: result store (same DB is fine for dev)

# ── Celery instance ───────────────────────────────────────────────────────────
celery_app = Celery(
    "yahoo_pricing_saas",
    broker=_BROKER_URL,
    backend=_BACKEND_URL,
    include=[
        "app.workers.tasks.crawl_task",
        "app.workers.tasks.notify_task",
    ],
)

celery_app.conf.update(
    # Serialisation
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Timezone
    timezone="Asia/Tokyo",
    enable_utc=True,
    # Reliability
    task_acks_late=True,            # ack only after task completes
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,   # one task at a time per worker process
    # Result expiry (keep for 1 day for debugging)
    result_expires=86400,
    # Beat schedule is defined in beat_schedule.py
    beat_schedule_filename="celerybeat-schedule",
)

# Import beat schedule (sets celery_app.conf.beat_schedule)
from app.workers import beat_schedule as _bs  # noqa: E402, F401
