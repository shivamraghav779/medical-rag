"""JWT authentication utilities and FastAPI dependencies."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional

import bcrypt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import Settings, get_settings
from api.core.database import get_db_session
from api.core.exceptions import ForbiddenException, UnauthorizedException
from api.models.db_models import User

bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(user_id: str, settings: Settings) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": user_id, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str, settings: Settings) -> str:
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        user_id = payload.get("sub")
        if not user_id:
            raise UnauthorizedException("Invalid token payload.")
        return str(user_id)
    except JWTError as exc:
        raise UnauthorizedException("Invalid or expired token.") from exc


async def get_current_user(
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> User:
    settings = get_settings()
    if credentials is None or not credentials.credentials:
        raise UnauthorizedException("Authentication required.")
    user_id = decode_token(credentials.credentials, settings)
    result = await db.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))
    user = result.scalar_one_or_none()
    if user is None:
        raise UnauthorizedException("User not found or inactive.")
    return user


async def get_optional_user(
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Optional[User]:
    if credentials is None or not credentials.credentials:
        return None
    try:
        return await get_current_user(credentials, db)
    except UnauthorizedException:
        return None


CurrentUserDep = Annotated[User, Depends(get_current_user)]
OptionalUserDep = Annotated[Optional[User], Depends(get_optional_user)]
DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]


async def require_agent_or_admin(user: CurrentUserDep) -> User:
    # 403, not 401: the token is valid and the user is who they say they
    # are — they just don't have the role for this endpoint. A 401 here
    # made the frontend's generic "session expired" interceptor force-log
    # out any patient who happened to hit an agent-only endpoint (Issue:
    # Live Queue tab logging out non-agent users).
    if (user.role or "user") not in ("agent", "admin"):
        raise ForbiddenException("Agent or admin role required.")
    return user


async def require_admin(user: CurrentUserDep) -> User:
    if (user.role or "user") != "admin":
        raise ForbiddenException("Admin role required.")
    return user


AgentUserDep = Annotated[User, Depends(require_agent_or_admin)]
AdminUserDep = Annotated[User, Depends(require_admin)]
