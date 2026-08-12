"""
app/workers/beat_schedule.py — Celery Beat periodic task definitions.

Schedule
--------
crawl_all_competitors_hourly
    Every 60 minutes — crawls all active CompetitorProducts for all tenants.

Extending
---------
Add new entries to BEAT_SCHEDULE and they will be picked up by Celery Beat
on next restart.
"""

from __future__ import annotations

from celery.schedules import crontab

from app.workers.celery_app import celery_app

# ── Beat schedule ─────────────────────────────────────────────────────────────
BEAT_SCHEDULE: dict = {
    # ── Hourly price crawl ────────────────────────────────────────────────────
    "crawl_all_competitors_hourly": {
        "task": "app.workers.tasks.crawl_task.crawl_all_competitors_task",
        "schedule": crontab(minute=0),   # every hour at :00
        "options": {
            "queue": "crawl",
            "expires": 3300,             # expire if not started within 55 min
        },
    },
}

celery_app.conf.beat_schedule = BEAT_SCHEDULE
celery_app.conf.task_default_queue = "default"
