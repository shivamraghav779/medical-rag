"""WebSocket endpoints for patient, agent, and admin handoff channels."""

from __future__ import annotations

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from api.core.auth import decode_token
from api.core.config import get_settings
from api.core.connection_manager import connection_manager
from api.core.database import AsyncSessionLocal
from api.core.dependencies import get_redis_service
from api.core.logger import get_logger, log_exception
from api.core import redis_keys as keys
from api.core.constants import AGENT_REGISTER_ROUTE_DELAY_SECONDS, PATIENT_HISTORY_RESUME_LIMIT
from api.models.db_models import Conversation, User
from api.services.handoff_service import (
    STATE_HUMAN_ACTIVE,
    STATE_QUEUED,
    HandoffService,
)

logger = get_logger(__name__)
router = APIRouter(tags=["websocket"])


async def _user_from_token(token: Optional[str]) -> Optional[User]:
    if not token:
        return None
    settings = get_settings()
    try:
        user_id = decode_token(token, settings)
    except Exception:
        return None
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User).where(User.id == user_id, User.is_active.is_(True))
        )
        return result.scalar_one_or_none()


@router.websocket("/ws/chat/{session_id}")
async def patient_ws(
    websocket: WebSocket,
    session_id: str,
    token: Optional[str] = Query(default=None),
):
    user = await _user_from_token(token)
    redis = get_redis_service(websocket)
    handoff = HandoffService(redis)

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Conversation).where(Conversation.id == session_id))
        conv = result.scalar_one_or_none()
        if conv is None:
            await websocket.close(code=4404)
            return
        if user and conv.user_id != user.id:
            await websocket.close(code=4403)
            return
        state = conv.handoff_state or STATE_QUEUED
        assigned_agent_id = conv.assigned_agent_id
        if state not in (STATE_QUEUED, STATE_HUMAN_ACTIVE):
            await websocket.close(code=4409)
            return

    await connection_manager.connect_patient(session_id, websocket)
    stop = asyncio.Event()

    # On every (re)connect, replay recent message history + current state so a
    # patient who reloads mid-conversation isn't left with an empty thread —
    # this is the one client (the widget) that has no REST history fetch of
    # its own on init. (Issue 18)
    agent_name = None
    if state == STATE_HUMAN_ACTIVE and assigned_agent_id:
        async with AsyncSessionLocal() as db:
            agent_name = await handoff._agent_display_name(db, assigned_agent_id)
    recent_messages = await redis.get_messages(session_id)
    await connection_manager.send_to_patient(
        session_id,
        {
            "type": "state_resume",
            "state": state,
            "agent_name": agent_name,
            "queue_position": await redis.queue_position(session_id) if state == STATE_QUEUED else None,
            "messages": recent_messages[-PATIENT_HISTORY_RESUME_LIMIT:],
        },
    )

    # Kept as a separate event too — existing frontend/widget handlers already
    # key off "agent_connected" specifically to flip into HUMAN_ACTIVE display.
    if state == STATE_HUMAN_ACTIVE and assigned_agent_id:
        await connection_manager.send_to_patient(
            session_id,
            {
                "type": "agent_connected",
                "agent_id": assigned_agent_id,
                "agent_name": agent_name,
                "state": STATE_HUMAN_ACTIVE,
                "resumed": True,
            },
        )

    async def _position_loop() -> None:
        while not stop.is_set():
            try:
                pos = await redis.queue_position(session_id)
                payload = await redis.get_session_state_payload(session_id)
                await connection_manager.send_to_patient(
                    session_id,
                    {
                        "type": "queue_position",
                        "position": pos if pos is not None else 0,
                        "state": (payload or {}).get("state") or state,
                    },
                )
            except Exception as exc:
                log_exception(logger, exc)
            try:
                await asyncio.wait_for(stop.wait(), timeout=30)
            except asyncio.TimeoutError:
                continue

    position_task = asyncio.create_task(_position_loop())
    try:
        await connection_manager.send_to_patient(
            session_id,
            {"type": "connection_status", "status": "connected", "session_id": session_id},
        )
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = {"type": "message", "content": raw}
            content = (data.get("content") or "").strip()
            if not content:
                continue
            # Persist + forward to assigned agent
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(Conversation).where(Conversation.id == session_id)
                )
                conv = result.scalar_one_or_none()
                agent_id = conv.assigned_agent_id if conv else None
                if conv:
                    from api.services.conversation_service import ConversationService

                    await ConversationService().add_message(
                        db, session_id, "user", content
                    )
                    await db.commit()
            await redis.store_message(session_id, "user", content)
            if agent_id and await redis.validate_agent_session(agent_id, session_id):
                await connection_manager.send_to_agent_for_session(
                    agent_id,
                    session_id,
                    {"type": "patient_message", "content": content},
                    owned=True,
                )
    except WebSocketDisconnect:
        logger.info("Patient WS disconnect", extra={"session_id": session_id})
    except Exception as exc:
        log_exception(logger, exc)
    finally:
        stop.set()
        position_task.cancel()
        was_active = connection_manager.disconnect_patient(session_id, websocket)
        # Only requeue on a real hangup — not when a newer socket replaced us
        # (React Strict Mode / dashboard refresh / widget reconnect).
        if was_active and not connection_manager.patient_connected(session_id):
            await asyncio.sleep(1.0)
            if not connection_manager.patient_connected(session_id):
                try:
                    await handoff.requeue_on_patient_disconnect(session_id)
                except Exception as exc:
                    log_exception(logger, exc)


