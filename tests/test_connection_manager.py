"""ConnectionManager tests — strict per-session/per-agent routing isolation,
clean replace-without-cross-delivery, and full cleanup on disconnect.

Uses a lightweight fake WebSocket (duck-typed to accept/send_text/close) so
these are pure in-process unit tests — no real network socket needed.
"""

from __future__ import annotations

import pytest

from api.core.connection_manager import ConnectionManager


class FakeWebSocket:
    def __init__(self, name: str = "ws") -> None:
        self.name = name
        self.accepted = False
        self.closed = False
        self.sent: list[str] = []
        self.raise_on_send = False

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, text: str) -> None:
        if self.raise_on_send:
            raise RuntimeError("send on closed socket")
        self.sent.append(text)

    async def close(self, code: int = 1000) -> None:
        self.closed = True


@pytest.fixture
def manager() -> ConnectionManager:
    return ConnectionManager()


async def test_send_to_patient_only_reaches_that_session(manager):
    ws_a = FakeWebSocket("patient-a")
    ws_b = FakeWebSocket("patient-b")
    await manager.connect_patient("session-a", ws_a)
    await manager.connect_patient("session-b", ws_b)

    delivered = await manager.send_to_patient("session-a", {"type": "agent_message", "content": "hi"})

    assert delivered is True
    assert len(ws_a.sent) == 1
    assert ws_b.sent == []


async def test_send_to_patient_unknown_session_returns_false(manager):
    delivered = await manager.send_to_patient("no-such-session", {"type": "x"})
    assert delivered is False


async def test_send_to_agent_for_session_requires_ownership(manager):
    ws = FakeWebSocket("agent")
    await manager.connect_agent("agent-1", ws)

    with pytest.raises(Exception):
        await manager.send_to_agent_for_session(
            "agent-1", "session-x", {"type": "patient_message"}, owned=False
        )
    assert ws.sent == []

    delivered = await manager.send_to_agent_for_session(
        "agent-1", "session-x", {"type": "patient_message"}, owned=True
    )
    assert delivered is True
    assert len(ws.sent) == 1


async def test_replacing_patient_connection_does_not_deliver_to_old_socket(manager):
    """Strict Mode double-mount / dashboard refresh scenario: a second socket
    for the same session_id replaces the first. The old socket must never
    receive messages sent after the replacement (Issue 22)."""
    old_ws = FakeWebSocket("old")
    new_ws = FakeWebSocket("new")

    await manager.connect_patient("session-1", old_ws)
    await manager.connect_patient("session-1", new_ws)  # replaces old_ws

    assert old_ws.closed is True

    await manager.send_to_patient("session-1", {"type": "agent_message", "content": "hello"})

    assert new_ws.sent, "new socket should receive the message"
    assert old_ws.sent == [], "old (replaced) socket must never receive messages"


async def test_replacing_agent_connection_does_not_deliver_to_old_socket(manager):
    old_ws = FakeWebSocket("old-agent")
    new_ws = FakeWebSocket("new-agent")

    await manager.connect_agent("agent-1", old_ws)
    await manager.connect_agent("agent-1", new_ws)

    assert old_ws.closed is True

    await manager.send_to_agent("agent-1", {"type": "patient_assigned"})

    assert new_ws.sent
    assert old_ws.sent == []


async def test_disconnect_patient_only_true_for_active_socket(manager):
    """disconnect_patient must return False (and not remove the current live
    socket) when called with a stale/replaced socket reference — this is
    what prevents a replaced connection's cleanup from requeuing a patient
    who is actually still connected via the newer socket."""
    old_ws = FakeWebSocket("old")
    new_ws = FakeWebSocket("new")

    await manager.connect_patient("session-1", old_ws)
    await manager.connect_patient("session-1", new_ws)

    # Old socket's own disconnect handler fires with a stale reference.
    was_active = manager.disconnect_patient("session-1", old_ws)
    assert was_active is False
    assert manager.patient_connected("session-1") is True

    was_active = manager.disconnect_patient("session-1", new_ws)
    assert was_active is True
    assert manager.patient_connected("session-1") is False


async def test_disconnect_cleanup_removes_from_all_tracking(manager):
    patient_ws = FakeWebSocket("patient")
    agent_ws = FakeWebSocket("agent")
    admin_ws = FakeWebSocket("admin")

    await manager.connect_patient("session-1", patient_ws)
    await manager.connect_agent("agent-1", agent_ws)
    await manager.connect_admin(admin_ws)

    assert manager.active_patient_count == 1
    assert manager.active_agent_count == 1

    manager.disconnect_patient("session-1", patient_ws)
    manager.disconnect_agent("agent-1", agent_ws)
    manager.disconnect_admin(admin_ws)

    assert manager.active_patient_count == 0
    assert manager.active_agent_count == 0
    assert manager.patient_connected("session-1") is False
    assert manager.agent_connected("agent-1") is False
    # broadcast_admins must not error/deliver to a disconnected admin socket
    await manager.broadcast_admins({"type": "queue_snapshot"})
    assert admin_ws.sent == []


async def test_send_to_patient_disconnects_on_send_failure(manager):
    """A send failure (e.g. socket half-closed) must clean up tracking so a
    subsequent connect for the same session isn't blocked/racy."""
    ws = FakeWebSocket("flaky")
    ws.raise_on_send = True
    await manager.connect_patient("session-1", ws)

    delivered = await manager.send_to_patient("session-1", {"type": "x"})

    assert delivered is False
    assert manager.patient_connected("session-1") is False


async def test_broadcast_admins_reaches_all_connected(manager):
    a1, a2 = FakeWebSocket("admin-1"), FakeWebSocket("admin-2")
    await manager.connect_admin(a1)
    await manager.connect_admin(a2)

    await manager.broadcast_admins({"type": "queue_snapshot", "queue_length": 0})

    assert len(a1.sent) == 1
    assert len(a2.sent) == 1
