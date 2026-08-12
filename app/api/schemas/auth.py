"""
Pydantic v2 schemas for Auth endpoints.

POST /api/v1/auth/register   — create tenant + first OWNER user
POST /api/v1/auth/login      — email + password → access + refresh tokens
POST /api/v1/auth/refresh    — rotate access token using refresh token
GET  /api/v1/auth/me         — return current user profile

Security notes
--------------
- hashed_password is NEVER included in any response schema.
- tenant_id is embedded in the JWT payload (not a request body field).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator


# ── Register ──────────────────────────────────────────────────────────────────

class TenantRegisterRequest(BaseModel):
    """POST /api/v1/auth/register"""

    # Tenant info
    shop_name: str = Field(..., min_length=1, max_length=255, examples=["My Yahoo Shopping Store"])
    shop_code: str = Field(..., min_length=1, max_length=100, examples=["my-shop-001"])
    shop_url: str | None = Field(None, max_length=500)
    contact_email: EmailStr

    # First OWNER account
    email: EmailStr
    # SHA-256 pre-hash in auth_service bypasses bcrypt's 72-byte limit.
    # Accept up to 128 characters freely.
    password: str = Field(..., min_length=8, max_length=128)
    full_name: str | None = Field(None, max_length=255)

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        return v


class TenantRegisterResponse(BaseModel):
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    email: EmailStr
    role: str
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


# ── Login ─────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    """POST /api/v1/auth/login"""

    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    """Returned by /login and /refresh"""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="Access token lifetime in seconds")


# ── Refresh ───────────────────────────────────────────────────────────────────

class RefreshRequest(BaseModel):
    """POST /api/v1/auth/refresh"""

    refresh_token: str


# ── Me ────────────────────────────────────────────────────────────────────────

class UserMeResponse(BaseModel):
    """GET /api/v1/auth/me"""

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    email: EmailStr
    full_name: str | None
    role: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Staff Management (OWNER only) ─────────────────────────────────────────────

class StaffInviteRequest(BaseModel):
    """POST /api/v1/auth/users  — OWNER invites a new staff member"""

    email: EmailStr
    full_name: str | None = None
    role: str = Field("VIEWER", pattern="^(MANAGER|VIEWER)$")
    password: str = Field(..., min_length=8, max_length=128)


class StaffResponse(BaseModel):
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    email: EmailStr
    full_name: str | None
    role: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