@router.websocket("/ws/agent/{agent_id}")
async def agent_ws(
    websocket: WebSocket,
    agent_id: str,
    token: Optional[str] = Query(default=None),
):
    user = await _user_from_token(token)
    if user is None or user.id != agent_id:
        await websocket.close(code=4401)
        return
    if (user.role or "user") not in ("agent", "admin"):
        await websocket.close(code=4403)
        return

    redis = get_redis_service(websocket)
    handoff = HandoffService(redis)

    await connection_manager.connect_agent(agent_id, websocket)
    try:
        await redis.agent_set_online(agent_id)
        async with AsyncSessionLocal() as db:
            await handoff.reconcile_agent_sessions(db, agent_id)
        active_sessions = await redis.get_agent_active_sessions(agent_id)
        active_count = len(active_sessions)
        await connection_manager.send_to_agent(
            agent_id,
            {
                "type": "connection_status",
                "status": "connected",
                "agent_id": agent_id,
                "active_count": active_count,
                "max_active": keys.AGENT_MAX_ACTIVE_SESSIONS,
                "active_sessions": active_sessions,
            },
        )
        for sid in active_sessions:
            try:
                async with AsyncSessionLocal() as db:
                    conv = await handoff._load_assignment_context(db, sid)
                await connection_manager.send_to_agent(
                    agent_id,
                    {
                        "type": "patient_assigned",
                        "session_id": sid,
                        "state": STATE_HUMAN_ACTIVE,
                        "resumed": True,
                        "patient": conv.get("patient"),
                        "clinical_context": conv.get("clinical_context"),
                        "messages": conv.get("messages"),
                        "reason": conv.get("reason"),
                        "last_query": conv.get("last_query"),
                        "active_count": active_count,
                        "max_active": keys.AGENT_MAX_ACTIVE_SESSIONS,
                    },
                )
            except Exception as exc:
                log_exception(logger, exc)
                await connection_manager.send_to_agent(
                    agent_id,
                    {
                        "type": "patient_assigned",
                        "session_id": sid,
                        "state": STATE_HUMAN_ACTIVE,
                        "resumed": True,
                        "patient": None,
                        "messages": [],
                        "active_count": active_count,
                        "max_active": keys.AGENT_MAX_ACTIVE_SESSIONS,
                    },
                )
        # Fill ONE open slot from the queue, after a short delay so the socket
        # is fully established before a patient_assigned event could arrive.
        # Draining all open slots at once (the old behavior) could route
        # patients before the frontend had subscribed to onmessage, silently
        # dropping their assignment event. Remaining slots fill via explicit
        # "Take next" clicks or when the next patient joins the queue. (Issue 14)
        if await redis.get_agent_active_count(agent_id) < keys.AGENT_MAX_ACTIVE_SESSIONS:
            await asyncio.sleep(AGENT_REGISTER_ROUTE_DELAY_SECONDS)
            if connection_manager.agent_connected(agent_id):
                await handoff.try_route_next_available()

        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            msg_type = data.get("type")
            if msg_type == "ping":
                # Heartbeat (Issue 24): ack, and opportunistically re-add to
                # agents:online in case Redis was cleared/restarted mid-session.
                await redis.agent_set_online(agent_id)
                await connection_manager.send_to_agent(agent_id, {"type": "pong"})
                continue
            if msg_type == "message":
                session_id = data.get("session_id")
                content = (data.get("content") or "").strip()
                if not session_id or not content:
                    continue
                owned = await redis.validate_agent_session(agent_id, session_id)
                if not owned:
                    logger.warning(
                        "Security: agent message to unowned session rejected",
                        extra={"agent_id": agent_id, "session_id": session_id},
                    )
                    await connection_manager.send_to_agent(
                        agent_id,
                        {
                            "type": "error",
                            "code": "session_not_owned",
                            "session_id": session_id,
                            "message": "Cannot send — session is not in your active chats.",
                        },
                    )
                    continue
                await redis.store_message(session_id, "assistant", content, agent_id=agent_id)
                agent_name = user.full_name or user.email or "Specialist"
                async with AsyncSessionLocal() as db:
                    from api.services.conversation_service import ConversationService

                    await ConversationService().add_message(
                        db,
                        session_id,
                        "assistant",
                        content,
                        metadata={"source": "human_agent", "agent_name": agent_name},
                    )
                    await db.commit()
                # Strict patient routing by session_id only
                delivered = await connection_manager.send_to_patient(
                    session_id,
                    {
                        "type": "agent_message",
                        "agent_id": agent_id,
                        "agent_name": agent_name,
                        "session_id": session_id,
                        "content": content,
                    },
                )
                if not delivered:
                    await connection_manager.send_to_agent(
                        agent_id,
                        {
                            "type": "error",
                            "code": "patient_offline",
                            "session_id": session_id,
                            "message": "Patient is offline — message saved, but not delivered live.",
                        },
                    )
            elif msg_type == "resolve":
                session_id = data.get("session_id")
                if not session_id:
                    continue
                owned = await redis.validate_agent_session(agent_id, session_id)
                if not owned:
                    logger.warning(
                        "Security: agent resolve on unowned session rejected",
                        extra={"agent_id": agent_id, "session_id": session_id},
                    )
                    await connection_manager.send_to_agent(
                        agent_id,
                        {
                            "type": "error",
                            "code": "session_not_owned",
                            "session_id": session_id,
                            "message": "Cannot resolve — session is not in your active chats.",
                        },
                    )
                    continue
                async with AsyncSessionLocal() as db:
                    await handoff.resolve(
                        db,
                        session_id,
                        agent_id=agent_id,
                        tag=str(data.get("tag") or "other"),
                        end_reason=str(data.get("end_reason") or "issue_resolved"),
                        issue_status=str(data.get("issue_status") or "resolved"),
                        comments=(data.get("comments") or None),
                    )
    except WebSocketDisconnect:
        logger.info("Agent WS disconnect", extra={"agent_id": agent_id})
    except RuntimeError as exc:
        # Replaced socket (Strict Mode / HMR) — not a real agent hangup.
        if "not connected" in str(exc).lower() or "accept" in str(exc).lower():
            logger.info("Agent WS replaced", extra={"agent_id": agent_id})
        else:
            log_exception(logger, exc)
    except Exception as exc:
        log_exception(logger, exc)
    finally:
        was_active = connection_manager.disconnect_agent(agent_id, websocket)
        if was_active and not connection_manager.agent_connected(agent_id):
            # Allow React Strict Mode / HMR / brief refresh to reconnect
            # without dumping all active patients back into the queue.
            # Settings-configurable, 5s minimum (Issue 15).
            await asyncio.sleep(max(5.0, float(get_settings().agent_disconnect_grace_seconds)))
            if not connection_manager.agent_connected(agent_id):
                try:
                    await handoff.requeue_on_agent_disconnect(agent_id)
                except Exception as exc:
                    log_exception(logger, exc)


