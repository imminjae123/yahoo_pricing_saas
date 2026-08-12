"""
app/dependencies/db.py — async SQLAlchemy session with RLS tenant isolation.

Each request session runs:
    SET LOCAL app.current_tenant_id = '<uuid>'
immediately after BEGIN so PostgreSQL RLS policies can read it via
    current_setting('app.current_tenant_id')

Usage
-----
    from app.dependencies.db import get_db
    from app.dependencies.auth import get_current_user, CurrentUser
    from sqlalchemy.ext.asyncio import AsyncSession

    @router.get("/items")
    async def list_items(
        db: AsyncSession = Depends(get_db),
        current_user: CurrentUser = Depends(get_current_user),
    ):
        ...

    # To combine RLS + auth in one dependency use get_tenant_db:
    @router.get("/items")
    async def list_items(db: AsyncSession = Depends(get_tenant_db)):
        ...
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated

import sqlalchemy as sa
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.dependencies.auth import CurrentUser, get_current_user

# ── Engine (module-level singleton) ──────────────────────────────────────────
engine = create_async_engine(
    settings.database_url,
    echo=settings.app_env == "development",
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


# ── Plain session (no RLS) ────────────────────────────────────────────────────
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield a raw async session — caller is responsible for RLS setup if needed."""
    async with AsyncSessionLocal() as session:
        yield session


# ── Tenant-scoped session (RLS applied) ──────────────────────────────────────
async def _get_tenant_db(
    session: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> AsyncGenerator[AsyncSession, None]:
    """
    Yield an async session with SET LOCAL app.current_tenant_id applied.
    Use this dependency on every endpoint that touches tenant-scoped tables.
    """
    await session.execute(
        sa.text("SET LOCAL app.current_tenant_id = :tid"),
        {"tid": str(current_user.tenant_id)},
    )
    yield session


# Public alias
get_tenant_db = _get_tenant_db
