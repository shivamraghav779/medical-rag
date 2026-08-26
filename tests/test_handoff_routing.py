"""HandoffService routing tests — distributed lock, race safety, and the
context-load-failure-must-not-undo-committed-state guarantee (Issues 2, 13,
15, 16). Runs against the real Upstash instance + a temp SQLite DB
(conftest), matching the rest of this suite.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from api.core.database import AsyncSessionLocal
from api.services.handoff_service import STATE_HUMAN_ACTIVE, STATE_QUEUED, HandoffService
from tests.conftest import make_conversation, make_user, unique_id


@pytest.fixture
def redis(redis_service):
    return redis_service


@pytest.fixture
def service(redis):
    return HandoffService(redis)


async def _cleanup_agent(redis, agent_id: str) -> None:
    sessions = await redis.get_agent_active_sessions(agent_id)
    for sid in sessions:
        await redis.remove_agent_active_session(agent_id, sid)
    await redis.agent_set_offline(agent_id)


async def test_take_next_acquires_and_releases_lock(service, redis, db_session):
    patient = await make_user(db_session, role="user")
    agent = await make_user(db_session, role="agent")
    conv = await make_conversation(db_session, patient.id, handoff_state=STATE_QUEUED)
    await redis.queue_add(conv.id)
    await redis.agent_set_online(agent.id)

    try:
        # Lock must be free before and after a routing call — try_route_to_agent
        # acquires lock:queue_routing, dequeues, assigns, then releases.
        pre_token = await redis.acquire_routing_lock(wait_seconds=0.2)
        assert pre_token is not None
        await redis.release_routing_lock(pre_token)

        result = await service.try_route_to_agent(agent.id)
        assert result is not None
        assert result["session_id"] == conv.id

        post_token = await redis.acquire_routing_lock(wait_seconds=1.0)
        assert post_token is not None, "lock must be released after routing completes"
        await redis.release_routing_lock(post_token)
    finally:
        await redis.queue_remove(conv.id)
        await _cleanup_agent(redis, agent.id)


async def test_concurrent_take_next_does_not_double_assign(service, redis, db_session):
    """Two agents calling take-next concurrently for a single waiting patient
    must result in exactly one assignment, not two (Issue 13)."""
    patient = await make_user(db_session, role="user")
    agent_a = await make_user(db_session, role="agent")
    agent_b = await make_user(db_session, role="agent")
    conv = await make_conversation(db_session, patient.id, handoff_state=STATE_QUEUED)
    await redis.queue_add(conv.id)
    await redis.agent_set_online(agent_a.id)
    await redis.agent_set_online(agent_b.id)

    try:
        results = await asyncio.gather(
            service.try_route_to_agent(agent_a.id),
            service.try_route_to_agent(agent_b.id),
            return_exceptions=True,
        )
        assigned = [r for r in results if isinstance(r, dict) and r]
        assert len(assigned) == 1, f"expected exactly one assignment, got {results}"

        winner = assigned[0]["agent_id"]
        loser = agent_b.id if winner == agent_a.id else agent_a.id
        assert await redis.validate_agent_session(winner, conv.id) is True
        assert await redis.get_agent_active_count(loser) == 0
    finally:
        await redis.queue_remove(conv.id)
        await _cleanup_agent(redis, agent_a.id)
        await _cleanup_agent(redis, agent_b.id)


async def test_concurrent_take_next_same_agent_only_fills_once(service, redis, db_session):
    """The same agent double-clicking Take Next (or auto-route racing an
    explicit click) must not land two sessions in one call each — only one
    patient was waiting, so only one assignment can succeed."""
    patient = await make_user(db_session, role="user")
    agent = await make_user(db_session, role="agent")
    conv = await make_conversation(db_session, patient.id, handoff_state=STATE_QUEUED)
    await redis.queue_add(conv.id)
    await redis.agent_set_online(agent.id)

    try:
        results = await asyncio.gather(
            service.try_route_to_agent(agent.id),
            service.try_route_to_agent(agent.id),
            return_exceptions=True,
        )
        assigned = [r for r in results if isinstance(r, dict) and r]
        assert len(assigned) == 1
        assert await redis.get_agent_active_count(agent.id) == 1
    finally:
        await redis.queue_remove(conv.id)
        await _cleanup_agent(redis, agent.id)


async def test_failed_context_load_does_not_requeue_committed_session(service, redis, db_session):
    """Issue 2/16: once HUMAN_ACTIVE is committed, a context-load failure
    must NOT roll it back or requeue the patient — the assignment stands and
    context is reconstructed lazily on next access."""
    patient = await make_user(db_session, role="user")
    agent = await make_user(db_session, role="agent")
    conv = await make_conversation(db_session, patient.id, handoff_state=STATE_QUEUED)
    await redis.queue_add(conv.id)
    await redis.agent_set_online(agent.id)

    try:
        with patch.object(
            HandoffService,
            "_load_assignment_context",
            new=AsyncMock(side_effect=RuntimeError("boom: context load failed")),
        ):
            result = await service.try_route_to_agent(agent.id)

        assert result is not None, "assignment must still succeed despite context-load failure"
        assert result["session_id"] == conv.id

        # State must be committed as HUMAN_ACTIVE — not rolled back.
        async with AsyncSessionLocal() as check_db:
            from sqlalchemy import select

            from api.models.db_models import Conversation

            row = await check_db.execute(select(Conversation).where(Conversation.id == conv.id))
            refreshed = row.scalar_one()
            assert refreshed.handoff_state == STATE_HUMAN_ACTIVE
            assert refreshed.assigned_agent_id == agent.id

        # And it must NOT have been put back in the pending queue (no split-brain).
        assert await redis.queue_position(conv.id) is None
        assert await redis.validate_agent_session(agent.id, conv.id) is True
    finally:
        await redis.queue_remove(conv.id)
        await _cleanup_agent(redis, agent.id)


async def test_disconnect_with_active_sessions_requeues_all(service, redis, db_session):
    """Issue 15: when an agent disconnects past the grace period, every
    session they held HUMAN_ACTIVE must be requeued (back to QUEUED, back in
    the pending sorted set)."""
    agent = await make_user(db_session, role="agent")
    patients = [await make_user(db_session, role="user") for _ in range(2)]
    convs = [
        await make_conversation(db_session, p.id, handoff_state=STATE_HUMAN_ACTIVE, assigned_agent_id=agent.id)
        for p in patients
    ]
    await redis.agent_set_online(agent.id)
    for conv in convs:
        await redis.add_agent_active_session(agent.id, conv.id)

    try:
        # requeue_on_agent_disconnect checks connection_manager first — with
        # no socket registered for this agent_id, it proceeds as a real
        # disconnect (the connection_manager import here is the shared
        # singleton also used by the websocket router).
        await service.requeue_on_agent_disconnect(agent.id)

        for conv in convs:
            assert await redis.queue_position(conv.id) is not None, "session must be back in queue"
            assert await redis.validate_agent_session(agent.id, conv.id) is False

            async with AsyncSessionLocal() as check_db:
                from sqlalchemy import select

                from api.models.db_models import Conversation

                row = await check_db.execute(select(Conversation).where(Conversation.id == conv.id))
                refreshed = row.scalar_one()
                assert refreshed.handoff_state == STATE_QUEUED
                assert refreshed.assigned_agent_id is None

        assert agent.id not in await redis.agent_list_online()
    finally:
        for conv in convs:
            await redis.queue_remove(conv.id)
        await _cleanup_agent(redis, agent.id)


async def test_try_route_next_available_skips_when_no_agents(service, redis):
    result = await service.try_route_next_available()
    assert result is None


async def test_try_route_to_agent_returns_none_for_full_agent(service, redis, db_session):
    from api.core import redis_keys as keys

    agent = await make_user(db_session, role="agent")
    await redis.agent_set_online(agent.id)
    filler_sessions = [unique_id("filler") for _ in range(keys.AGENT_MAX_ACTIVE_SESSIONS)]
    for sid in filler_sessions:
        await redis.add_agent_active_session(agent.id, sid)

    try:
        result = await service.try_route_to_agent(agent.id)
        assert result is None
    finally:
        await _cleanup_agent(redis, agent.id)