@router.websocket("/ws/admin/queue")
async def admin_queue_ws(
    websocket: WebSocket,
    token: Optional[str] = Query(default=None),
):
    user = await _user_from_token(token)
    if user is None or (user.role or "user") != "admin":
        # Allow agent+admin for ops visibility
        if user is None or (user.role or "user") not in ("admin", "agent"):
            await websocket.close(code=4403)
            return

    redis = get_redis_service(websocket)
    handoff = HandoffService(redis)
    await connection_manager.connect_admin(websocket)
    stop = asyncio.Event()

    async def _broadcast_loop() -> None:
        while not stop.is_set():
            try:
                async with AsyncSessionLocal() as db:
                    entries = await handoff.queue_snapshot_for_admin(db)
                    active = await handoff.active_handoffs_for_admin(db)
                agents = await redis.agent_list_statuses()
                busy = sum(1 for a in agents if (a.get("status") or "") == "busy")
                waits = [e.get("wait_seconds", 0) for e in entries]
                avg_wait = int(sum(waits) / len(waits)) if waits else 0
                payload = {
                    "type": "queue_snapshot",
                    "queue_length": len(entries),
                    "entries": entries,
                    "active": active,
                    "agents": agents,
                    "active_conversations": max(busy, len(active)),
                    "average_wait_seconds": avg_wait,
                    "patient_connections": connection_manager.active_patient_count,
                    "agent_connections": connection_manager.active_agent_count,
                }
                await connection_manager.broadcast_admins(payload)
            except Exception as exc:
                log_exception(logger, exc)
            try:
                await asyncio.wait_for(stop.wait(), timeout=10)
            except asyncio.TimeoutError:
                continue

    task = asyncio.create_task(_broadcast_loop())
    try:
        while True:
            # Keepalive / ignore client messages
            await websocket.receive_text()
    except WebSocketDisconnect:
        logger.info("Admin WS disconnect")
    except Exception as exc:
        log_exception(logger, exc)
    finally:
        stop.set()
        task.cancel()
        connection_manager.disconnect_admin(websocket)
