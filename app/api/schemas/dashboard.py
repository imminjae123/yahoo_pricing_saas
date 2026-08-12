"""
app/api/schemas/dashboard.py — Pydantic v2 schemas for dashboard endpoints.

GET /api/v1/dashboard/summary        — tenant-level KPI summary
GET /api/v1/dashboard/price-history  — price chart data per product
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── Summary ───────────────────────────────────────────────────────────────────

class ProductSummaryItem(BaseModel):
    """Per-product mini-stats embedded in the summary response."""
    product_id: uuid.UUID
    sku: str
    name: str
    current_price: float | None
    defensive_price: float
    competitor_count: int
    all_time_low_count: int      # times we hit a new all-time low this period
    last_crawled_at: datetime | None


class DashboardSummaryResponse(BaseModel):
    """
    GET /api/v1/dashboard/summary

    High-level KPIs for the tenant dashboard landing page.
    VIEWER role: cost / defensive_price fields are omitted (set to None).
    """
    tenant_id: uuid.UUID

    # ── Product counts ────────────────────────────────────────────────────────
    total_my_products: int          = Field(..., description="Active self products")
    total_competitor_products: int  = Field(..., description="Mapped competitor items")
    total_mappings: int             = Field(..., description="Total product mappings")

    # ── Rule stats ────────────────────────────────────────────────────────────
    total_active_rules: int         = Field(..., description="Active pricing rules")

    # ── Crawl activity (last 24 h) ────────────────────────────────────────────
    crawls_last_24h: int            = Field(..., description="PriceHistory rows inserted in last 24h")
    all_time_lows_last_24h: int     = Field(..., description="New all-time-low events in last 24h")

    # ── Price stats ───────────────────────────────────────────────────────────
    cheapest_competitor_price: int | None  = Field(None, description="Global minimum across mapped items")
    avg_competitor_price: float | None     = Field(None, description="Average price across last snapshot per item")

    # ── Per-product breakdown ─────────────────────────────────────────────────
    products: list[ProductSummaryItem]

    generated_at: datetime


# ── Price History Chart ───────────────────────────────────────────────────────

class PriceDataPoint(BaseModel):
    """Single (timestamp, price) data point for chart rendering."""
    captured_at: datetime
    price: int
    is_all_time_low: bool
    source: str   # CRAWL | RULE | MANUAL


class ProductPriceSeriesResponse(BaseModel):
    """
    Price series for one product (MY or COMPETITOR).
    Designed for Chart.js / ECharts line chart consumption.
    """
    product_ref_id: uuid.UUID
    product_type: str           # MY | COMPETITOR
    label: str                  # product name or item_name
    data_points: list[PriceDataPoint]
    min_price: int | None
    max_price: int | None
    latest_price: int | None
    all_time_low: int | None
    total_points: int


class PriceHistoryChartResponse(BaseModel):
    """
    GET /api/v1/dashboard/price-history

    Returns one or more price series suitable for multi-line chart rendering.
    When product_id is provided: series for that product + its mapped competitors.
    When omitted: latest snapshot per product for all active products.
    """
    tenant_id: uuid.UUID
    series: list[ProductPriceSeriesResponse]
    from_date: datetime
    to_date: datetime
    generated_at: datetime
