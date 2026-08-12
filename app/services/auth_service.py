"""
app/services/auth_service.py — Auth business logic.

Responsibilities
----------------
- Hash / verify passwords with bcrypt (passlib)
- Mint JWT access + refresh tokens (PyJWT HS256)
- register(): create Tenant + OWNER User in one transaction
- login(): verify credentials → tokens
- get_me(): load User row for /me endpoint
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt as _bcrypt
import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.tenant import Tenant
from app.models.user import User, UserRole

# ── Password hashing ─────────────────────────────────────────────────────────
# Use bcrypt directly (bypasses passlib's bcrypt-4.x incompatibility).
# SHA-256 pre-hash → base64 (44 bytes) removes bcrypt's 72-byte input limit.
_BCRYPT_ROUNDS = 12


def _prehash(plain: str) -> bytes:
    """SHA-256(UTF-8 password) → 44-byte base64 — always fits bcrypt's 72-byte limit."""
    digest = hashlib.sha256(plain.encode("utf-8")).digest()
    return base64.b64encode(digest)

# ── JWT config ────────────────────────────────────────────────────────────────
_ALGORITHM = "HS256"
ACCESS_TOKEN_TTL = timedelta(hours=1)
REFRESH_TOKEN_TTL = timedelta(days=30)


def hash_password(plain: str) -> str:
    salt = _bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    return _bcrypt.hashpw(_prehash(plain), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _bcrypt.checkpw(_prehash(plain), hashed.encode("utf-8"))
    except Exception:
        return False


def _make_token(
    subject: str,
    tenant_id: str,
    role: str,
    ttl: timedelta,
    token_type: str,
) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "tenant_id": tenant_id,
        "role": role,
        "type": token_type,
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=_ALGORITHM)


def create_tokens(user_id: uuid.UUID, tenant_id: uuid.UUID, role: str) -> dict[str, str]:
    uid = str(user_id)
    tid = str(tenant_id)
    return {
        "access_token": _make_token(uid, tid, role, ACCESS_TOKEN_TTL, "access"),
        "refresh_token": _make_token(uid, tid, role, REFRESH_TOKEN_TTL, "refresh"),
    }


def decode_refresh_token(token: str) -> dict[str, Any]:
    """Decode and validate a refresh token. Raises jwt.PyJWTError on failure."""
    payload = jwt.decode(token, settings.secret_key, algorithms=[_ALGORITHM])
    if payload.get("type") != "refresh":
        raise jwt.InvalidTokenError("Not a refresh token")
    return payload


# ── register ─────────────────────────────────────────────────────────────────

async def register(
    db: AsyncSession,
    *,
    shop_name: str,
    shop_code: str,
    shop_url: str | None,
    contact_email: str,
    email: str,
    password: str,
    full_name: str | None,
) -> dict[str, Any]:
    """
    Create Tenant + OWNER User atomically.
    Returns token dict + user/tenant ids.
    Raises ValueError for duplicate shop_code or email.
    """
    # Duplicate shop_code check
    exists = await db.scalar(
        select(Tenant.id).where(Tenant.shop_code == shop_code).limit(1)
    )
    if exists:
        raise ValueError(f"shop_code '{shop_code}' already registered")

    tenant = Tenant(
        shop_name=shop_name,
        shop_code=shop_code,
        shop_url=shop_url,
        contact_email=contact_email,
    )
    db.add(tenant)
    await db.flush()  # get tenant.id without committing

    # Duplicate email within tenant (same-tenant uniqueness)
    email_exists = await db.scalar(
        select(User.id)
        .where(User.tenant_id == tenant.id, User.email == email)
        .limit(1)
    )
    if email_exists:
        raise ValueError(f"Email '{email}' already registered")

    user = User(
        tenant_id=tenant.id,
        email=email,
        hashed_password=hash_password(password),
        full_name=full_name,
        role=UserRole.OWNER,
        is_active=True,
    )
    db.add(user)
    await db.flush()  # get user.id

    await db.commit()
    await db.refresh(tenant)
    await db.refresh(user)

    tokens = create_tokens(user.id, tenant.id, user.role)
    return {
        "tenant_id": tenant.id,
        "user_id": user.id,
        "email": user.email,
        "role": user.role,
        **tokens,
    }


# ── login ─────────────────────────────────────────────────────────────────────

async def login(
    db: AsyncSession,
    *,
    email: str,
    password: str,
) -> dict[str, Any]:
    """
    Verify email + password, return tokens.
    Raises ValueError on bad credentials.
    """
    user: User | None = await db.scalar(
        select(User).where(User.email == email, User.is_active.is_(True)).limit(1)
    )
    if user is None or not verify_password(password, user.hashed_password):
        raise ValueError("Invalid email or password")

    tokens = create_tokens(user.id, user.tenant_id, user.role)
    return {
        **tokens,
        "expires_in": int(ACCESS_TOKEN_TTL.total_seconds()),
    }


# ── get_me ────────────────────────────────────────────────────────────────────

async def get_me(db: AsyncSession, user_id: uuid.UUID) -> User:
    """Load full User row. Raises ValueError if not found."""
    user: User | None = await db.scalar(
        select(User).where(User.id == user_id).limit(1)
    )
    if user is None:
        raise ValueError("User not found")
    return user
