"""Async SQLAlchemy database setup."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from api.core.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
_db_path = Path(settings.database_path)
try:
    _db_path.parent.mkdir(parents=True, exist_ok=True)
except OSError:
    # Serverless read-only FS except /tmp — Settings already remaps path there.
    pass

engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


def _ensure_sqlite_columns(connection) -> None:
    """Additive migrations for existing SQLite databases (sync, for run_sync)."""

    def _cols(table: str) -> set[str]:
        rows = connection.execute(text(f"PRAGMA table_info({table})")).fetchall()
        return {row[1] for row in rows}

    user_cols = _cols("users")
    if "role" not in user_cols:
        connection.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR(16) DEFAULT 'user'"))

    conv_cols = _cols("conversations")
    if "handoff_state" not in conv_cols:
        connection.execute(
            text(
                "ALTER TABLE conversations ADD COLUMN handoff_state VARCHAR(32) DEFAULT 'BOT_ACTIVE'"
            )
        )
    if "handoff_reason" not in conv_cols:
        connection.execute(
            text("ALTER TABLE conversations ADD COLUMN handoff_reason VARCHAR(128)")
        )
    if "assigned_agent_id" not in conv_cols:
        connection.execute(
            text("ALTER TABLE conversations ADD COLUMN assigned_agent_id VARCHAR(36)")
        )
    if "handoff_resolution_json" not in conv_cols:
        connection.execute(
            text("ALTER TABLE conversations ADD COLUMN handoff_resolution_json TEXT")
        )


async def init_db() -> None:
    from api.models import db_models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_sqlite_columns)
