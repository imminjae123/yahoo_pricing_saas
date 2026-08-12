"""
app/dependencies/auth.py — JWT authentication dependency.

Usage
-----
    from app.dependencies.auth import get_current_user, require_roles, CurrentUser

    @router.get("/me")
    async def me(current_user: CurrentUser = Depends(get_current_user)):
        ...

    @router.delete("/{id}")
    async def delete(
        current_user: CurrentUser = Depends(require_roles("OWNER", "MANAGER"))
    ):
        ...
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Callable

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.models.user import UserRole

_ALGORITHM = "HS256"
_bearer = HTTPBearer(auto_error=True)


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """Decoded JWT principal attached to every authenticated request."""

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    role: UserRole


def _decode_token(token: str) -> dict:
    try:
        return jwt.decode(
            token,
            settings.secret_key,
            algorithms=[_ALGORITHM],
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> CurrentUser:
    payload = _decode_token(credentials.credentials)
    try:
        return CurrentUser(
            user_id=uuid.UUID(payload["sub"]),
            tenant_id=uuid.UUID(payload["tenant_id"]),
            role=UserRole(payload["role"]),
        )
    except (KeyError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed token payload",
            headers={"WWW-Authenticate": "Bearer"},
        )


def require_roles(*roles: str) -> Callable:
    """Return a dependency that enforces one of the given roles."""
    allowed = {UserRole(r) for r in roles}

    async def _check(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current_user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user

    return _check
