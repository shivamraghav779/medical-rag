"""End-to-end websocket endpoint tests via FastAPI's TestClient (real ASGI
app, real Upstash Redis, temp SQLite DB from conftest).

Written as sync test functions because TestClient's websocket_connect is a
blocking API backed by its own thread — DB/Redis setup for each test runs
via a throwaway asyncio.run() before the TestClient (and its own event
loop) is entered, avoiding any event-loop nesting.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

import pytest
from starlette.testclient import TestClient

from api.core import redis_keys as keys
from api.core.database import AsyncSessionLocal
from api.main import app
from api.services.handoff_service import STATE_HUMAN_ACTIVE, STATE_QUEUED
from api.services.redis_service import RedisService
from tests.conftest import get_redis_client, make_conversation, make_token, make_user, unique_id

RECEIVE_TIMEOUT_SECONDS = 10


def _run(coro):
    return asyncio.run(coro)


def _redis() -> RedisService:
    return RedisService(get_redis_client())


def receive_json(ws, timeout: float = RECEIVE_TIMEOUT_SECONDS) -> dict:
    """receive_json(ws) blocks forever (queue.Queue.get with no timeout) if
    the server never sends — fail fast with a clear error instead of hanging
    the whole test run."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(ws.receive_json)
        try:
            return future.result(timeout=timeout)
        except FutureTimeoutError:
            pytest.fail(f"no websocket message received within {timeout}s")


def receive_until(ws, message_type: str, *, timeout: float = RECEIVE_TIMEOUT_SECONDS, max_messages: int = 10) -> dict:
    """The patient channel also runs a periodic queue_position heartbeat
    that can interleave with the messages a test cares about — skip
    anything that isn't the type being waited for instead of assuming a
    fixed message ordinal."""
    for _ in range(max_messages):
        msg = receive_json(ws, timeout=timeout)
        if msg.get("type") == message_type:
            return msg
    pytest.fail(f"did not see a {message_type!r} message within {max_messages} messages")


async def _setup_patient(*, state: str, assigned_agent_id: str | None = None):
    async with AsyncSessionLocal() as db:
        patient = await make_user(db, role="user")
        conv = await make_conversation(
            db, patient.id, handoff_state=state, assigned_agent_id=assigned_agent_id
        )
    return patient, conv


async def _setup_agent():
    async with AsyncSessionLocal() as db:
        agent = await make_user(db, role="agent")
    return agent


@pytest.fixture
def redis_cleanup():
    """Tracks Redis agent/queue state created by a test for teardown."""
    created: dict[str, list[str]] = {"agents": [], "sessions": []}

    yield created

    async def _cleanup():
        r = _redis()
        for agent_id in created["agents"]:
            for sid in await r.get_agent_active_sessions(agent_id):
                await r.remove_agent_active_session(agent_id, sid)
            await r.agent_set_offline(agent_id)
        for sid in created["sessions"]:
            await r.queue_remove(sid)

    _run(_cleanup())


def test_patient_connect_message_and_disconnect(redis_cleanup):
    patient, conv = _run(_setup_patient(state=STATE_QUEUED))
    redis_cleanup["sessions"].append(conv.id)
    _run(_redis().queue_add(conv.id))
    token = make_token(patient.id)

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/chat/{conv.id}?token={token}") as ws:
            first = receive_json(ws)
            assert first["type"] == "state_resume"
            assert first["state"] == STATE_QUEUED

            status = receive_json(ws)
            assert status["type"] == "connection_status"
            assert status["status"] == "connected"

            ws.send_text('{"content": "hello from patient"}')
            # No assigned agent yet — message is persisted, nothing to assert
            # over the wire back to the patient for this message itself.
        # Clean close — no exception means graceful disconnect handling.


