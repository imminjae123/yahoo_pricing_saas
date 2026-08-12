"""
app/workers/tasks/notify_task.py — Celery task for LINE Messaging API notifications.

Task
----
send_line_notification_task(payload: dict)
    Reads a LINE notification payload (same schema as crawl_service._enqueue_line_notification),
    looks up the tenant's LINE User ID, and sends a push message via LINE Messaging API v3.

Retry policy
------------
- Max 5 retries.
- Exponential backoff: 30s, 60s, 120s, 240s, 480s (capped at 480s).
- Retries on LINE API 429 (rate-limited) and 5xx errors.
- Permanent failures (400, 403) are not retried.

Redis consumer
--------------
This task is triggered two ways:
  1. Directly via crawl_service._enqueue_line_notification → RPUSH line_notifications
  2. A companion consumer task (consume_line_queue_task) polls Redis DB-1
     and calls this task for each payload.

LINE API v3 SDK usage
---------------------
    from linebot.v3.messaging import ApiClient, Configuration, MessagingApi
    from linebot.v3.messaging.models import PushMessageRequest, TextMessage

Payload schema (from crawl_service)
-------------------------------------
{
    "task": "send_line_notification",
    "tenant_id": "<uuid>",
    "competitor_product_id": "<uuid>",
    "yahoo_item_code": "<str>",
    "item_name": "<str|null>",
    "previous_low": <int|null>,
    "new_low": <int>,
    "price_history_id": "<uuid>"
}
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

import redis.asyncio as aioredis
from celery import Task
from celery.exceptions import MaxRetriesExceededError
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    PushMessageRequest,
    TextMessage,
)
from sqlalchemy import select

from app.config import settings
from app.dependencies.db import AsyncSessionLocal
from app.models.tenant import Tenant
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

_LINE_QUEUE_KEY = "line_notifications"
_REDIS_LINE_DB  = 1

# ── LINE client factory ───────────────────────────────────────────────────────

def _line_api() -> MessagingApi:
    """Return a LINE Messaging API v3 client."""
    config = Configuration(access_token=settings.line_channel_access_token)
    return MessagingApi(ApiClient(config))


# ── LINE message builder ──────────────────────────────────────────────────────

def _build_message(payload: dict[str, Any]) -> str:
    """Build the push message text from a notification payload."""
    item_name   = payload.get("item_name") or payload.get("yahoo_item_code", "不明")
    new_low     = payload.get("new_low")
    previous    = payload.get("previous_low")

    if previous is not None:
        diff = previous - new_low
        return (
            f"🔔 過去最安値を更新！\n"
            f"商品: {item_name}\n"
            f"新最安値: ¥{new_low:,}\n"
            f"前回最安値: ¥{previous:,}（¥{diff:,} 安）"
        )
    return (
        f"🔔 価格情報を取得しました\n"
        f"商品: {item_name}\n"
        f"最安値: ¥{new_low:,}"
    )


# ── Lookup tenant LINE user ID ────────────────────────────────────────────────

async def _get_tenant_line_user_id(tenant_id: uuid.UUID) -> str | None:
    async with AsyncSessionLocal() as db:
        tenant: Tenant | None = await db.scalar(
            select(Tenant).where(Tenant.id == tenant_id)
        )
        return tenant.line_user_id if tenant else None


# ── Send LINE notification task ───────────────────────────────────────────────

@celery_app.task(
    bind=True,
    name="app.workers.tasks.notify_task.send_line_notification_task",
    queue="notify",
    max_retries=5,
    default_retry_delay=30,
    retry_backoff=True,
    retry_backoff_max=480,
    retry_jitter=True,
    acks_late=True,
)
def send_line_notification_task(
    self: Task,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """
    Send an all-time-low LINE push notification to the tenant owner.

    Parameters
    ----------
    payload : Notification payload dict from crawl_service.

    Returns
    -------
    dict with keys: sent, line_user_id, tenant_id, item_name, new_low
    """
    if not settings.line_channel_access_token:
        logger.warning(
            "send_line_notification_task: LINE_CHANNEL_ACCESS_TOKEN not configured — skip"
        )
        return {"sent": False, "reason": "LINE_CHANNEL_ACCESS_TOKEN not set"}

    tenant_id_str = payload.get("tenant_id", "")
    try:
        tenant_id = uuid.UUID(tenant_id_str)
    except ValueError:
        logger.error("send_line_notification_task: invalid tenant_id %s", tenant_id_str)
        return {"sent": False, "reason": f"invalid tenant_id: {tenant_id_str}"}

    async def _get_user_id() -> str | None:
        return await _get_tenant_line_user_id(tenant_id)

    line_user_id = asyncio.run(_get_user_id())
    if not line_user_id:
        logger.info(
            "send_line_notification_task: tenant %s has no LINE user ID — skip",
            tenant_id,
        )
        return {"sent": False, "reason": "no LINE user ID for tenant"}

    text = _build_message(payload)

    try:
        api = _line_api()
        api.push_message(
            PushMessageRequest(
                to=line_user_id,
                messages=[TextMessage(text=text)],
            )
        )
        logger.info(
            "send_line_notification_task: sent to %s (tenant=%s, item=%s, new_low=%s)",
            line_user_id,
            tenant_id,
            payload.get("item_name"),
            payload.get("new_low"),
        )
        return {
            "sent": True,
            "line_user_id": line_user_id,
            "tenant_id": tenant_id_str,
            "item_name": payload.get("item_name"),
            "new_low": payload.get("new_low"),
        }
    except Exception as exc:  # noqa: BLE001
        exc_str = str(exc)
        # Permanent failures — do not retry
        if any(code in exc_str for code in ("400", "403", "InvalidSignature")):
            logger.error(
                "send_line_notification_task: permanent failure for tenant %s: %s",
                tenant_id,
                exc,
            )
            return {"sent": False, "reason": exc_str}
        # Transient (429, 5xx) — retry
        logger.warning(
            "send_line_notification_task: transient error (attempt %d/%d): %s",
            self.request.retries + 1,
            self.max_retries + 1,
            exc,
        )
        try:
            raise self.retry(exc=exc)
        except MaxRetriesExceededError:
            logger.error(
                "send_line_notification_task: max retries exceeded for tenant %s",
                tenant_id,
            )
            return {"sent": False, "reason": "max retries exceeded", "error": exc_str}


# ── Redis queue consumer task ─────────────────────────────────────────────────

@celery_app.task(
    bind=True,
    name="app.workers.tasks.notify_task.consume_line_queue_task",
    queue="notify",
    max_retries=0,
    acks_late=True,
)
def consume_line_queue_task(self: Task, batch_size: int = 20) -> dict[str, Any]:
    """
    Drain up to `batch_size` items from the Redis line_notifications queue
    and fire send_line_notification_task for each.

    This task is designed to be triggered periodically (e.g. every 30s via Beat)
    or called directly after a crawl run.
    """
    def _redis_url(db: int) -> str:
        base = settings.redis_url.rsplit("/", 1)[0]
        return f"{base}/{db}"

    async def _drain() -> list[dict]:
        client = aioredis.from_url(_redis_url(_REDIS_LINE_DB), decode_responses=True)
        items: list[dict] = []
        try:
            for _ in range(batch_size):
                raw = await client.lpop(_LINE_QUEUE_KEY)
                if raw is None:
                    break
                try:
                    items.append(json.loads(raw))
                except json.JSONDecodeError:
                    logger.error("consume_line_queue_task: invalid JSON in queue: %s", raw)
        finally:
            await client.aclose()
        return items

    payloads = asyncio.run(_drain())
    enqueued = 0
    for payload in payloads:
        send_line_notification_task.apply_async(
            args=[payload],
            queue="notify",
        )
        enqueued += 1

    logger.info("consume_line_queue_task: dispatched %d LINE notification tasks", enqueued)
    return {"dispatched": enqueued}
