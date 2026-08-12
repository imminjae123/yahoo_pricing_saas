"""
app/api/v1/rules.py — PricingRules router.

Endpoints
---------
GET    /api/v1/rules                  — list rules (all roles)
POST   /api/v1/rules                  — create rule (OWNER, MANAGER)
GET    /api/v1/rules/{rule_id}        — rule detail (all roles)
PUT    /api/v1/rules/{rule_id}        — full replace (OWNER, MANAGER)
PATCH  /api/v1/rules/{rule_id}        — partial update (OWNER, MANAGER)
DELETE /api/v1/rules/{rule_id}        — soft-delete (OWNER)
POST   /api/v1/rules/simulate         — stateless simulation (all roles)
POST   /api/v1/rules/{rule_id}/simulate — simulate saved rule (all roles)

Simulation endpoints are STATELESS — they never write to DB.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.rule import (
    PricingRuleCreateRequest,
    PricingRuleListResponse,
    PricingRulePatchRequest,
    PricingRuleResponse,
    PricingRuleUpdateRequest,
    RuleSimulateRequest,
    RuleSimulateResponse,
)
from app.dependencies.auth import CurrentUser, get_current_user, require_roles
from app.dependencies.db import get_tenant_db
from app.domain.rule_engine import evaluate_rule
from app.services import rule_service

router = APIRouter()

_owner_manager = require_roles("OWNER", "MANAGER")
_owner_only = require_roles("OWNER")


# ═══════════════════════════════════════════════════════════════════════════════
# IMPORTANT: /simulate (fixed path) MUST be registered BEFORE /{rule_id}
# to avoid FastAPI matching "simulate" as a UUID.
# ═══════════════════════════════════════════════════════════════════════════════

# ── POST /simulate (stateless — no saved rule) ────────────────────────────────

@router.post(
    "/simulate",
    response_model=RuleSimulateResponse,
    summary="ステートレスシミュレーション (condition をリクエストで指定)",
)
async def simulate_stateless(
    body: RuleSimulateRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> RuleSimulateResponse:
    if body.condition is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="condition is required for stateless /simulate endpoint",
        )
    if body.cost is None or body.min_margin_amount is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="cost and min_margin_amount are required for stateless /simulate",
        )

    try:
        result = evaluate_rule(
            rule_condition=body.condition.to_jsonb(),
            competitor_prices=body.competitor_prices,
            cost=Decimal(str(body.cost)),
            min_margin_amount=Decimal(str(body.min_margin_amount)),
            min_margin_rate=Decimal(str(body.min_margin_rate)) if body.min_margin_rate is not None else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    return RuleSimulateResponse(
        recommended_price=result.recommended_price,
        raw_price=result.raw_price,
        defensive_floor=result.defensive_floor,
        floor_applied=result.floor_applied,
        ceiling_applied=result.ceiling_applied,
        reference_price=result.reference_price,
        explanation=result.explanation,
        competitor_prices_used=body.competitor_prices,
        rule_type=body.condition.type,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# CRUD
# ═══════════════════════════════════════════════════════════════════════════════

@router.get(
    "",
    response_model=PricingRuleListResponse,
    summary="ルール一覧取得",
)
async def list_rules(
    my_product_id: uuid.UUID | None = Query(None),
    active_only: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> PricingRuleListResponse:
    result = await rule_service.list_rules(
        db,
        current_user.tenant_id,
        my_product_id=my_product_id,
        active_only=active_only,
        page=page,
        page_size=page_size,
    )
    return PricingRuleListResponse(
        items=[PricingRuleResponse.model_validate(r) for r in result["items"]],
        total=result["total"],
    )


@router.post(
    "",
    response_model=PricingRuleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="ルール作成 (OWNER / MANAGER)",
)
async def create_rule(
    body: PricingRuleCreateRequest,
    current_user: CurrentUser = Depends(_owner_manager),
    db: AsyncSession = Depends(get_tenant_db),
) -> PricingRuleResponse:
    try:
        rule = await rule_service.create_rule(
            db,
            current_user.tenant_id,
            name=body.name,
            description=body.description,
            my_product_id=body.my_product_id,
            condition=body.condition.to_jsonb(),
            priority=body.priority,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return PricingRuleResponse.model_validate(rule)


@router.get(
    "/{rule_id}",
    response_model=PricingRuleResponse,
    summary="ルール詳細取得",
)
async def get_rule(
    rule_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> PricingRuleResponse:
    try:
        rule = await rule_service.get_rule(db, rule_id, current_user.tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return PricingRuleResponse.model_validate(rule)


@router.put(
    "/{rule_id}",
    response_model=PricingRuleResponse,
    summary="ルール全更新 (OWNER / MANAGER)",
)
async def update_rule(
    rule_id: uuid.UUID,
    body: PricingRuleUpdateRequest,
    current_user: CurrentUser = Depends(_owner_manager),
    db: AsyncSession = Depends(get_tenant_db),
) -> PricingRuleResponse:
    try:
        rule = await rule_service.update_rule(
            db,
            rule_id,
            current_user.tenant_id,
            name=body.name,
            description=body.description,
            my_product_id=body.my_product_id,
            condition=body.condition.to_jsonb(),
            priority=body.priority,
            is_active=body.is_active,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return PricingRuleResponse.model_validate(rule)


@router.patch(
    "/{rule_id}",
    response_model=PricingRuleResponse,
    summary="ルール部分更新 (OWNER / MANAGER)",
)
async def patch_rule(
    rule_id: uuid.UUID,
    body: PricingRulePatchRequest,
    current_user: CurrentUser = Depends(_owner_manager),
    db: AsyncSession = Depends(get_tenant_db),
) -> PricingRuleResponse:
    try:
        rule = await rule_service.patch_rule(
            db,
            rule_id,
            current_user.tenant_id,
            name=body.name,
            description=body.description,
            my_product_id=body.my_product_id,
            condition=body.condition.to_jsonb() if body.condition is not None else None,
            priority=body.priority,
            is_active=body.is_active,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return PricingRuleResponse.model_validate(rule)


@router.delete(
    "/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="ルールソフト削除 (OWNER)",
)
async def delete_rule(
    rule_id: uuid.UUID,
    current_user: CurrentUser = Depends(_owner_only),
    db: AsyncSession = Depends(get_tenant_db),
) -> None:
    try:
        await rule_service.delete_rule(db, rule_id, current_user.tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


# ── POST /{rule_id}/simulate (saved rule + optional overrides) ────────────────

@router.post(
    "/{rule_id}/simulate",
    response_model=RuleSimulateResponse,
    summary="保存済みルールでシミュレーション (cost/marginはDB値 or オーバーライド可)",
)
async def simulate_saved_rule(
    rule_id: uuid.UUID,
    body: RuleSimulateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> RuleSimulateResponse:
    # Load saved rule
    try:
        rule = await rule_service.get_rule(db, rule_id, current_user.tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    # Determine condition: request body override or saved rule
    condition_dict = body.condition.to_jsonb() if body.condition is not None else rule.condition

    # Determine cost / margin: request body override or load from product
    if body.cost is not None and body.min_margin_amount is not None:
        cost = Decimal(str(body.cost))
        min_margin_amount = Decimal(str(body.min_margin_amount))
        min_margin_rate = Decimal(str(body.min_margin_rate)) if body.min_margin_rate is not None else None
    elif rule.my_product_id is not None:
        try:
            cost, min_margin_amount, min_margin_rate = await rule_service.load_product_financials(
                db, rule.my_product_id, current_user.tenant_id
            )
            # Allow partial override from request
            if body.cost is not None:
                cost = Decimal(str(body.cost))
            if body.min_margin_amount is not None:
                min_margin_amount = Decimal(str(body.min_margin_amount))
            if body.min_margin_rate is not None:
                min_margin_rate = Decimal(str(body.min_margin_rate))
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "cost and min_margin_amount are required when the rule has no linked product. "
                "Pass them in the request body."
            ),
        )

    try:
        result = evaluate_rule(
            rule_condition=condition_dict,
            competitor_prices=body.competitor_prices,
            cost=cost,
            min_margin_amount=min_margin_amount,
            min_margin_rate=min_margin_rate,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    rule_type = condition_dict.get("type", "UNKNOWN")

    return RuleSimulateResponse(
        recommended_price=result.recommended_price,
        raw_price=result.raw_price,
        defensive_floor=result.defensive_floor,
        floor_applied=result.floor_applied,
        ceiling_applied=result.ceiling_applied,
        reference_price=result.reference_price,
        explanation=result.explanation,
        competitor_prices_used=body.competitor_prices,
        rule_type=rule_type,
    )
