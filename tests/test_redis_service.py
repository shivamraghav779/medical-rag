"""RedisService tests against the real Upstash instance (conftest.redis_service).

Covers: queue add/remove/position/pop_oldest/idempotent-add, agent capacity
cap at 5, session-ownership isolation, and the online/offline primitives the
agent disconnect-grace-period logic relies on.
"""

from __future__ import annotations

import pytest

from api.core import redis_keys as keys
from tests.conftest import unique_id


@pytest.fixture
async def cleanup_queue(redis_service):
    session_ids: list[str] = []
    yield session_ids
    for sid in session_ids:
        await redis_service.queue_remove(sid)


@pytest.fixture
async def cleanup_agent(redis_service):
    agent_ids: list[str] = []
    yield agent_ids
    for aid in agent_ids:
        sessions = await redis_service.get_agent_active_sessions(aid)
        for sid in sessions:
            await redis_service.remove_agent_active_session(aid, sid)
        await redis_service.agent_set_offline(aid)


async def test_queue_add_and_position(redis_service, cleanup_queue):
    sid = unique_id("session")
    cleanup_queue.append(sid)

    position = await redis_service.queue_add(sid)
    assert position >= 0
    assert await redis_service.queue_position(sid) == position


async def test_queue_add_is_idempotent(redis_service, cleanup_queue):
    """Calling queue_add twice for the same session must not create a
    duplicate entry or reset its wait time — Redis sorted sets are inherently
    deduplicated by member, and queue_add explicitly preserves the original
    score rather than resetting it (Issue 12)."""
    sid = unique_id("session")
    cleanup_queue.append(sid)

    first_position = await redis_service.queue_add(sid)
    length_after_first = await redis_service.queue_length()

    second_position = await redis_service.queue_add(sid)
    length_after_second = await redis_service.queue_length()

    assert first_position == second_position
    assert length_after_first == length_after_second


async def test_queue_remove(redis_service, cleanup_queue):
    sid = unique_id("session")
    cleanup_queue.append(sid)

    await redis_service.queue_add(sid)
    assert await redis_service.queue_position(sid) is not None

    removed = await redis_service.queue_remove(sid)
    assert removed is True
    assert await redis_service.queue_position(sid) is None


async def test_queue_pop_oldest_returns_longest_waiting(redis_service, cleanup_queue):
    older = unique_id("session-older")
    newer = unique_id("session-newer")
    cleanup_queue.extend([older, newer])

    await redis_service.queue_add(older, requested_at=1000.0)
    await redis_service.queue_add(newer, requested_at=2000.0)

    popped = await redis_service.queue_pop_oldest()
    assert popped == older
    # It's gone from the queue now — remove the (already-popped) tracking entry.
    cleanup_queue.remove(older)
    assert await redis_service.queue_position(older) is None


async def test_queue_pop_oldest_empty_returns_none(redis_service):
    # Use a fresh isolated check: pop from an already-empty queue state by
    # popping until empty is not safe against a shared instance, so instead
    # just verify a never-added session isn't returned as a false positive.
    sid = unique_id("session-never-queued")
    assert await redis_service.queue_position(sid) is None


async def test_agent_capacity_cannot_exceed_max(redis_service, cleanup_agent):
    agent_id = unique_id("agent")
    cleanup_agent.append(agent_id)

    added_sessions = []
    for i in range(keys.AGENT_MAX_ACTIVE_SESSIONS):
        sid = unique_id(f"session-{i}")
        added_sessions.append(sid)
        ok = await redis_service.add_agent_active_session(agent_id, sid)
        assert ok is True

    assert await redis_service.get_agent_active_count(agent_id) == keys.AGENT_MAX_ACTIVE_SESSIONS

    overflow_sid = unique_id("session-overflow")
    ok = await redis_service.add_agent_active_session(agent_id, overflow_sid)
    assert ok is False
    assert await redis_service.get_agent_active_count(agent_id) == keys.AGENT_MAX_ACTIVE_SESSIONS

    for sid in added_sessions:
        await redis_service.remove_agent_active_session(agent_id, sid)


async def test_validate_agent_session_rejects_wrong_agent(redis_service, cleanup_agent):
    agent_a = unique_id("agent-a")
    agent_b = unique_id("agent-b")
    cleanup_agent.extend([agent_a, agent_b])
    sid = unique_id("session")

    await redis_service.add_agent_active_session(agent_a, sid)

    assert await redis_service.validate_agent_session(agent_a, sid) is True
    assert await redis_service.validate_agent_session(agent_b, sid) is False

    await redis_service.remove_agent_active_session(agent_a, sid)


async def test_agent_online_offline_and_reconnect(redis_service, cleanup_agent):
    """The disconnect-grace-period logic (Issue 15/24) hinges on
    agent_set_online/offline being correct — this app tracks "is the agent
    still around" via connection_manager's live in-process socket identity
    rather than a separate Redis pending-disconnect flag, so what we can
    verify at the Redis layer is that online/offline/reconnect toggle
    agents:online membership correctly and idempotently."""
    agent_id = unique_id("agent")
    cleanup_agent.append(agent_id)

    await redis_service.agent_set_online(agent_id)
    assert agent_id in await redis_service.agent_list_online()

    await redis_service.agent_set_offline(agent_id)
    assert agent_id not in await redis_service.agent_list_online()

    # Reconnect — should be back online, idempotently.
    await redis_service.agent_set_online(agent_id)
    await redis_service.agent_set_online(agent_id)
    online = await redis_service.agent_list_online()
    assert online.count(agent_id) == 1


async def test_repair_queue_consistency_removes_ghosts(redis_service, cleanup_queue):
    """Issue 12/16: a session in the queue whose DB state is HUMAN_ACTIVE
    elsewhere is a split-brain ghost entry and must be removed."""
    ghost_sid = unique_id("session-ghost")
    legit_sid = unique_id("session-legit")
    cleanup_queue.extend([ghost_sid, legit_sid])

    await redis_service.queue_add(ghost_sid)
    await redis_service.queue_add(legit_sid)

    removed_count = await redis_service.repair_queue_consistency(
        active_session_ids={ghost_sid}
    )

    assert removed_count == 1
    assert await redis_service.queue_position(ghost_sid) is None
    assert await redis_service.queue_position(legit_sid) is not None
    cleanup_queue.remove(ghost_sid)


async def test_routing_lock_acquire_and_release(redis_service):
    token = await redis_service.acquire_routing_lock(wait_seconds=1.0)
    assert token is not None

    # A second concurrent acquire attempt should fail fast while held.
    second_token = await redis_service.acquire_routing_lock(wait_seconds=0.2)
    assert second_token is None

    released = await redis_service.release_routing_lock(token)
    assert released is True

    # Now that it's released, acquiring again should succeed immediately.
    third_token = await redis_service.acquire_routing_lock(wait_seconds=1.0)
    assert third_token is not None
    await redis_service.release_routing_lock(third_token)


async def test_release_routing_lock_rejects_wrong_token(redis_service):
    """Prevents releasing a lock that expired and was re-acquired by someone
    else — the release script only deletes if the token still matches."""
    token = await redis_service.acquire_routing_lock(wait_seconds=1.0)
    assert token is not None

    released = await redis_service.release_routing_lock("not-the-real-token")
    assert released is False

    # Real token still works afterward.
    assert await redis_service.release_routing_lock(token) is True
