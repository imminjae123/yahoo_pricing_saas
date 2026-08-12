"""
app/services/dashboard_service.py — Dashboard query logic.

All queries are read-only. No writes, no side effects.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.dashboard import (
    DashboardSummaryResponse,
    PriceDataPoint,
    PriceHistoryChartResponse,
    ProductPriceSeriesResponse,
    ProductSummaryItem,
)
from app.models.mapping import ProductMapping
from app.models.price_history import PriceHistory
from app.models.product import CompetitorProduct, MyProduct
from app.models.rule import PricingRule
from app.models.user import UserRole


# ── Summary ───────────────────────────────────────────────────────────────────

async def get_summary(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    role: UserRole,
) -> DashboardSummaryResponse:
    now = datetime.now(timezone.utc)
    cutoff_24h = now - timedelta(hours=24)

    # ── Total active my_products ──────────────────────────────────────────────
    total_my_products: int = await db.scalar(
        select(func.count(MyProduct.id)).where(
            MyProduct.tenant_id == tenant_id,
            MyProduct.is_active.is_(True),
        )
    ) or 0

    # ── Total mapped competitor products (distinct) ───────────────────────────
    total_competitor_products: int = await db.scalar(
        select(func.count(func.distinct(ProductMapping.competitor_product_id))).where(
            ProductMapping.tenant_id == tenant_id,
        )
    ) or 0

    # ── Total mappings ────────────────────────────────────────────────────────
    total_mappings: int = await db.scalar(
        select(func.count(ProductMapping.id)).where(
            ProductMapping.tenant_id == tenant_id,
        )
    ) or 0

    # ── Active rules ──────────────────────────────────────────────────────────
    total_active_rules: int = await db.scalar(
        select(func.count(PricingRule.id)).where(
            PricingRule.tenant_id == tenant_id,
            PricingRule.is_active.is_(True),
        )
    ) or 0

    # ── Crawl activity last 24h ───────────────────────────────────────────────
    crawls_last_24h: int = await db.scalar(
        select(func.count(PriceHistory.id)).where(
            PriceHistory.tenant_id == tenant_id,
            PriceHistory.captured_at >= cutoff_24h,
        )
    ) or 0

    all_time_lows_last_24h: int = await db.scalar(
        select(func.count(PriceHistory.id)).where(
            PriceHistory.tenant_id == tenant_id,
            PriceHistory.captured_at >= cutoff_24h,
            PriceHistory.is_all_time_low.is_(True),
        )
    ) or 0

    # ── Global competitor price stats (latest snapshot per competitor) ────────
    # Subquery: latest captured_at per product_ref_id
    latest_sub = (
        select(
            PriceHistory.product_ref_id,
            func.max(PriceHistory.captured_at).label("latest_at"),
        )
        .where(
            PriceHistory.tenant_id == tenant_id,
            PriceHistory.product_type == "COMPETITOR",
        )
        .group_by(PriceHistory.product_ref_id)
        .subquery()
    )
    latest_prices_q = (
        select(PriceHistory.price)
        .join(
            latest_sub,
            and_(
                PriceHistory.product_ref_id == latest_sub.c.product_ref_id,
                PriceHistory.captured_at == latest_sub.c.latest_at,
            ),
        )
        .where(PriceHistory.tenant_id == tenant_id)
    )
    latest_price_rows = (await db.scalars(latest_prices_q)).all()

    cheapest_competitor_price: int | None = min(latest_price_rows) if latest_price_rows else None
    avg_competitor_price: float | None = (
        sum(latest_price_rows) / len(latest_price_rows) if latest_price_rows else None
    )

    # ── Per-product breakdown ─────────────────────────────────────────────────
    my_products = (
        await db.scalars(
            select(MyProduct).where(
                MyProduct.tenant_id == tenant_id,
                MyProduct.is_active.is_(True),
            ).order_by(MyProduct.created_at.desc())
        )
    ).all()

    product_items: list[ProductSummaryItem] = []
    for p in my_products:
        # Competitor count for this product
        comp_count: int = await db.scalar(
            select(func.count(ProductMapping.id)).where(
                ProductMapping.my_product_id == p.id,
                ProductMapping.tenant_id == tenant_id,
            )
        ) or 0

        # All-time low count last 24h for mapped competitors
        mapped_comp_ids_q = (
            select(ProductMapping.competitor_product_id)
            .where(
                ProductMapping.my_product_id == p.id,
                ProductMapping.tenant_id == tenant_id,
            )
        )
        atl_count: int = await db.scalar(
            select(func.count(PriceHistory.id)).where(
                PriceHistory.product_ref_id.in_(mapped_comp_ids_q),
                PriceHistory.tenant_id == tenant_id,
                PriceHistory.is_all_time_low.is_(True),
                PriceHistory.captured_at >= cutoff_24h,
            )
        ) or 0

        # Last crawled_at for this product's competitors
        last_crawled: datetime | None = await db.scalar(
            select(func.max(PriceHistory.captured_at)).where(
                PriceHistory.product_ref_id.in_(mapped_comp_ids_q),
                PriceHistory.tenant_id == tenant_id,
            )
        )

        # Hide sensitive fields from VIEWER
        defensive = float(p.defensive_price) if role != UserRole.VIEWER else 0.0

        product_items.append(
            ProductSummaryItem(
                product_id=p.id,
                sku=p.sku,
                name=p.name,
                current_price=float(p.current_price) if p.current_price is not None else None,
                defensive_price=defensive,
                competitor_count=comp_count,
                all_time_low_count=atl_count,
                last_crawled_at=last_crawled,
            )
        )

    return DashboardSummaryResponse(
        tenant_id=tenant_id,
        total_my_products=total_my_products,
        total_competitor_products=total_competitor_products,
        total_mappings=total_mappings,
        total_active_rules=total_active_rules,
        crawls_last_24h=crawls_last_24h,
        all_time_lows_last_24h=all_time_lows_last_24h,
        cheapest_competitor_price=cheapest_competitor_price,
        avg_competitor_price=avg_competitor_price,
        products=product_items,
        generated_at=now,
    )


# ── Price History Chart ───────────────────────────────────────────────────────

async def get_price_history_chart(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    product_id: uuid.UUID | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    limit: int = 200,
) -> PriceHistoryChartResponse:
    now = datetime.now(timezone.utc)
    from_date = from_date or (now - timedelta(days=7))
    to_date = to_date or now

    series: list[ProductPriceSeriesResponse] = []

    if product_id is not None:
        # ── Single product + its mapped competitors ───────────────────────────
        series = await _series_for_product(
            db, tenant_id, product_id, from_date, to_date, limit
        )
    else:
        # ── All active products: one MY series each, latest N points ─────────
        products = (
            await db.scalars(
                select(MyProduct).where(
                    MyProduct.tenant_id == tenant_id,
                    MyProduct.is_active.is_(True),
                ).order_by(MyProduct.created_at.desc()).limit(20)
            )
        ).all()
        for p in products:
            p_series = await _series_for_product(
                db, tenant_id, p.id, from_date, to_date, limit
            )
            series.extend(p_series)

    return PriceHistoryChartResponse(
        tenant_id=tenant_id,
        series=series,
        from_date=from_date,
        to_date=to_date,
        generated_at=now,
    )


async def _series_for_product(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    product_id: uuid.UUID,
    from_date: datetime,
    to_date: datetime,
    limit: int,
) -> list[ProductPriceSeriesResponse]:
    """Return price series for a MyProduct and all its mapped competitors."""
    result: list[ProductPriceSeriesResponse] = []

    # ── MY product self-price series (RULE source) ────────────────────────────
    my_product: MyProduct | None = await db.scalar(
        select(MyProduct).where(
            MyProduct.id == product_id,
            MyProduct.tenant_id == tenant_id,
        )
    )
    if my_product:
        my_rows = (
            await db.scalars(
                select(PriceHistory)
                .where(
                    PriceHistory.tenant_id == tenant_id,
                    PriceHistory.product_ref_id == product_id,
                    PriceHistory.product_type == "MY",
                    PriceHistory.captured_at >= from_date,
                    PriceHistory.captured_at <= to_date,
                )
                .order_by(PriceHistory.captured_at.asc())
                .limit(limit)
            )
        ).all()

        if my_rows:
            prices = [r.price for r in my_rows]
            result.append(
                ProductPriceSeriesResponse(
                    product_ref_id=product_id,
                    product_type="MY",
                    label=f"{my_product.sku} — {my_product.name}",
                    data_points=[
                        PriceDataPoint(
                            captured_at=r.captured_at,
                            price=r.price,
                            is_all_time_low=r.is_all_time_low,
                            source=r.source,
                        )
                        for r in my_rows
                    ],
                    min_price=min(prices),
                    max_price=max(prices),
                    latest_price=prices[-1],
                    all_time_low=my_product.defensive_price and min(prices),
                    total_points=len(my_rows),
                )
            )

    # ── Mapped competitor series ───────────────────────────────────────────────
    mappings = (
        await db.scalars(
            select(ProductMapping).where(
                ProductMapping.my_product_id == product_id,
                ProductMapping.tenant_id == tenant_id,
            ).order_by(ProductMapping.is_primary.desc())
        )
    ).all()

    for mapping in mappings:
        comp_id = mapping.competitor_product_id

        comp: CompetitorProduct | None = await db.scalar(
            select(CompetitorProduct).where(CompetitorProduct.id == comp_id)
        )

        comp_rows = (
            await db.scalars(
                select(PriceHistory)
                .where(
                    PriceHistory.tenant_id == tenant_id,
                    PriceHistory.product_ref_id == comp_id,
                    PriceHistory.product_type == "COMPETITOR",
                    PriceHistory.captured_at >= from_date,
                    PriceHistory.captured_at <= to_date,
                )
                .order_by(PriceHistory.captured_at.asc())
                .limit(limit)
            )
        ).all()

        if not comp_rows:
            continue

        prices = [r.price for r in comp_rows]
        label = (comp.item_name or comp.yahoo_item_code) if comp else str(comp_id)
        if mapping.label:
            label = f"{mapping.label} — {label}"
        if mapping.is_primary:
            label = f"[PRIMARY] {label}"

        result.append(
            ProductPriceSeriesResponse(
                product_ref_id=comp_id,
                product_type="COMPETITOR",
                label=label,
                data_points=[
                    PriceDataPoint(
                        captured_at=r.captured_at,
                        price=r.price,
                        is_all_time_low=r.is_all_time_low,
                        source=r.source,
                    )
                    for r in comp_rows
                ],
                min_price=min(prices),
                max_price=max(prices),
                latest_price=prices[-1],
                all_time_low=comp.all_time_low_price if comp else None,
                total_points=len(comp_rows),
            )
        )

    return result
