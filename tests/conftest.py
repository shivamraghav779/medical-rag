"""Shared test fixtures.

Sets an isolated temp-file SQLite DB via env vars BEFORE any ``api.*`` module
is imported anywhere (including by other conftest/test files at collection
time) — ``api.core.database`` builds its async engine from ``Settings`` at
import time, so this must happen first or tests would run against the real
``data/clinical_rag.db``.

Redis tests run against the real Upstash instance already configured in
``.env`` (per the assignment: "use a real Upstash Redis test instance ... do
not mock the methods being tested") — fixtures use uuid4-suffixed keys and
clean up after themselves so they're safe to run against a shared instance.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

_TEST_DB_DIR = tempfile.mkdtemp(prefix="clinical_rag_test_")
_TEST_DB_PATH = str(Path(_TEST_DB_DIR) / "test.db")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TEST_DB_PATH}"
os.environ["DATABASE_PATH"] = _TEST_DB_PATH

import pytest
import pytest_asyncio

from upstash_redis.asyncio import Redis

from api.core.auth import create_access_token, hash_password
from api.core.config import get_settings
from api.core.database import AsyncSessionLocal, engine, init_db
from api.models.db_models import Conversation, User
from api.services.redis_service import RedisService


def get_redis_client() -> Redis:
    """Build the same Upstash Redis client dependencies.py wires into the app —
    formerly imported from the now-deleted api.tools.redis_tools."""
    settings = get_settings()
    return Redis(url=settings.upstash_redis_rest_url, token=settings.upstash_redis_rest_token)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _init_test_db():
    await init_db()
    # Test-only: several test files each spin up their own asyncio.run() /
    # TestClient event loop against this same temp-file SQLite DB, plus the
    # app's background queue-repair task. SQLite's default busy_timeout is 0,
    # so any overlap between those separate connections throws "database is
    # locked" instead of waiting — WAL + a real busy_timeout makes concurrent
    # readers/writers queue instead of erroring. Scoped to the test DB only;
    # production's database_url is untouched.
    async with engine.begin() as conn:
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        await conn.exec_driver_sql("PRAGMA busy_timeout=5000")
    yield


@pytest.fixture
def redis_service() -> RedisService:
    return RedisService(get_redis_client())


@pytest_asyncio.fixture(autouse=True)
async def isolated_routing_queue():
    """queue_pop_oldest (ZPOPMIN, used by try_route_to_agent /
    try_route_next_available and thus by any test that connects an agent or
    calls take-next) operates on ONE global pending-queue sorted set on the
    real shared Upstash instance. Left un-isolated, a pre-existing real entry
    (e.g. a leftover QUEUED conversation from manual dev testing) can win
    "oldest" over whatever a test just queued, making routing outcomes
    non-deterministic — or, worse, silently swallow a test's auto-route
    entirely (the ghost session doesn't exist in the test's temp DB, so
    assignment fails and the test's own patient is never routed, hanging
    any assertion waiting on a resulting websocket message).
    Autouse for every test: snapshot + drain before, restore exactly after —
    no real queued conversation is ever lost, even for tests that don't
    touch the queue directly."""
    redis = RedisService(get_redis_client())
    existing = await redis.queue_list()
    for entry in existing:
        await redis.queue_remove(entry["session_id"])
    yield
    leftover = await redis.queue_list()
    for entry in leftover:
        await redis.queue_remove(entry["session_id"])
    for entry in existing:
        await redis.queue_add(entry["session_id"], requested_at=entry["requested_at"])


@pytest_asyncio.fixture
async def db_session():
    async with AsyncSessionLocal() as session:
        yield session


def unique_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


async def make_user(db_session, *, role: str = "user", email: str | None = None) -> User:
    """Persists (and commits) a test user so it's visible to other sessions —
    websocket endpoints and background tasks open their own AsyncSessionLocal."""
    user = User(
        email=email or f"{unique_id('user')}@test.local",
        password_hash=hash_password("testpassword123"),
        full_name="Test User",
        role=role,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


def make_token(user_id: str) -> str:
    return create_access_token(user_id, get_settings())


async def make_conversation(
    db_session,
    user_id: str,
    *,
    handoff_state: str = "BOT_ACTIVE",
    assigned_agent_id: str | None = None,
    title: str = "Test conversation",
) -> Conversation:
    conv = Conversation(
        user_id=user_id,
        title=title,
        handoff_state=handoff_state,
        assigned_agent_id=assigned_agent_id,
    )
    db_session.add(conv)
    await db_session.commit()
    await db_session.refresh(conv)
    return conv
