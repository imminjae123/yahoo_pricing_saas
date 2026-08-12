"""
app/services/product_service.py — MyProduct, CompetitorProduct, ProductMapping business logic.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.price_floor import compute_defensive_price
from app.models.mapping import ProductMapping
from app.models.product import CompetitorProduct, MyProduct


# ── helpers ───────────────────────────────────────────────────────────────────

async def _get_my_product_or_404(
    db: AsyncSession, product_id: uuid.UUID, tenant_id: uuid.UUID
) -> MyProduct:
    row: MyProduct | None = await db.scalar(
        select(MyProduct).where(
            MyProduct.id == product_id,
            MyProduct.tenant_id == tenant_id,
        )
    )
    if row is None:
        raise ValueError(f"MyProduct {product_id} not found")
    return row


# ── MyProduct CRUD ────────────────────────────────────────────────────────────

async def list_my_products(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    page: int = 1,
    page_size: int = 20,
    active_only: bool = True,
) -> dict[str, Any]:
    base_q = select(MyProduct).where(MyProduct.tenant_id == tenant_id)
    if active_only:
        base_q = base_q.where(MyProduct.is_active.is_(True))

    total: int = await db.scalar(
        select(func.count()).select_from(base_q.subquery())
    ) or 0

    rows = (
        await db.scalars(
            base_q.order_by(MyProduct.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    return {"items": list(rows), "total": total, "page": page, "page_size": page_size}


async def get_my_product(
    db: AsyncSession, product_id: uuid.UUID, tenant_id: uuid.UUID
) -> MyProduct:
    return await _get_my_product_or_404(db, product_id, tenant_id)


async def create_my_product(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    sku: str,
    name: str,
    description: str | None,
    yahoo_item_code: str | None,
    cost: Decimal,
    min_margin_amount: Decimal,
    min_margin_rate: Decimal | None,
) -> MyProduct:
    defensive = Decimal(
        compute_defensive_price(
            cost=cost,
            min_margin_amount=min_margin_amount if min_margin_amount != Decimal("0") or min_margin_rate is None else None,
            min_margin_rate=min_margin_rate,
        )
    )
    product = MyProduct(
        tenant_id=tenant_id,
        sku=sku,
        name=name,
        description=description,
        yahoo_item_code=yahoo_item_code,
        cost=cost,
        min_margin_amount=min_margin_amount,
        min_margin_rate=min_margin_rate,
        defensive_price=defensive,
    )
    db.add(product)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise ValueError(f"SKU '{sku}' already exists for this tenant")
    await db.commit()
    await db.refresh(product)
    return product


async def update_my_product(
    db: AsyncSession,
    product_id: uuid.UUID,
    tenant_id: uuid.UUID,
    *,
    name: str | None = None,
    description: str | None = None,
    yahoo_item_code: str | None = None,
    cost: Decimal | None = None,
    min_margin_amount: Decimal | None = None,
    min_margin_rate: Decimal | None = None,
    is_active: bool | None = None,
) -> MyProduct:
    product = await _get_my_product_or_404(db, product_id, tenant_id)

    if name is not None:
        product.name = name
    if description is not None:
        product.description = description
    if yahoo_item_code is not None:
        product.yahoo_item_code = yahoo_item_code
    if is_active is not None:
        product.is_active = is_active

    # Recompute defensive_price if financial fields change
    new_cost = cost if cost is not None else product.cost
    new_margin_amount = min_margin_amount if min_margin_amount is not None else product.min_margin_amount
    new_margin_rate = min_margin_rate if min_margin_rate is not None else product.min_margin_rate

    if cost is not None:
        product.cost = new_cost
    if min_margin_amount is not None:
        product.min_margin_amount = new_margin_amount
    if min_margin_rate is not None:
        product.min_margin_rate = new_margin_rate

    if any(v is not None for v in (cost, min_margin_amount, min_margin_rate)):
        product.defensive_price = Decimal(
            compute_defensive_price(
                cost=new_cost,
                min_margin_amount=new_margin_amount if new_margin_amount != Decimal("0") or new_margin_rate is None else None,
                min_margin_rate=new_margin_rate,
            )
        )

    await db.commit()
    await db.refresh(product)
    return product


async def delete_my_product(
    db: AsyncSession, product_id: uuid.UUID, tenant_id: uuid.UUID
) -> None:
    """Soft-delete: set is_active = False."""
    product = await _get_my_product_or_404(db, product_id, tenant_id)
    product.is_active = False
    await db.commit()


# ── CompetitorProduct ─────────────────────────────────────────────────────────

async def create_competitor(
    db: AsyncSession,
    *,
    yahoo_item_code: str,
    shop_code: str | None,
    shop_name: str | None,
    item_name: str | None,
    item_url: str | None,
) -> CompetitorProduct:
    """Upsert by yahoo_item_code — returns existing row if duplicate."""
    existing: CompetitorProduct | None = await db.scalar(
        select(CompetitorProduct).where(
            CompetitorProduct.yahoo_item_code == yahoo_item_code
        )
    )
    if existing:
        return existing

    competitor = CompetitorProduct(
        yahoo_item_code=yahoo_item_code,
        shop_code=shop_code,
        shop_name=shop_name,
        item_name=item_name,
        item_url=item_url,
    )
    db.add(competitor)
    await db.flush()
    await db.commit()
    await db.refresh(competitor)
    return competitor


async def get_competitor(db: AsyncSession, competitor_id: uuid.UUID) -> CompetitorProduct:
    row: CompetitorProduct | None = await db.scalar(
        select(CompetitorProduct).where(CompetitorProduct.id == competitor_id)
    )
    if row is None:
        raise ValueError(f"CompetitorProduct {competitor_id} not found")
    return row


# ── ProductMapping ────────────────────────────────────────────────────────────

async def list_mappings(
    db: AsyncSession, my_product_id: uuid.UUID, tenant_id: uuid.UUID
) -> list[ProductMapping]:
    rows = (
        await db.scalars(
            select(ProductMapping)
            .where(
                ProductMapping.my_product_id == my_product_id,
                ProductMapping.tenant_id == tenant_id,
            )
            .options(selectinload(ProductMapping.competitor_product))
            .order_by(ProductMapping.created_at.desc())
        )
    ).all()
    return list(rows)


async def create_mapping(
    db: AsyncSession,
    my_product_id: uuid.UUID,
    tenant_id: uuid.UUID,
    *,
    competitor_product_id: uuid.UUID,
    is_primary: bool,
    label: str | None,
) -> ProductMapping:
    # Verify my_product belongs to tenant
    await _get_my_product_or_404(db, my_product_id, tenant_id)

    # Verify competitor exists
    comp: CompetitorProduct | None = await db.scalar(
        select(CompetitorProduct).where(CompetitorProduct.id == competitor_product_id)
    )
    if comp is None:
        raise ValueError(f"CompetitorProduct {competitor_product_id} not found")

    # If is_primary, demote existing primary for this my_product
    if is_primary:
        existing_primary: ProductMapping | None = await db.scalar(
            select(ProductMapping).where(
                ProductMapping.my_product_id == my_product_id,
                ProductMapping.tenant_id == tenant_id,
                ProductMapping.is_primary.is_(True),
            )
        )
        if existing_primary:
            existing_primary.is_primary = False
            await db.flush()

    mapping = ProductMapping(
        tenant_id=tenant_id,
        my_product_id=my_product_id,
        competitor_product_id=competitor_product_id,
        is_primary=is_primary,
        label=label,
    )
    db.add(mapping)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise ValueError("Mapping already exists for this product pair")

    await db.commit()
    await db.refresh(mapping)
    # Eagerly load competitor for response
    await db.refresh(mapping, attribute_names=["competitor_product"])
    return mapping


async def delete_mapping(
    db: AsyncSession,
    mapping_id: uuid.UUID,
    my_product_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> None:
    row: ProductMapping | None = await db.scalar(
        select(ProductMapping).where(
            ProductMapping.id == mapping_id,
            ProductMapping.my_product_id == my_product_id,
            ProductMapping.tenant_id == tenant_id,
        )
    )
    if row is None:
        raise ValueError(f"Mapping {mapping_id} not found")
    await db.delete(row)
    await db.commit()
