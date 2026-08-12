"""
app/api/v1/auth.py — Auth router.

Endpoints
---------
POST /api/v1/auth/register  — create Tenant + OWNER user, return tokens
POST /api/v1/auth/login     — email/password → tokens
POST /api/v1/auth/refresh   — rotate access token via refresh token
GET  /api/v1/auth/me        — current user profile (JWT required)
"""

from __future__ import annotations

import uuid

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    TenantRegisterRequest,
    TenantRegisterResponse,
    TokenResponse,
    UserMeResponse,
)
from app.dependencies.auth import CurrentUser, get_current_user
from app.dependencies.db import get_db
from app.services import auth_service

router = APIRouter()


# ── POST /register ────────────────────────────────────────────────────────────

@router.post(
    "/register",
    response_model=TenantRegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="テナント登録 + OWNERアカウント作成",
)
async def register(
    body: TenantRegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> TenantRegisterResponse:
    try:
        result = await auth_service.register(
            db,
            shop_name=body.shop_name,
            shop_code=body.shop_code,
            shop_url=body.shop_url,
            contact_email=str(body.contact_email),
            email=str(body.email),
            password=body.password,
            full_name=body.full_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

    return TenantRegisterResponse(**result)


# ── POST /login ───────────────────────────────────────────────────────────────

@router.post(
    "/login",
    response_model=TokenResponse,
    summary="ログイン → JWTトークン発行",
)
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    try:
        result = await auth_service.login(
            db,
            email=str(body.email),
            password=body.password,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        )

    return TokenResponse(**result)


# ── POST /refresh ─────────────────────────────────────────────────────────────

@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="リフレッシュトークン → 新しいアクセストークン発行",
)
async def refresh(body: RefreshRequest) -> TokenResponse:
    try:
        payload = auth_service.decode_refresh_token(body.refresh_token)
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid refresh token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    tokens = auth_service.create_tokens(
        user_id=uuid.UUID(payload["sub"]),
        tenant_id=uuid.UUID(payload["tenant_id"]),
        role=payload["role"],
    )
    return TokenResponse(
        **tokens,
        expires_in=int(auth_service.ACCESS_TOKEN_TTL.total_seconds()),
    )


# ── GET /me ───────────────────────────────────────────────────────────────────

@router.get(
    "/me",
    response_model=UserMeResponse,
    summary="現在のログインユーザー情報取得",
)
async def me(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserMeResponse:
    try:
        user = await auth_service.get_me(db, current_user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    return UserMeResponse(
        user_id=user.id,
        tenant_id=user.tenant_id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        created_at=user.created_at,
    )
