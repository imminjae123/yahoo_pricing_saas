"""
app/services/crawl_service.py — Competitor price crawling service.

Responsibilities
----------------
1. For a given CompetitorProduct, call Yahoo! Shopping API to fetch latest price.
2. INSERT PriceHistory row (append-only — DB trigger enforces no UPDATE/DELETE).
3. INSERT AuditLog row in the SAME transaction as PriceHistory.
4. If a new all-time low is detected:
   - UPDATE competitor_products.all_time_low_price + latest_price
   - Push a LINE notification task to Redis DB-1 queue (JSON payload).
5. Otherwise just UPDATE competitor_products.latest_price.

Redis queue design (from context handover)
------------------------------------------
  DB 0 — Yahoo! API rate-limit Token Bucket  (not managed here)
  DB 1 — Celery/RQ broker — LINE notification tasks   ← this file pushes here
  DB 2 — tenant metadata cache                        (not managed here)

LINE queue payload schema
--------------------------
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

Usage (from a Celery task or scheduler)
----------------------------------------
    from app.services.crawl_service import crawl_competitor

    async with AsyncSessionLocal() as db:
        await crawl_competitor(
            db,
            competitor_product_id=uuid.UUID("..."),
            tenant_id=uuid.UUID("..."),
        )
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.audit_log import AuditLog
from app.models.price_history import PriceHistory
from app.models.product import CompetitorProduct
from app.services.yahoo import YahooAPIError, YahooSearchParams, YahooShoppingClient

logger = logging.getLogger(__name__)

# Redis DB-1 is the Celery/RQ broker for LINE notification tasks
_REDIS_LINE_QUEUE_DB = 1
_LINE_QUEUE_KEY = "line_notifications"


# ── Redis client factory ──────────────────────────────────────────────────────

def _make_redis() -> aioredis.Redis:
    """Return an async Redis client connected to DB-1 (LINE notification queue)."""
    base_url = settings.redis_url.rsplit("/", 1)[0]  # strip existing db index
    url = f"{base_url}/{_REDIS_LINE_QUEUE_DB}"
    return aioredis.from_url(url, decode_responses=True)


# ── Core crawl function ───────────────────────────────────────────────────────

async def crawl_competitor(
    db: AsyncSession,
    competitor_product_id: uuid.UUID,
    tenant_id: uuid.UUID,
    *,
    yahoo_client: YahooShoppingClient | None = None,
) -> dict[str, Any]:
    """
    Fetch the latest price for one CompetitorProduct from Yahoo! Shopping API,
    persist the result, and enqueue LINE notification if a new all-time low
    is detected.

    Parameters
    ----------
    db                      : Active AsyncSession (caller manages transaction scope).
    competitor_product_id   : PK of the CompetitorProduct row to crawl.
    tenant_id               : Tenant that owns this monitoring relationship.
    yahoo_client            : Optional pre-opened YahooShoppingClient (for
                              batch reuse). If None, one is opened internally.

    Returns
    -------
    dict with keys:
        competitor_product_id, yahoo_item_code, price, is_all_time_low,
        previous_low, price_history_id, line_queued

    Raises
    ------
    ValueError          : CompetitorProduct not found.
    YahooAPIError       : Yahoo! API returned non-2xx.
    httpx.TimeoutError  : Yahoo! API timed out.
    """
    # ── 1. Load CompetitorProduct row ─────────────────────────────────────────
    competitor: CompetitorProduct | None = await db.scalar(
        select(CompetitorProduct).where(
            CompetitorProduct.id == competitor_product_id,
            CompetitorProduct.is_active.is_(True),
        )
    )
    if competitor is None:
        raise ValueError(
            f"CompetitorProduct {competitor_product_id} not found or inactive"
        )

    yahoo_item_code = competitor.yahoo_item_code
    item_name = competitor.item_name

    # ── 2. Fetch price from Yahoo! Shopping API ───────────────────────────────
    fetched_price = await _fetch_price(
        yahoo_item_code=yahoo_item_code,
        item_name=item_name,
        yahoo_client=yahoo_client,
    )

    if fetched_price is None:
        logger.warning(
            "crawl_competitor: no price returned for yahoo_item_code=%s — skipping",
            yahoo_item_code,
        )
        return {
            "competitor_product_id": competitor_product_id,
            "yahoo_item_code": yahoo_item_code,
            "price": None,
            "is_all_time_low": False,
            "previous_low": competitor.all_time_low_price,
            "price_history_id": None,
            "line_queued": False,
        }

    # ── 3. Determine all-time low ─────────────────────────────────────────────
    previous_low: int | None = competitor.all_time_low_price
    is_all_time_low = (
        previous_low is None or fetched_price < previous_low
    )

    # ── 4. INSERT PriceHistory + AuditLog in ONE transaction ─────────────────
    price_history = PriceHistory(
        tenant_id=tenant_id,
        product_ref_id=competitor_product_id,
        product_type="COMPETITOR",
        price=fetched_price,
        recommended_price=None,
        source="CRAWL",
        rule_id=None,
        is_all_time_low=is_all_time_low,
    )
    db.add(price_history)
    await db.flush()  # obtain price_history.id before audit log

    audit_details: dict[str, Any] = {
        "action": "PRICE_CRAWLED",
        "yahoo_item_code": yahoo_item_code,
        "item_name": item_name,
        "price": fetched_price,
        "previous_low": previous_low,
        "is_all_time_low": is_all_time_low,
        "price_history_id": str(price_history.id),
    }
    if is_all_time_low:
        audit_details["action"] = "ALL_TIME_LOW"
        audit_details["new_low"] = fetched_price

    audit = AuditLog(
        tenant_id=tenant_id,
        actor_user_id=None,  # system-generated
        entity_type="COMPETITOR_PRODUCT",
        entity_id=competitor_product_id,
        action=audit_details["action"],
        details=audit_details,
    )
    db.add(audit)

    # ── 5. UPDATE CompetitorProduct.latest_price (+ all_time_low if needed) ──
    update_vals: dict[str, Any] = {"latest_price": fetched_price}
    if is_all_time_low:
        update_vals["all_time_low_price"] = fetched_price

    await db.execute(
        update(CompetitorProduct)
        .where(CompetitorProduct.id == competitor_product_id)
        .values(**update_vals)
    )

    # Commit PriceHistory + AuditLog + CompetitorProduct update atomically
    await db.commit()

    logger.info(
        "crawl_competitor: yahoo_item_code=%s price=%d all_time_low=%s",
        yahoo_item_code,
        fetched_price,
        is_all_time_low,
    )

    # ── 6. Enqueue LINE notification AFTER commit (fire-and-forget) ──────────
    line_queued = False
    if is_all_time_low:
        line_queued = await _enqueue_line_notification(
            tenant_id=tenant_id,
            competitor_product_id=competitor_product_id,
            yahoo_item_code=yahoo_item_code,
            item_name=item_name,
            previous_low=previous_low,
            new_low=fetched_price,
            price_history_id=price_history.id,
        )

    return {
        "competitor_product_id": competitor_product_id,
        "yahoo_item_code": yahoo_item_code,
        "price": fetched_price,
        "is_all_time_low": is_all_time_low,
        "previous_low": previous_low,
        "price_history_id": price_history.id,
        "line_queued": line_queued,
    }


# ── Batch crawl ───────────────────────────────────────────────────────────────

async def crawl_all_competitors(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    batch_size: int = 20,
) -> list[dict[str, Any]]:
    """
    Crawl all active CompetitorProducts for a tenant in batches.

    Each item is crawled sequentially to respect Yahoo! API rate limits
    (Token Bucket on Redis DB-0 is managed externally by the Celery worker).

    Returns list of per-item crawl result dicts (same shape as crawl_competitor).
    """
    competitors = (
        await db.scalars(
            select(CompetitorProduct)
            .where(CompetitorProduct.is_active.is_(True))
            .limit(batch_size)
        )
    ).all()

    results: list[dict[str, Any]] = []

    async with YahooShoppingClient(client_id=settings.yahoo_client_id) as client:
        for comp in competitors:
            try:
                result = await crawl_competitor(
                    db,
                    competitor_product_id=comp.id,
                    tenant_id=tenant_id,
                    yahoo_client=client,
                )
                results.append(result)
            except YahooAPIError as exc:
                logger.error(
                    "crawl_all_competitors: Yahoo API error for %s: %s",
                    comp.yahoo_item_code,
                    exc,
                )
                results.append({
                    "competitor_product_id": comp.id,
                    "yahoo_item_code": comp.yahoo_item_code,
                    "price": None,
                    "is_all_time_low": False,
                    "previous_low": comp.all_time_low_price,
                    "price_history_id": None,
                    "line_queued": False,
                    "error": str(exc),
                })
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "crawl_all_competitors: unexpected error for %s",
                    comp.yahoo_item_code,
                )
                results.append({
                    "competitor_product_id": comp.id,
                    "yahoo_item_code": comp.yahoo_item_code,
                    "price": None,
                    "is_all_time_low": False,
                    "previous_low": comp.all_time_low_price,
                    "price_history_id": None,
                    "line_queued": False,
                    "error": str(exc),
                })

    return results


# ── Private helpers ───────────────────────────────────────────────────────────

async def _fetch_price(
    yahoo_item_code: str,
    item_name: str | None,
    yahoo_client: YahooShoppingClient | None,
) -> int | None:
    """
    Fetch the current price for a Yahoo! item code.

    Strategy:
    1. Search by yahoo_item_code directly (exact code match).
    2. If no hit, fall back to item_name keyword search and pick first result.

    Returns None if no price could be found.
    """
    _own_client = yahoo_client is None
    client = yahoo_client or YahooShoppingClient(client_id=settings.yahoo_client_id)

    try:
        if _own_client:
            await client.__aenter__()

        # Attempt 1: search by item code
        if yahoo_item_code:
            try:
                resp = await client.search(
                    YahooSearchParams(query=yahoo_item_code, results=5, sort="+price")
                )
                # Look for an exact code match first
                for hit in resp.hits:
                    if hit.code and hit.code == yahoo_item_code:
                        return hit.price
                # Accept first result if code not found
                if resp.hits:
                    return resp.hits[0].price
            except YahooAPIError as exc:
                logger.warning(
                    "_fetch_price: code search failed for %s: %s",
                    yahoo_item_code,
                    exc,
                )

        # Attempt 2: fallback to item name keyword search
        if item_name:
            try:
                resp = await client.search(
                    YahooSearchParams(query=item_name, results=5, sort="+price")
                )
                if resp.hits:
                    return resp.hits[0].price
            except YahooAPIError as exc:
                logger.warning(
                    "_fetch_price: name search failed for %s: %s",
                    item_name,
                    exc,
                )

        return None

    finally:
        if _own_client:
            await client.__aexit__(None, None, None)


async def _enqueue_line_notification(
    *,
    tenant_id: uuid.UUID,
    competitor_product_id: uuid.UUID,
    yahoo_item_code: str,
    item_name: str | None,
    previous_low: int | None,
    new_low: int,
    price_history_id: uuid.UUID,
) -> bool:
    """
    Push a LINE notification task to Redis DB-1 queue.

    Uses RPUSH so Celery/RQ workers can BLPOP from the left.
    Returns True on success, False on any Redis error (non-fatal — crawl already committed).
    """
    payload: dict[str, Any] = {
        "task": "send_line_notification",
        "tenant_id": str(tenant_id),
        "competitor_product_id": str(competitor_product_id),
        "yahoo_item_code": yahoo_item_code,
        "item_name": item_name,
        "previous_low": previous_low,
        "new_low": new_low,
        "price_history_id": str(price_history_id),
    }

    redis_client: aioredis.Redis = _make_redis()
    try:
        await redis_client.rpush(_LINE_QUEUE_KEY, json.dumps(payload, ensure_ascii=False))
        logger.info(
            "_enqueue_line_notification: queued all_time_low for %s (new_low=%d)",
            yahoo_item_code,
            new_low,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        # Redis failure must NOT rollback the already-committed DB transaction.
        logger.error(
            "_enqueue_line_notification: Redis push failed for %s: %s",
            yahoo_item_code,
            exc,
        )
        return False
    finally:
        await redis_client.aclose()