def test_agent_connect_take_patient_send_and_patient_receives(redis_cleanup):
    patient, conv = _run(_setup_patient(state=STATE_QUEUED))
    agent = _run(_setup_agent())
    redis_cleanup["sessions"].append(conv.id)
    redis_cleanup["agents"].append(agent.id)
    _run(_redis().queue_add(conv.id))

    patient_token = make_token(patient.id)
    agent_token = make_token(agent.id)

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/chat/{conv.id}?token={patient_token}") as pws:
            # The patient channel also runs a periodic queue_position
            # heartbeat that can interleave with these — match by type
            # rather than assuming a fixed ordinal.
            receive_until(pws, "state_resume")
            receive_until(pws, "connection_status")

            with client.websocket_connect(f"/ws/agent/{agent.id}?token={agent_token}") as aws:
                connected = receive_json(aws)
                assert connected["type"] == "connection_status"

                # Agent auto-routes into the queued patient shortly after connect.
                assigned = receive_json(aws)
                assert assigned["type"] == "patient_assigned"
                assert assigned["session_id"] == conv.id

                aws.send_text(
                    f'{{"type": "message", "session_id": "{conv.id}", "content": "hi, how can I help?"}}'
                )

                delivered = receive_until(pws, "agent_message")
                assert delivered["content"] == "hi, how can I help?"
                assert delivered["agent_id"] == agent.id
                assert delivered.get("agent_name")


def test_agent_cannot_send_to_session_it_does_not_own(redis_cleanup):
    other_patient_id = unique_id("patient")
    agent = _run(_setup_agent())
    redis_cleanup["agents"].append(agent.id)
    agent_token = make_token(agent.id)

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/agent/{agent.id}?token={agent_token}") as aws:
            receive_json(aws)  # connection_status

            aws.send_text(
                f'{{"type": "message", "session_id": "{other_patient_id}", "content": "leak?"}}'
            )
            err = receive_json(aws)
            assert err["type"] == "error"
            assert err["code"] == "session_not_owned"


def test_full_state_style_delivery_on_agent_reconnect(redis_cleanup):
    """Issue 11: reconnecting must self-heal missing tabs — the backend
    pushes connection_status (with active_sessions) plus a patient_assigned
    replay for each currently-active session, without the frontend needing
    to have been connected when the original assignment happened."""
    agent = _run(_setup_agent())
    patient, conv = _run(_setup_patient(state=STATE_HUMAN_ACTIVE, assigned_agent_id=agent.id))
    redis_cleanup["agents"].append(agent.id)
    _run(_redis().agent_set_online(agent.id))
    _run(_redis().add_agent_active_session(agent.id, conv.id))

    agent_token = make_token(agent.id)

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/agent/{agent.id}?token={agent_token}") as aws:
            connected = receive_json(aws)
            assert connected["type"] == "connection_status"
            assert conv.id in connected["active_sessions"]
            # Exact count (rather than >=1 and consistent with active_sessions)
            # isn't asserted here: this runs against the real shared Upstash
            # queue/routing lock, where unrelated concurrent activity from
            # other tests' auto-route timers can legitimately land an extra
            # session on a freshly-connecting agent between requests. What
            # Issue 11 requires — this session is present and capacity
            # accounting is internally consistent — still holds regardless.
            assert connected["active_count"] == len(connected["active_sessions"])
            assert connected["active_count"] >= 1
            assert connected["max_active"] == keys.AGENT_MAX_ACTIVE_SESSIONS

            replay = receive_json(aws)
            assert replay["type"] == "patient_assigned"
            assert replay["session_id"] == conv.id
            assert replay["resumed"] is True


def test_state_resume_delivery_on_patient_reconnect(redis_cleanup):
    """Issue 18: a patient reconnecting mid-handoff immediately gets state,
    agent name (if HUMAN_ACTIVE), and recent message history."""
    agent = _run(_setup_agent())
    patient, conv = _run(_setup_patient(state=STATE_HUMAN_ACTIVE, assigned_agent_id=agent.id))
    redis_cleanup["agents"].append(agent.id)
    r = _redis()
    _run(r.agent_set_online(agent.id))
    _run(r.add_agent_active_session(agent.id, conv.id))
    _run(r.store_message(conv.id, "user", "I have a question"))
    _run(r.store_message(conv.id, "assistant", "Sure, go ahead", agent_id=agent.id))

    patient_token = make_token(patient.id)

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/chat/{conv.id}?token={patient_token}") as pws:
            resume = receive_json(pws)
            assert resume["type"] == "state_resume"
            assert resume["state"] == STATE_HUMAN_ACTIVE
            assert resume["agent_name"]
            assert len(resume["messages"]) >= 2

            # Backward-compat event for existing handlers keyed on
            # agent_connected — may be interleaved with a queue_position
            # heartbeat, so match by type rather than assuming an ordinal.
            connected = receive_until(pws, "agent_connected")
            assert connected["state"] == STATE_HUMAN_ACTIVE
