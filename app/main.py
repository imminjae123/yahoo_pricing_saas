"""
app/main.py — FastAPI application entry point.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
_origins = (
    ["*"]
    if not settings.is_production
    else ["https://your-production-domain.example.com"]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
# Placeholder — routers will be added as each feature is implemented.
# from app.api.routers import auth, products, rules
# app.include_router(auth.router,     prefix="/api/v1/auth",     tags=["auth"])
# app.include_router(products.router, prefix="/api/v1/products", tags=["products"])
# app.include_router(rules.router,    prefix="/api/v1/rules",    tags=["rules"])


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
