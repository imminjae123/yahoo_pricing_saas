"""
app/api/v1/products.py — Products, Competitors, Mappings router.

Endpoints
---------
GET    /api/v1/products                            — list my products (all roles)
POST   /api/v1/products                            — create (OWNER, MANAGER)
GET    /api/v1/products/{product_id}               — detail (all roles; VIEWER hides cost)
PATCH  /api/v1/products/{product_id}               — update (OWNER, MANAGER)
DELETE /api/v1/products/{product_id}               — soft-delete (OWNER)

GET    /api/v1/products/{product_id}/mappings          — list mappings (all roles)
POST   /api/v1/products/{product_id}/mappings          — add mapping (OWNER, MANAGER)
DELETE /api/v1/products/{product_id}/mappings/{map_id} — remove mapping (OWNER, MANAGER)

POST   /api/v1/competitors                         — register competitor (OWNER, MANAGER)
GET    /api/v1/competitors/{competitor_id}         — detail (all roles)
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.product import (
    CompetitorProductCreateRequest,
    CompetitorProductResponse,
    MyProductCreateRequest,
    MyProductDetailResponse,
    MyProductListResponse,
    MyProductResponse,
    MyProductUpdateRequest,
    ProductMappingCreateRequest,
    ProductMappingListResponse,
    ProductMappingResponse,
)
from app.dependencies.auth import CurrentUser, get_current_user, require_roles
from app.dependencies.db import get_tenant_db
from app.models.user import UserRole
from app.services import product_service

router = APIRouter()

# ── role dependency aliases ───────────────────────────────────────────────────
_owner_manager = require_roles("OWNER", "MANAGER")
_owner_only = require_roles("OWNER")


# ── helpers ───────────────────────────────────────────────────────────────────

def _product_response(product, role: UserRole) -> MyProductResponse | MyProductDetailResponse:
    """Return detail schema for OWNER/MANAGER, safe schema for VIEWER."""
    if role == UserRole.VIEWER:
        return MyProductResponse.model_validate(product)
    return MyProductDetailResponse.model_validate(product)


# ═══════════════════════════════════════════════════════════════════════════════
# MyProducts
# ═══════════════════════════════════════════════════════════════════════════════

@router.get(
    "",
    response_model=MyProductListResponse,
    summary="自社商品一覧取得",
)
async def list_products(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    active_only: bool = Query(True),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> MyProductListResponse:
    result = await product_service.list_my_products(
        db,
        current_user.tenant_id,
        page=page,
        page_size=page_size,
        active_only=active_only,
    )
    items = [_product_response(p, current_user.role) for p in result["items"]]
    return MyProductListResponse(
        items=items,
        total=result["total"],
        page=result["page"],
        page_size=result["page_size"],
    )


@router.post(
    "",
    response_model=MyProductDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="自社商品登録 (OWNER / MANAGER)",
)
async def create_product(
    body: MyProductCreateRequest,
    current_user: CurrentUser = Depends(_owner_manager),
    db: AsyncSession = Depends(get_tenant_db),
) -> MyProductDetailResponse:
    try:
        product = await product_service.create_my_product(
            db,
            current_user.tenant_id,
            sku=body.sku,
            name=body.name,
            description=body.description,
            yahoo_item_code=body.yahoo_item_code,
            cost=body.cost,
            min_margin_amount=body.min_margin_amount,
            min_margin_rate=body.min_margin_rate,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return MyProductDetailResponse.model_validate(product)


@router.get(
    "/{product_id}",
    response_model=MyProductDetailResponse,  # FastAPI uses this for schema; actual response may be MyProductResponse
    summary="自社商品詳細取得",
)
async def get_product(
    product_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> MyProductResponse | MyProductDetailResponse:
    try:
        product = await product_service.get_my_product(db, product_id, current_user.tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return _product_response(product, current_user.role)


@router.patch(
    "/{product_id}",
    response_model=MyProductDetailResponse,
    summary="自社商品更新 (OWNER / MANAGER)",
)
async def update_product(
    product_id: uuid.UUID,
    body: MyProductUpdateRequest,
    current_user: CurrentUser = Depends(_owner_manager),
    db: AsyncSession = Depends(get_tenant_db),
) -> MyProductDetailResponse:
    try:
        product = await product_service.update_my_product(
            db,
            product_id,
            current_user.tenant_id,
            name=body.name,
            description=body.description,
            yahoo_item_code=body.yahoo_item_code,
            cost=body.cost,
            min_margin_amount=body.min_margin_amount,
            min_margin_rate=body.min_margin_rate,
            is_active=body.is_active,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return MyProductDetailResponse.model_validate(product)


@router.delete(
    "/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="自社商品ソフト削除 (OWNER)",
)
async def delete_product(
    product_id: uuid.UUID,
    current_user: CurrentUser = Depends(_owner_only),
    db: AsyncSession = Depends(get_tenant_db),
) -> None:
    try:
        await product_service.delete_my_product(db, product_id, current_user.tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


# ═══════════════════════════════════════════════════════════════════════════════
# ProductMappings
# ═══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/{product_id}/mappings",
    response_model=ProductMappingListResponse,
    summary="競合商品マッピング一覧",
)
async def list_mappings(
    product_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> ProductMappingListResponse:
    rows = await product_service.list_mappings(db, product_id, current_user.tenant_id)
    items = [
        ProductMappingResponse(
            id=m.id,
            tenant_id=m.tenant_id,
            my_product_id=m.my_product_id,
            competitor_product_id=m.competitor_product_id,
            is_primary=m.is_primary,
            label=m.label,
            competitor=CompetitorProductResponse.model_validate(m.competitor_product),
            created_at=m.created_at,
        )
        for m in rows
    ]
    return ProductMappingListResponse(items=items, total=len(items))


@router.post(
    "/{product_id}/mappings",
    response_model=ProductMappingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="競合商品マッピング追加 (OWNER / MANAGER)",
)
async def create_mapping(
    product_id: uuid.UUID,
    body: ProductMappingCreateRequest,
    current_user: CurrentUser = Depends(_owner_manager),
    db: AsyncSession = Depends(get_tenant_db),
) -> ProductMappingResponse:
    try:
        mapping = await product_service.create_mapping(
            db,
            product_id,
            current_user.tenant_id,
            competitor_product_id=body.competitor_product_id,
            is_primary=body.is_primary,
            label=body.label,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return ProductMappingResponse(
        id=mapping.id,
        tenant_id=mapping.tenant_id,
        my_product_id=mapping.my_product_id,
        competitor_product_id=mapping.competitor_product_id,
        is_primary=mapping.is_primary,
        label=mapping.label,
        competitor=CompetitorProductResponse.model_validate(mapping.competitor_product),
        created_at=mapping.created_at,
    )


@router.delete(
    "/{product_id}/mappings/{mapping_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="競合商品マッピング削除 (OWNER / MANAGER)",
)
async def delete_mapping(
    product_id: uuid.UUID,
    mapping_id: uuid.UUID,
    current_user: CurrentUser = Depends(_owner_manager),
    db: AsyncSession = Depends(get_tenant_db),
) -> None:
    try:
        await product_service.delete_mapping(
            db, mapping_id, product_id, current_user.tenant_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


# ═══════════════════════════════════════════════════════════════════════════════
# CompetitorProducts (separate prefix — mounted in main.py as /api/v1/competitors)
# ═══════════════════════════════════════════════════════════════════════════════

competitor_router = APIRouter()


@competitor_router.post(
    "",
    response_model=CompetitorProductResponse,
    status_code=status.HTTP_201_CREATED,
    summary="競合商品登録 (OWNER / MANAGER)",
)
async def create_competitor(
    body: CompetitorProductCreateRequest,
    current_user: CurrentUser = Depends(_owner_manager),
    db: AsyncSession = Depends(get_tenant_db),
) -> CompetitorProductResponse:
    competitor = await product_service.create_competitor(
        db,
        yahoo_item_code=body.yahoo_item_code,
        shop_code=body.shop_code,
        shop_name=body.shop_name,
        item_name=body.item_name,
        item_url=body.item_url,
    )
    return CompetitorProductResponse.model_validate(competitor)


@competitor_router.get(
    "/{competitor_id}",
    response_model=CompetitorProductResponse,
    summary="競合商品詳細取得",
)
async def get_competitor(
    competitor_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> CompetitorProductResponse:
    try:
        competitor = await product_service.get_competitor(db, competitor_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return CompetitorProductResponse.model_validate(competitor)
