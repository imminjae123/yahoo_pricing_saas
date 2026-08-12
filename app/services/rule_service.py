"""
app/services/rule_service.py — PricingRule CRUD business logic.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import MyProduct
from app.models.rule import PricingRule


# ── helpers ───────────────────────────────────────────────────────────────────

async def _get_rule_or_404(
    db: AsyncSession, rule_id: uuid.UUID, tenant_id: uuid.UUID
) -> PricingRule:
    row: PricingRule | None = await db.scalar(
        select(PricingRule).where(
            PricingRule.id == rule_id,
            PricingRule.tenant_id == tenant_id,
        )
    )
    if row is None:
        raise ValueError(f"PricingRule {rule_id} not found")
    return row


# ── list ─────────────────────────────────────────────────────────────────────

async def list_rules(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    my_product_id: uuid.UUID | None = None,
    active_only: bool = False,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    q = select(PricingRule).where(PricingRule.tenant_id == tenant_id)
    if my_product_id is not None:
        q = q.where(PricingRule.my_product_id == my_product_id)
    if active_only:
        q = q.where(PricingRule.is_active.is_(True))

    total: int = await db.scalar(
        select(func.count()).select_from(q.subquery())
    ) or 0

    rows = (
        await db.scalars(
            q.order_by(PricingRule.priority.asc(), PricingRule.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    return {"items": list(rows), "total": total, "page": page, "page_size": page_size}


# ── get ───────────────────────────────────────────────────────────────────────

async def get_rule(
    db: AsyncSession, rule_id: uuid.UUID, tenant_id: uuid.UUID
) -> PricingRule:
    return await _get_rule_or_404(db, rule_id, tenant_id)


# ── create ────────────────────────────────────────────────────────────────────

async def create_rule(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    name: str,
    description: str | None,
    my_product_id: uuid.UUID | None,
    condition: dict[str, Any],
    priority: int,
) -> PricingRule:
    # Verify my_product_id belongs to tenant when provided
    if my_product_id is not None:
        product: MyProduct | None = await db.scalar(
            select(MyProduct).where(
                MyProduct.id == my_product_id,
                MyProduct.tenant_id == tenant_id,
            )
        )
        if product is None:
            raise ValueError(f"MyProduct {my_product_id} not found for this tenant")

    rule = PricingRule(
        tenant_id=tenant_id,
        name=name,
        description=description,
        my_product_id=my_product_id,
        condition=condition,
        priority=priority,
        is_active=True,
    )
    db.add(rule)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise ValueError(f"Rule name '{name}' already exists for this tenant")
    await db.commit()
    await db.refresh(rule)
    return rule


# ── update (full replace) ─────────────────────────────────────────────────────

async def update_rule(
    db: AsyncSession,
    rule_id: uuid.UUID,
    tenant_id: uuid.UUID,
    *,
    name: str,
    description: str | None,
    my_product_id: uuid.UUID | None,
    condition: dict[str, Any],
    priority: int,
    is_active: bool,
) -> PricingRule:
    rule = await _get_rule_or_404(db, rule_id, tenant_id)

    if my_product_id is not None and my_product_id != rule.my_product_id:
        product: MyProduct | None = await db.scalar(
            select(MyProduct).where(
                MyProduct.id == my_product_id,
                MyProduct.tenant_id == tenant_id,
            )
        )
        if product is None:
            raise ValueError(f"MyProduct {my_product_id} not found for this tenant")

    rule.name = name
    rule.description = description
    rule.my_product_id = my_product_id
    rule.condition = condition
    rule.priority = priority
    rule.is_active = is_active

    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise ValueError(f"Rule name '{name}' already exists for this tenant")
    await db.commit()
    await db.refresh(rule)
    return rule


# ── patch (partial update) ────────────────────────────────────────────────────

async def patch_rule(
    db: AsyncSession,
    rule_id: uuid.UUID,
    tenant_id: uuid.UUID,
    *,
    name: str | None = None,
    description: str | None = None,
    my_product_id: uuid.UUID | None = None,
    condition: dict[str, Any] | None = None,
    priority: int | None = None,
    is_active: bool | None = None,
) -> PricingRule:
    rule = await _get_rule_or_404(db, rule_id, tenant_id)

    if name is not None:
        rule.name = name
    if description is not None:
        rule.description = description
    if my_product_id is not None:
        product: MyProduct | None = await db.scalar(
            select(MyProduct).where(
                MyProduct.id == my_product_id,
                MyProduct.tenant_id == tenant_id,
            )
        )
        if product is None:
            raise ValueError(f"MyProduct {my_product_id} not found for this tenant")
        rule.my_product_id = my_product_id
    if condition is not None:
        rule.condition = condition
    if priority is not None:
        rule.priority = priority
    if is_active is not None:
        rule.is_active = is_active

    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise ValueError(f"Rule name '{rule.name}' already exists for this tenant")
    await db.commit()
    await db.refresh(rule)
    return rule


# ── delete (soft) ─────────────────────────────────────────────────────────────

async def delete_rule(
    db: AsyncSession, rule_id: uuid.UUID, tenant_id: uuid.UUID
) -> None:
    """Soft-delete: set is_active = False."""
    rule = await _get_rule_or_404(db, rule_id, tenant_id)
    rule.is_active = False
    await db.commit()


# ── load product financials for simulation ────────────────────────────────────

async def load_product_financials(
    db: AsyncSession,
    my_product_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> tuple[Decimal, Decimal, Decimal | None]:
    """Return (cost, min_margin_amount, min_margin_rate) for a product."""
    product: MyProduct | None = await db.scalar(
        select(MyProduct).where(
            MyProduct.id == my_product_id,
            MyProduct.tenant_id == tenant_id,
        )
    )
    if product is None:
        raise ValueError(f"MyProduct {my_product_id} not found for this tenant")
    return product.cost, product.min_margin_amount, product.min_margin_rate
