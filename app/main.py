"""
app/main.py — FastAPI application entry point.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import auth as auth_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.products import competitor_router, router as products_router
from app.api.v1.rules import router as rules_router
from app.api.v1.ui import router as ui_router
from app.config import settings
from app.dependencies.db import engine
from app.models import Base


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── startup ──────────────────────────────────────────────────────────────
    # For production use Alembic migrations; this creates tables only in dev.
    if not settings.is_production:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    yield
    # ── shutdown ─────────────────────────────────────────────────────────────
    await engine.dispose()


app = FastAPI(
    title="Yahoo! Pricing SaaS",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# ── CORS ─────────────────────────────────────────────────────────────────────
# allow_credentials=True is incompatible with allow_origins=["*"].
# In dev: use explicit localhost origins so Swagger UI works correctly.
if not settings.is_production:
    _origins = [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    _credentials = True
else:
    _origins = ["https://your-production-domain.example.com"]
    _credentials = True

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth_router.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(products_router, prefix="/api/v1/products", tags=["products"])
app.include_router(competitor_router, prefix="/api/v1/competitors", tags=["competitors"])
app.include_router(rules_router, prefix="/api/v1/rules", tags=["rules"])
app.include_router(dashboard_router, prefix="/api/v1/dashboard", tags=["dashboard"])
app.include_router(ui_router, prefix="/ui", tags=["ui"])


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ── Debug: confirm routes at import time ──────────────────────────────────────
# (Removed in production — routes are confirmed via /api/docs)
