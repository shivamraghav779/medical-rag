"""Admin-only endpoints — user role promotion and user listing."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from api.core.auth import AdminUserDep, DbSessionDep
from api.core.exceptions import ValidationException
from api.models.db_models import User
from api.models.schemas import (
    PromoteUserRequest,
    PromoteUserResponse,
    UserListItem,
    UserListResponse,
)

VALID_ROLES = frozenset({"user", "agent", "admin"})

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/users", response_model=UserListResponse)
async def list_users(admin: AdminUserDep, db: DbSessionDep) -> UserListResponse:
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    users = result.scalars().all()
    return UserListResponse(
        users=[
            UserListItem(
                id=u.id,
                email=u.email,
                full_name=u.full_name,
                role=u.role or "user",
                is_active=u.is_active,
            )
            for u in users
        ]
    )


@router.post("/promote", response_model=PromoteUserResponse)
async def promote_user(
    body: PromoteUserRequest,
    admin: AdminUserDep,
    db: DbSessionDep,
) -> PromoteUserResponse:
    role = (body.role or "").strip().lower()
    if role not in VALID_ROLES:
        raise ValidationException(f"role must be one of: {', '.join(sorted(VALID_ROLES))}")

    result = await db.execute(select(User).where(User.id == body.user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise ValidationException(f"User {body.user_id} not found.")

    user.role = role
    await db.commit()

    return PromoteUserResponse(user_id=user.id, email=user.email, role=user.role)
