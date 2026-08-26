"""Queue/state consistency tests — idempotent adds, HUMAN_ACTIVE sessions
never sitting in the pending queue, the ghost-entry repair function, and
queue position updates as sessions are dequeued (Issues 11, 12, 16).
"""

from __future__ import annotations

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


@pytest.fixture
async def cleanup_queue(redis):
    session_ids: list[str] = []
    yield session_ids
    for sid in session_ids:
        await redis.queue_remove(sid)


async def test_duplicate_queue_add_is_idempotent(redis, cleanup_queue):
    sid = unique_id("session")
    cleanup_queue.append(sid)

    await redis.queue_add(sid)
    await redis.queue_add(sid)
    await redis.queue_add(sid)

    entries = await redis.queue_list()
    matching = [e for e in entries if e["session_id"] == sid]
    assert len(matching) == 1, "duplicate adds must not create duplicate entries"


async def test_duplicate_add_preserves_original_wait_time(redis, cleanup_queue):
    sid = unique_id("session")
    cleanup_queue.append(sid)

    first_position = await redis.queue_add(sid, requested_at=1000.0)
    # A later "requeue" with no explicit timestamp must not reset the score —
    # otherwise a patient who gets bumped back to the queue would lose their
    # place even though they've already been waiting.
    second_position = await redis.queue_add(sid)

    assert first_position == second_position
    assert await redis.queue_score(sid) == 1000.0


async def test_human_active_session_removed_from_queue_on_resolve(service, redis, db_session):
    """A resolved (or otherwise no-longer-QUEUED) session must never
    remain visible in the pending queue."""
    patient = await make_user(db_session, role="user")
    agent = await make_user(db_session, role="agent")
    conv = await make_conversation(
        db_session, patient.id, handoff_state=STATE_HUMAN_ACTIVE, assigned_agent_id=agent.id
    )
    await redis.agent_set_online(agent.id)
    await redis.add_agent_active_session(agent.id, conv.id)

    try:
        await service.resolve(
            db_session,
            conv.id,
            agent_id=agent.id,
            tag="other",
            end_reason="issue_resolved",
            issue_status="resolved",
        )
        assert await redis.queue_position(conv.id) is None
        assert await redis.validate_agent_session(agent.id, conv.id) is False
    finally:
        for sid in await redis.get_agent_active_sessions(agent.id):
            await redis.remove_agent_active_session(agent.id, sid)
        await redis.agent_set_offline(agent.id)


async def test_repair_function_removes_ghost_entries_only(service, redis, cleanup_queue, db_session):
    """repair_queue_ghosts (Issue 12/16) must remove only sessions whose DB
    state is actually HUMAN_ACTIVE elsewhere, leaving legitimately-QUEUED
    sessions untouched."""
    patient_a = await make_user(db_session, role="user")
    patient_b = await make_user(db_session, role="user")
    agent = await make_user(db_session, role="agent")

    ghost_conv = await make_conversation(
        db_session, patient_a.id, handoff_state=STATE_HUMAN_ACTIVE, assigned_agent_id=agent.id
    )
    legit_conv = await make_conversation(db_session, patient_b.id, handoff_state=STATE_QUEUED)

    cleanup_queue.extend([ghost_conv.id, legit_conv.id])
    await redis.queue_add(ghost_conv.id)  # split-brain: HUMAN_ACTIVE in DB but also queued
    await redis.queue_add(legit_conv.id)

    removed = await service.repair_queue_ghosts(db_session)

    assert removed == 1
    assert await redis.queue_position(ghost_conv.id) is None
    assert await redis.queue_position(legit_conv.id) is not None


async def test_repair_function_is_noop_when_consistent(service, redis, cleanup_queue, db_session):
    patient = await make_user(db_session, role="user")
    conv = await make_conversation(db_session, patient.id, handoff_state=STATE_QUEUED)
    cleanup_queue.append(conv.id)
    await redis.queue_add(conv.id)

    removed = await service.repair_queue_ghosts(db_session)

    assert removed == 0
    assert await redis.queue_position(conv.id) is not None


async def test_queue_position_updates_as_sessions_are_dequeued(redis, cleanup_queue):
    sids = [unique_id(f"session-{i}") for i in range(3)]
    cleanup_queue.extend(sids)

    for i, sid in enumerate(sids):
        await redis.queue_add(sid, requested_at=1000.0 + i)

    assert await redis.queue_position(sids[0]) == 0
    assert await redis.queue_position(sids[1]) == 1
    assert await redis.queue_position(sids[2]) == 2

    popped = await redis.queue_pop_oldest()
    assert popped == sids[0]
    cleanup_queue.remove(sids[0])

    assert await redis.queue_position(sids[1]) == 0
    assert await redis.queue_position(sids[2]) == 1


async def test_queue_length_reflects_current_size(redis, cleanup_queue):
    sids = [unique_id(f"session-{i}") for i in range(3)]
    cleanup_queue.extend(sids)
    before = await redis.queue_length()

    for sid in sids:
        await redis.queue_add(sid)
    assert await redis.queue_length() == before + 3

    await redis.queue_remove(sids[0])
    cleanup_queue.remove(sids[0])
    assert await redis.queue_length() == before + 2
