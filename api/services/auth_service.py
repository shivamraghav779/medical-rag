"""Auth service — registration and login."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.auth import create_access_token, hash_password, verify_password
from api.core.config import Settings
from api.core.exceptions import UnauthorizedException, ValidationException
from api.models.db_models import User


class AuthService:
    async def register(
        self,
        db: AsyncSession,
        settings: Settings,
        *,
        email: str,
        password: str,
        full_name: str = "",
    ) -> tuple[User, str]:
        email_norm = email.strip().lower()
        if len(password) < 8:
            raise ValidationException("Password must be at least 8 characters.")

        existing = await db.execute(select(User).where(User.email == email_norm))
        if existing.scalar_one_or_none():
            raise ValidationException("Email already registered.")

        user = User(
            email=email_norm,
            password_hash=hash_password(password),
            full_name=full_name.strip(),
        )
        db.add(user)
        await db.flush()
        token = create_access_token(user.id, settings)
        return user, token

    async def login(
        self,
        db: AsyncSession,
        settings: Settings,
        *,
        email: str,
        password: str,
    ) -> tuple[User, str]:
        email_norm = email.strip().lower()
        result = await db.execute(select(User).where(User.email == email_norm))
        user = result.scalar_one_or_none()
        if user is None or not verify_password(password, user.password_hash):
            raise UnauthorizedException("Invalid email or password.")
        if not user.is_active:
            raise UnauthorizedException("Account is inactive.")
        token = create_access_token(user.id, settings)
        return user, token

    async def get_usage_totals(self, db: AsyncSession, user_id: str) -> dict:
        from sqlalchemy import func

        from api.models.db_models import TokenUsage

        result = await db.execute(
            select(
                func.coalesce(func.sum(TokenUsage.prompt_tokens), 0),
                func.coalesce(func.sum(TokenUsage.completion_tokens), 0),
                func.coalesce(func.sum(TokenUsage.total_tokens), 0),
                func.count(TokenUsage.id),
            ).where(TokenUsage.user_id == user_id)
        )
        prompt, completion, total, count = result.one()
        return {
            "prompt_tokens": int(prompt),
            "completion_tokens": int(completion),
            "total_tokens": int(total),
            "request_count": int(count),
        }
