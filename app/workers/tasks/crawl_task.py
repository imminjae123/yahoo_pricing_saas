"""
app/workers/tasks/crawl_task.py — Celery tasks for price crawling.

Tasks
-----
crawl_competitor_task(competitor_product_id, tenant_id)
    Crawl one CompetitorProduct. Called individually or from the batch task.

crawl_all_competitors_task()
    Beat-scheduled hourly task. Fetches all active tenants and their
    competitor products, fans out individual crawl_competitor_task calls.

Design
------
- Tasks are async via asyncio.run() wrapper (Celery workers are sync by default).
- DB sessions are opened fresh per task invocation — no shared state between tasks.
- Each task uses bind=True so self.retry() is available.
- crawl_competitor_task retries up to 3 times with exponential backoff on
  transient errors (network, Yahoo API 429/5xx).
- crawl_all_competitors_task does NOT retry — each fan-out task retries individually.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from celery import Task
from celery.exceptions import MaxRetriesExceededError
from sqlalchemy import select

from app.dependencies.db import AsyncSessionLocal
from app.models.product import CompetitorProduct
from app.models.tenant import Tenant
from app.services.crawl_service import crawl_all_competitors, crawl_competitor
from app.services.yahoo import YahooAPIError
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


# ── Single competitor crawl task ──────────────────────────────────────────────

@celery_app.task(
    bind=True,
    name="app.workers.tasks.crawl_task.crawl_competitor_task",
    queue="crawl",
    max_retries=3,
    default_retry_delay=60,          # base delay seconds (doubled each retry)
    autoretry_for=(YahooAPIError,),  # auto-retry on Yahoo API errors
    retry_backoff=True,              # exponential: 60s, 120s, 240s
    retry_backoff_max=300,
    retry_jitter=True,
    acks_late=True,
)
def crawl_competitor_task(
    self: Task,
    competitor_product_id: str,
    tenant_id: str,
) -> dict[str, Any]:
    """
    Crawl a single CompetitorProduct and persist the result.

    Parameters
    ----------
    competitor_product_id : UUID string of the CompetitorProduct row.
    tenant_id             : UUID string of the owning Tenant.

    Returns
    -------
    Crawl result dict (price, is_all_time_low, line_queued, ...).
    """
    async def _run() -> dict[str, Any]:
        async with AsyncSessionLocal() as db:
            return await crawl_competitor(
                db,
                competitor_product_id=uuid.UUID(competitor_product_id),
                tenant_id=uuid.UUID(tenant_id),
            )

    try:
        result = asyncio.run(_run())
        logger.info(
            "crawl_competitor_task: done competitor=%s price=%s all_time_low=%s",
            competitor_product_id,
            result.get("price"),
            result.get("is_all_time_low"),
        )
        return result
    except YahooAPIError as exc:
        logger.warning(
            "crawl_competitor_task: YahooAPIError for %s (attempt %d/%d): %s",
            competitor_product_id,
            self.request.retries + 1,
            self.max_retries + 1,
            exc,
        )
        raise  # autoretry_for handles this
    except MaxRetriesExceededError:
        logger.error(
            "crawl_competitor_task: max retries exceeded for %s",
            competitor_product_id,
        )
        return {
            "competitor_product_id": competitor_product_id,
            "error": "max retries exceeded",
            "price": None,
            "is_all_time_low": False,
            "line_queued": False,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "crawl_competitor_task: unexpected error for %s: %s",
            competitor_product_id,
            exc,
        )
        try:
            raise self.retry(exc=exc, countdown=120)
        except MaxRetriesExceededError:
            return {
                "competitor_product_id": competitor_product_id,
                "error": str(exc),
                "price": None,
                "is_all_time_low": False,
                "line_queued": False,
            }


# ── Hourly batch crawl task (Beat entry point) ────────────────────────────────

@celery_app.task(
    bind=True,
    name="app.workers.tasks.crawl_task.crawl_all_competitors_task",
    queue="crawl",
    max_retries=0,   # fan-out task itself does not retry; child tasks do
    acks_late=True,
)
def crawl_all_competitors_task(self: Task) -> dict[str, Any]:
    """
    Beat-scheduled hourly task.

    Loads all active tenants, then fans out one crawl_competitor_task
    per active CompetitorProduct using Celery's apply_async (fire-and-forget).

    Returns a summary dict with total enqueued count.
    """
    async def _load_tenant_competitor_pairs() -> list[tuple[str, str]]:
        """Return [(competitor_product_id, tenant_id), ...] for all active pairs."""
        pairs: list[tuple[str, str]] = []
        async with AsyncSessionLocal() as db:
            # Load all active tenants
            tenants = (
                await db.scalars(
                    select(Tenant).where(Tenant.is_active.is_(True))
                )
            ).all()

            for tenant in tenants:
                # Load active competitors (no RLS needed here — migration_user)
                competitors = (
                    await db.scalars(
                        select(CompetitorProduct).where(
                            CompetitorProduct.is_active.is_(True)
                        )
                    )
                ).all()
                for comp in competitors:
                    pairs.append((str(comp.id), str(tenant.id)))
        return pairs

    pairs = asyncio.run(_load_tenant_competitor_pairs())
    enqueued = 0

    for competitor_product_id, tenant_id in pairs:
        crawl_competitor_task.apply_async(
            args=[competitor_product_id, tenant_id],
            queue="crawl",
            expires=3300,
        )
        enqueued += 1

    logger.info(
        "crawl_all_competitors_task: enqueued %d crawl tasks",
        enqueued,
    )
    return {"enqueued": enqueued, "pairs": len(pairs)}
