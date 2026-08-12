"""
app/api/v1/ui.py — Server-side rendered UI routes (Jinja2 + HTMX).

Routes
------
GET  /ui/dashboard                    — ダッシュボード
GET  /ui/dashboard/summary-partial    — HTMX: KPI カード部分更新
GET  /ui/dashboard/chart-partial      — HTMX: 価格グラフ部分更新
GET  /ui/dashboard/atl-events-partial — HTMX: 最安値イベント一覧部分更新

GET  /ui/products                     — 商品管理ページ
GET  /ui/products/search              — HTMX: 商品リアルタイム検索
GET  /ui/products/competitors         — HTMX: 競合商品一覧

GET  /ui/rules                        — ルール設定ページ
GET  /ui/rules/list-partial           — HTMX: ルール一覧部分更新

All UI routes require a valid JWT in the `access_token` cookie.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import jwt as _jwt
from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies.db import get_db, get_tenant_db
from app.models.audit_log import AuditLog
from app.models.user import User, UserRole
from app.services import dashboard_service, product_service, rule_service

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


# ── Cookie-based auth helper ──────────────────────────────────────────────────

class _UiUser:
    """Minimal user object passed to all templates."""
    def __init__(self, user_id: uuid.UUID, tenant_id: uuid.UUID, role: str, email: str):
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.role = role
        self.email = email


async def _get_ui_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    access_token: str | None = Cookie(default=None),
) -> _UiUser:
    """Decode JWT from cookie and return a minimal user object. Redirect on failure."""
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": "/ui/login"},
        )
    try:
        payload = _jwt.decode(access_token, settings.secret_key, algorithms=["HS256"])
        user_id = uuid.UUID(payload["sub"])
        tenant_id = uuid.UUID(payload["tenant_id"])
        role = payload["role"]
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": "/ui/login"},
        )

    user: User | None = await db.scalar(
        select(User).where(User.id == user_id)
    )
    email = user.email if user else payload.get("sub", "")
    return _UiUser(user_id=user_id, tenant_id=tenant_id, role=role, email=email)


def _series_to_json(series) -> str:
    """Serialize price series to JSON string for Chart.js."""
    data = []
    for s in series:
        data.append({
            "label": s.label,
            "product_ref_id": str(s.product_ref_id),
            "product_type": s.product_type,
            "data_points": [
                {
                    "captured_at": p.captured_at.isoformat(),
                    "price": p.price,
                    "is_all_time_low": p.is_all_time_low,
                    "source": p.source,
                }
                for p in s.data_points
            ],
        })
    return json.dumps(data, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════════════════════
# Login / Logout
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/login", response_class=HTMLResponse)
async def ui_login_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"active_page": "login", "user": None},
    )


@router.get("/logout")
async def ui_logout():
    response = RedirectResponse(url="/ui/login", status_code=303)
    response.delete_cookie("access_token")
    return response


# ═══════════════════════════════════════════════════════════════════════════════
# Dashboard
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/dashboard", response_class=HTMLResponse)
async def ui_dashboard(
    request: Request,
    ui_user: _UiUser = Depends(_get_ui_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    from app.models.product import MyProduct

    summary = await dashboard_service.get_summary(
        db, tenant_id=ui_user.tenant_id, role=UserRole(ui_user.role)
    )
    chart = await dashboard_service.get_price_history_chart(
        db, tenant_id=ui_user.tenant_id
    )
    products = (
        await db.scalars(
            select(MyProduct).where(
                MyProduct.tenant_id == ui_user.tenant_id,
                MyProduct.is_active.is_(True),
            )
        )
    ).all()

    series_json = _series_to_json(chart.series)
    chart_data_script = f"<script>window.__INITIAL_CHART_DATA__ = {series_json};</script>"

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "active_page": "dashboard",
            "user": ui_user,
            "summary": summary,
            "products": products,
            "chart_data_script": chart_data_script,
        },
    )


@router.get("/dashboard/summary-partial", response_class=HTMLResponse)
async def ui_dashboard_summary_partial(
    request: Request,
    ui_user: _UiUser = Depends(_get_ui_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    summary = await dashboard_service.get_summary(
        db, tenant_id=ui_user.tenant_id, role=UserRole(ui_user.role)
    )
    return templates.TemplateResponse(
        request=request,
        name="partials/summary_cards.html",
        context={"summary": summary},
    )


@router.get("/dashboard/chart-partial", response_class=HTMLResponse)
async def ui_dashboard_chart_partial(
    request: Request,
    product_id: str | None = Query(None),
    days: int = Query(7, ge=1, le=90),
    ui_user: _UiUser = Depends(_get_ui_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    now = datetime.now(timezone.utc)
    from_date = now - timedelta(days=days)
    pid = uuid.UUID(product_id) if product_id else None

    chart = await dashboard_service.get_price_history_chart(
        db,
        tenant_id=ui_user.tenant_id,
        product_id=pid,
        from_date=from_date,
        to_date=now,
    )
    series_json = _series_to_json(chart.series)
    return templates.TemplateResponse(
        request=request,
        name="partials/price_chart.html",
        context={"series_json": series_json},
    )


@router.get("/dashboard/atl-events-partial", response_class=HTMLResponse)
async def ui_dashboard_atl_events_partial(
    request: Request,
    ui_user: _UiUser = Depends(_get_ui_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    events = (
        await db.scalars(
            select(AuditLog)
            .where(
                AuditLog.tenant_id == ui_user.tenant_id,
                AuditLog.action == "ALL_TIME_LOW",
                AuditLog.occurred_at >= cutoff,
            )
            .order_by(AuditLog.occurred_at.desc())
            .limit(20)
        )
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="partials/atl_events.html",
        context={"events": events},
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Products
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/products", response_class=HTMLResponse)
async def ui_products(
    request: Request,
    ui_user: _UiUser = Depends(_get_ui_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    result = await product_service.list_my_products(
        db, ui_user.tenant_id, page=1, page_size=50
    )
    return templates.TemplateResponse(
        request=request,
        name="products.html",
        context={
            "active_page": "products",
            "user": ui_user,
            "products": result["items"],
        },
    )


@router.get("/products/search", response_class=HTMLResponse)
async def ui_products_search(
    request: Request,
    q: str = Query(""),
    ui_user: _UiUser = Depends(_get_ui_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    from app.models.product import MyProduct
    from sqlalchemy import or_

    rows = (
        await db.scalars(
            select(MyProduct)
            .where(
                MyProduct.tenant_id == ui_user.tenant_id,
                MyProduct.is_active.is_(True),
                or_(
                    MyProduct.sku.ilike(f"%{q}%"),
                    MyProduct.name.ilike(f"%{q}%"),
                ) if q else MyProduct.is_active.is_(True),
            )
            .order_by(MyProduct.created_at.desc())
            .limit(50)
        )
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="partials/product_list.html",
        context={
            "products": rows,
            "user": ui_user,
        },
    )


@router.get("/products/competitors", response_class=HTMLResponse)
async def ui_competitors_list(
    request: Request,
    ui_user: _UiUser = Depends(_get_ui_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    from app.models.mapping import ProductMapping
    from app.models.product import CompetitorProduct

    mapped_ids = (
        await db.scalars(
            select(ProductMapping.competitor_product_id)
            .where(ProductMapping.tenant_id == ui_user.tenant_id)
        )
    ).all()
    competitors = (
        await db.scalars(
            select(CompetitorProduct)
            .where(CompetitorProduct.id.in_(mapped_ids))
            .order_by(CompetitorProduct.created_at.desc())
        )
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="partials/competitor_list.html",
        context={"competitors": competitors},
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Rules
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/rules", response_class=HTMLResponse)
async def ui_rules(
    request: Request,
    ui_user: _UiUser = Depends(_get_ui_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    from app.models.product import MyProduct

    products = (
        await db.scalars(
            select(MyProduct).where(
                MyProduct.tenant_id == ui_user.tenant_id,
                MyProduct.is_active.is_(True),
            )
        )
    ).all()
    result = await rule_service.list_rules(
        db, ui_user.tenant_id, page=1, page_size=100
    )
    return templates.TemplateResponse(
        request=request,
        name="rules.html",
        context={
            "active_page": "rules",
            "user": ui_user,
            "products": products,
            "rules": result["items"],
        },
    )


@router.get("/rules/list-partial", response_class=HTMLResponse)
async def ui_rules_list_partial(
    request: Request,
    ui_user: _UiUser = Depends(_get_ui_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    result = await rule_service.list_rules(
        db, ui_user.tenant_id, page=1, page_size=100
    )
    return templates.TemplateResponse(
        request=request,
        name="partials/rule_list.html",
        context={"rules": result["items"]},
    )
