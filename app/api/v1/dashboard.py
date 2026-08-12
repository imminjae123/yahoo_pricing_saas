"""
app/api/v1/dashboard.py — Dashboard router.

Endpoints
---------
GET /api/v1/dashboard/summary        — tenant KPI summary (all roles)
GET /api/v1/dashboard/price-history  — price chart data (all roles)
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.dashboard import (
    DashboardSummaryResponse,
    PriceHistoryChartResponse,
)
from app.dependencies.auth import CurrentUser, get_current_user
from app.dependencies.db import get_tenant_db
from app.services import dashboard_service

router = APIRouter()


# ── GET /summary ──────────────────────────────────────────────────────────────

@router.get(
    "/summary",
    response_model=DashboardSummaryResponse,
    summary="テナントKPIサマリー取得 (全ロール)",
)
async def get_summary(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> DashboardSummaryResponse:
    try:
        return await dashboard_service.get_summary(
            db,
            tenant_id=current_user.tenant_id,
            role=current_user.role,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate summary: {exc}",
        )


# ── GET /price-history ────────────────────────────────────────────────────────

@router.get(
    "/price-history",
    response_model=PriceHistoryChartResponse,
    summary="価格変動履歴チャートデータ取得 (全ロール)",
)
async def get_price_history(
    product_id: uuid.UUID | None = Query(
        None,
        description="自社商品ID。指定するとその商品+競合マッピング全系列を返す",
    ),
    from_date: datetime | None = Query(
        None,
        description="集計開始日時 (ISO8601)。省略時は7日前",
    ),
    to_date: datetime | None = Query(
        None,
        description="集計終了日時 (ISO8601)。省略時は現在",
    ),
    limit: int = Query(
        200,
        ge=1,
        le=500,
        description="1系列あたり最大データ点数",
    ),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> PriceHistoryChartResponse:
    try:
        return await dashboard_service.get_price_history_chart(
            db,
            tenant_id=current_user.tenant_id,
            product_id=product_id,
            from_date=from_date,
            to_date=to_date,
            limit=limit,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch price history: {exc}",
        )
