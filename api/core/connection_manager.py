"""Singleton WebSocket connection manager for handoff channels.

Routers and services must never hold WebSocket objects directly —
all send/broadcast/cleanup goes through this manager.

Patient routing is strictly by session_id (no fan-out to other patients).
Agent session sends must validate ownership when a session_id is provided.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import WebSocket

from api.core.exceptions import WebSocketConnectionError
from api.core.logger import get_logger, log_exception

logger = get_logger(__name__)


class ConnectionManager:
    """Tracks patient, agent, and admin WebSocket connections."""

    def __init__(self) -> None:
        self._patients: dict[str, WebSocket] = {}
        self._agents: dict[str, WebSocket] = {}
        self._admins: list[WebSocket] = []

    async def connect_patient(self, session_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        # Register the new socket BEFORE closing the old one so a replaced
        # connection's finally-handler does not treat this as a real hangup.
        old = self._patients.get(session_id)
        self._patients[session_id] = websocket
        if old is not None and old is not websocket:
            try:
                await old.close()
            except Exception as exc:
                log_exception(logger, exc)
        logger.info("Patient WebSocket connected", extra={"session_id": session_id})

    async def connect_agent(self, agent_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        old = self._agents.get(agent_id)
        self._agents[agent_id] = websocket
        if old is not None and old is not websocket:
            # Close the previous socket without unregistering the new one.
            try:
                await old.close()
            except Exception as exc:
                log_exception(logger, exc)
            # If a race cleared our new socket, restore it.
            if self._agents.get(agent_id) is not websocket:
                self._agents[agent_id] = websocket
        logger.info("Agent WebSocket connected", extra={"agent_id": agent_id})

    async def connect_admin(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._admins.append(websocket)
        logger.info("Admin WebSocket connected", extra={"admin_count": len(self._admins)})

    def disconnect_patient(self, session_id: str, websocket: Optional[WebSocket] = None) -> bool:
        """Remove patient socket. Returns True only if this socket was the active one."""
        current = self._patients.get(session_id)
        if current is None:
            return False
        if websocket is not None and current is not websocket:
            return False
        self._patients.pop(session_id, None)
        logger.info("Patient WebSocket disconnected", extra={"session_id": session_id})
        return True

    def disconnect_agent(self, agent_id: str, websocket: Optional[WebSocket] = None) -> bool:
        """Remove agent socket. Returns True only if this socket was the active one."""
        current = self._agents.get(agent_id)
        if current is None:
            return False
        if websocket is not None and current is not websocket:
            return False
        self._agents.pop(agent_id, None)
        logger.info("Agent WebSocket disconnected", extra={"agent_id": agent_id})
        return True

    def disconnect_admin(self, websocket: WebSocket) -> None:
        self._admins = [ws for ws in self._admins if ws is not websocket]
        logger.info("Admin WebSocket disconnected", extra={"admin_count": len(self._admins)})

    async def send_to_patient(self, session_id: str, payload: dict[str, Any]) -> bool:
        """Route exclusively to the patient WebSocket for this session_id. Never iterates all."""
        ws = self._patients.get(session_id)
        if ws is None:
            return False
        try:
            await ws.send_text(json.dumps(payload))
            return True
        except Exception as exc:
            log_exception(logger, exc)
            self.disconnect_patient(session_id, ws)
            return False

    async def send_to_agent(self, agent_id: str, payload: dict[str, Any]) -> bool:
        """Send to an agent's control channel (assignments, resolve, etc.)."""
        ws = self._agents.get(agent_id)
        if ws is None:
            return False
        try:
            await ws.send_text(json.dumps(payload))
            return True
        except Exception as exc:
            log_exception(logger, exc)
            self.disconnect_agent(agent_id, ws)
            return False

    async def send_to_agent_for_session(
        self,
        agent_id: str,
        session_id: str,
        payload: dict[str, Any],
        *,
        owned: bool,
    ) -> bool:
        """Send to agent only after session ownership is confirmed by the caller.

        ``owned`` must be True only when Redis validate_agent_session passed.
        """
        if not owned:
            logger.warning(
                "Blocked agent send — session not owned",
                extra={"agent_id": agent_id, "session_id": session_id},
            )
            raise WebSocketConnectionError(
                f"Agent {agent_id} does not own session {session_id}"
            )
        message = {**payload, "session_id": session_id}
        return await self.send_to_agent(agent_id, message)

    async def broadcast_admins(self, payload: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        message = json.dumps(payload)
        for ws in list(self._admins):
            try:
                await ws.send_text(message)
            except Exception as exc:
                log_exception(logger, exc)
                dead.append(ws)
        for ws in dead:
            self.disconnect_admin(ws)

    def patient_connected(self, session_id: str) -> bool:
        return session_id in self._patients

    def agent_connected(self, agent_id: str) -> bool:
        return agent_id in self._agents

    @property
    def active_patient_count(self) -> int:
        return len(self._patients)

    @property
    def active_agent_count(self) -> int:
        return len(self._agents)


connection_manager = ConnectionManager()
