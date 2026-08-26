"""Auth router — register, login, profile, token usage."""

from __future__ import annotations

from fastapi import APIRouter

from api.core.auth import CurrentUserDep, DbSessionDep
from api.core.dependencies import SettingsDep
from api.models.schemas import (
    AuthResponse,
    TokenUsageSummary,
    UserLoginRequest,
    UserRegisterRequest,
    UserResponse,
)
from api.services.auth_service import AuthService

auth_service = AuthService()


def _user_response(user) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=getattr(user, "role", None) or "user",
    )


class AuthRouter:
    def __init__(self) -> None:
        self.router = APIRouter(prefix="/api/auth", tags=["auth"])
        self.register(self.router)

    def register(self, router: APIRouter) -> None:
        @router.post("/register", response_model=AuthResponse)
        async def register(
            body: UserRegisterRequest,
            db: DbSessionDep,
            settings: SettingsDep,
        ) -> AuthResponse:
            user, token = await auth_service.register(
                db,
                settings,
                email=body.email,
                password=body.password,
                full_name=body.full_name,
            )
            await db.commit()
            return AuthResponse(
                access_token=token,
                user=_user_response(user),
            )

        @router.post("/login", response_model=AuthResponse)
        async def login(
            body: UserLoginRequest,
            db: DbSessionDep,
            settings: SettingsDep,
        ) -> AuthResponse:
            user, token = await auth_service.login(
                db,
                settings,
                email=body.email,
                password=body.password,
            )
            await db.commit()
            return AuthResponse(
                access_token=token,
                user=_user_response(user),
            )

        @router.get("/me", response_model=UserResponse)
        async def me(user: CurrentUserDep) -> UserResponse:
            return _user_response(user)

        @router.get("/usage", response_model=TokenUsageSummary)
        async def usage(user: CurrentUserDep, db: DbSessionDep) -> TokenUsageSummary:
            totals = await auth_service.get_usage_totals(db, user.id)
            return TokenUsageSummary(**totals)


auth_router = AuthRouter()
router = auth_router.router
