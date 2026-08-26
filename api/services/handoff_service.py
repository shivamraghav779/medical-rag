"""Human handoff orchestration — queue, assign, resolve, persist state."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.core.connection_manager import connection_manager
from api.core.constants import GROUNDED_ANSWER_NOT_FOUND
from api.core import redis_keys as keys
from api.core.exceptions import (
    AgentUnavailableException,
    ConversationNotFoundException,
    SessionNotQueuedError,
    ValidationException,
)
from api.core.logger import get_logger, log_exception
from api.core.database import AsyncSessionLocal
from api.models.db_models import Conversation, Message, User
from api.services.redis_service import RedisService

logger = get_logger(__name__)

STATE_BOT_ACTIVE = "BOT_ACTIVE"
STATE_HANDOFF_REQUESTED = "HANDOFF_REQUESTED"
STATE_QUEUED = "QUEUED"
STATE_HUMAN_ACTIVE = "HUMAN_ACTIVE"
STATE_RESOLVED = "RESOLVED"

MANUAL_HANDOFF_PHRASE = "connect me to a human"
FAIL_THRESHOLD = 3
NOT_FOUND_THRESHOLD = 2

# Loose intent match — patients rarely type the exact canned phrase.
_HANDOFF_PATTERNS = (
    r"\bconnect(?:\s+me)?\s+(?:to|with|me\s+to)\s+(?:an?\s+)?(?:human|agent|specialist|person|doctor|nurse)\b",
    r"\btalk\s+(?:to|with)\s+(?:an?\s+)?(?:human|agent|specialist|person|doctor|nurse)\b",
    r"\bspeak\s+(?:to|with)\s+(?:an?\s+)?(?:human|agent|specialist|person|doctor|nurse)\b",
    r"\b(?:transfer|hand\s*off|handoff)\s+(?:me\s+)?(?:to\s+)?(?:an?\s+)?(?:human|agent|specialist)\b",
    r"\b(?:real|live)\s+(?:person|human|agent)\b",
    r"\bhuman\s+(?:agent|specialist|support)\b",
)


def is_manual_handoff_request(query: str) -> bool:
    text = " ".join((query or "").strip().lower().split())
    if not text:
        return False
    if text == MANUAL_HANDOFF_PHRASE:
        return True
    return any(re.search(p, text) for p in _HANDOFF_PATTERNS)

RESOLVE_TAGS = frozenset(
    {"medication", "labs", "guidelines", "symptoms", "billing", "technical", "other"}
)
RESOLVE_END_REASONS = frozenset(
    {
        "issue_resolved",
        "patient_ended",
        "patient_inactive",
        "unresolved",
        "transferred",
    }
)
RESOLVE_ISSUE_STATUSES = frozenset({"resolved", "not_resolved", "partial"})


class HandoffService:
    def __init__(self, redis_service: RedisService):
        self._redis = redis_service

    async def _record_event(
        self,
        db: AsyncSession,
        conversation_id: str,
        content: str,
        *,
        event_type: str,
        **meta: Any,
    ) -> None:
        """Persist a scrollable timeline divider into the conversation history."""
        from api.services.conversation_service import ConversationService

        await ConversationService().add_message(
            db,
            conversation_id,
            "event",
            content,
            metadata={"handoff_event": event_type, **meta},
        )

    async def _agent_display_name(self, db: AsyncSession, agent_id: Optional[str]) -> str:
        if not agent_id:
            return "a specialist"
        row = await db.execute(select(User).where(User.id == agent_id))
        agent = row.scalar_one_or_none()
        if agent is None:
            return "a specialist"
        return (agent.full_name or "").strip() or agent.email or "a specialist"

    async def _assign_and_notify(self, agent_id: str, session_id: str) -> Optional[dict]:
        """Shared path after session is reserved for an agent."""
        async with AsyncSessionLocal() as db:
            try:
                await self.persist_state(
                    db,
                    session_id,
                    STATE_HUMAN_ACTIVE,
                    reason="routed_to_agent",
                    agent_id=agent_id,
                )
                agent_name = await self._agent_display_name(db, agent_id)
                await self._record_event(
                    db,
                    session_id,
                    f"Connected with {agent_name}",
                    event_type="agent_connected",
                    agent_id=agent_id,
                    agent_name=agent_name,
                )
                await db.commit()
            except Exception as exc:
                log_exception(logger, exc)
                await db.rollback()
                payload = await self._redis.get_session_state_payload(session_id)
                queued_at = (payload or {}).get("queued_at")
                await self._redis.queue_add(
                    session_id,
                    requested_at=float(queued_at) if queued_at is not None else None,
                )
                await self._redis.remove_agent_active_session(agent_id, session_id)
                return None

            try:
                conv = await self._load_assignment_context(db, session_id)
            except Exception as exc:
                # Assignment already committed — still notify with empty context
                log_exception(logger, exc)
                conv = {}

        active_count = await self._redis.get_agent_active_count(agent_id)
        await connection_manager.send_to_patient(
            session_id,
            {
                "type": "agent_connected",
                "agent_id": agent_id,
                "agent_name": agent_name,
                "state": STATE_HUMAN_ACTIVE,
            },
        )
        await connection_manager.send_to_agent(
            agent_id,
            {
                "type": "patient_assigned",
                "session_id": session_id,
                "state": STATE_HUMAN_ACTIVE,
                "patient": conv.get("patient"),
                "clinical_context": conv.get("clinical_context"),
                "messages": conv.get("messages"),
                "reason": conv.get("reason"),
                "last_query": conv.get("last_query"),
                "active_count": active_count,
                "max_active": keys.AGENT_MAX_ACTIVE_SESSIONS,
            },
        )
        await connection_manager.broadcast_admins(
            {
                "type": "assignment",
                "session_id": session_id,
                "agent_id": agent_id,
                "active_count": active_count,
            }
        )
        logger.info(
            "Routed patient to agent",
            extra={
                "session_id": session_id,
                "agent_id": agent_id,
                "active_count": active_count,
            },
        )
        return {"session_id": session_id, "agent_id": agent_id}

    async def persist_state(
        self,
        db: AsyncSession,
        conversation_id: str,
        state: str,
        *,
        reason: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Conversation:
        result = await db.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        conv = result.scalar_one_or_none()
        if conv is None:
            raise ConversationNotFoundException(f"Conversation {conversation_id} not found")
        conv.handoff_state = state
        if reason is not None:
            conv.handoff_reason = reason
        if agent_id is not None or state in (STATE_QUEUED, STATE_RESOLVED, STATE_BOT_ACTIVE):
            conv.assigned_agent_id = agent_id
        await db.flush()
        await self._redis.set_session_state(
            conversation_id,
            state,
            reason=reason or conv.handoff_reason,
            agent_id=agent_id if agent_id is not None else conv.assigned_agent_id,
        )
        logger.info(
            "Handoff state persisted",
            extra={
                "session_id": conversation_id,
                "state": state,
                "reason": reason,
                "agent_id": agent_id,
            },
        )
        return conv

    async def request_handoff(
        self,
        db: AsyncSession,
        conversation_id: str,
        *,
        reason: str = "patient_request",
        user_id: Optional[str] = None,
    ) -> dict:
        result = await db.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        conv = result.scalar_one_or_none()
        if conv is None:
            raise ConversationNotFoundException(f"Conversation {conversation_id} not found")
        if user_id and conv.user_id != user_id:
            raise ConversationNotFoundException(f"Conversation {conversation_id} not found")

        if conv.handoff_state == STATE_HUMAN_ACTIVE:
            position = await self._redis.queue_position(conversation_id)
            return {
                "conversation_id": conversation_id,
                "state": STATE_HUMAN_ACTIVE,
                "queue_position": position if position is not None else 0,
                "reason": conv.handoff_reason or reason,
            }

        await self.persist_state(
            db, conversation_id, STATE_HANDOFF_REQUESTED, reason=reason, agent_id=None
        )
        position = await self._redis.queue_add(conversation_id)
        await self.persist_state(
            db, conversation_id, STATE_QUEUED, reason=reason, agent_id=None
        )
        await self._record_event(
            db,
            conversation_id,
            "Connecting with a human specialist",
            event_type="connecting",
            reason=reason,
        )
        await db.commit()

        # Try immediate assignment if an agent is free
        await self.try_route_next_available()

        position = await self._redis.queue_position(conversation_id)
        state_payload = await self._redis.get_session_state_payload(conversation_id)
        state = (state_payload or {}).get("state") or STATE_QUEUED
        return {
            "conversation_id": conversation_id,
            "state": state,
            "queue_position": position if position is not None else 0,
            "reason": reason,
        }

    async def cancel_handoff(
        self,
        db: AsyncSession,
        conversation_id: str,
        *,
        user_id: str,
    ) -> dict:
        """Patient-initiated cancel while still QUEUED/HANDOFF_REQUESTED —
        the missing counterpart to request_handoff (Issue: the frontend's
        "Cancel" button only ever reset local UI state; the backend queue
        entry and DB state survived, so a "cancelled" patient could still
        be routed to an agent later with no one there). Once a session is
        HUMAN_ACTIVE this no longer applies — an agent ends that via
        resolve() (or an admin via force-resolve), not the patient."""
        result = await db.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        conv = result.scalar_one_or_none()
        if conv is None or conv.user_id != user_id:
            # Same not-found-for-non-owner shape as request_handoff, so this
            # doesn't leak whether a conversation exists to someone else's ID.
            raise ConversationNotFoundException(f"Conversation {conversation_id} not found")
        if conv.handoff_state not in (STATE_HANDOFF_REQUESTED, STATE_QUEUED):
            raise ValidationException(
                f"Cannot cancel — conversation is {conv.handoff_state}, not waiting in queue."
            )

        await self.persist_state(
            db, conversation_id, STATE_BOT_ACTIVE, reason="patient_cancelled", agent_id=None
        )
        await self._record_event(
            db,
            conversation_id,
            "Patient cancelled the request — back to assistant",
            event_type="cancelled",
        )
        await db.commit()
        # Must happen after the DB commit, not before: if the queue removal
        # succeeded but the commit then failed, the session would be gone
        # from the queue while the DB still said QUEUED — a split-brain in
        # the same shape the earlier hardening pass fixed elsewhere.
        await self._redis.queue_remove(conversation_id)

        return {"conversation_id": conversation_id, "state": STATE_BOT_ACTIVE}

    async def try_route_next_available(self) -> Optional[dict]:
        """Assign longest-waiting patient to an online agent with capacity (< 5).

        Serialized via a Redis lock (Issue 13) — without it, auto-route on
        agent-register and an explicit take-next click can both read the same
        "under capacity" snapshot and both add a session, overrunning the
        5-session cap (queue_pop_oldest's ZPOPMIN is atomic and prevents two
        callers popping the *same* patient, but not two callers each popping a
        *different* patient onto the same agent past capacity).
        """
        lock_token = await self._redis.acquire_routing_lock()
        if lock_token is None:
            logger.warning("try_route_next_available: routing lock busy, skipping this cycle")
            return None
        try:
            try:
                available_ids = await self._redis.get_available_agents()
            except Exception as exc:
                log_exception(logger, exc)
                return None

            if not available_ids:
                return None

            # Prefer agent with fewest active chats, then oldest last_freed_at
            ranked: list[tuple[int, float, str]] = []
            for agent_id in available_ids:
                count = await self._redis.get_agent_active_count(agent_id)
                status = await self._redis.agent_get_status(agent_id) or {}
                ranked.append((count, float(status.get("last_freed_at") or 0), agent_id))
            ranked.sort(key=lambda t: (t[0], t[1]))
            agent_id = ranked[0][2]

            if await self._redis.get_agent_active_count(agent_id) >= keys.AGENT_MAX_ACTIVE_SESSIONS:
                return None

            session_id = await self._redis.queue_pop_oldest()
            if not session_id:
                return None

            added = await self._redis.add_agent_active_session(agent_id, session_id)
            if not added:
                # Capacity raced — put patient back with original queued_at if known
                payload = await self._redis.get_session_state_payload(session_id)
                queued_at = (payload or {}).get("queued_at")
                await self._redis.queue_add(
                    session_id,
                    requested_at=float(queued_at) if queued_at is not None else None,
                )
                return None
        finally:
            await self._redis.release_routing_lock(lock_token)

        return await self._assign_and_notify(agent_id, session_id)

    async def try_route_to_agent(self, agent_id: str) -> Optional[dict]:
        """Assign longest-waiting patient specifically to this agent if under capacity."""
        lock_token = await self._redis.acquire_routing_lock()
        if lock_token is None:
            raise AgentUnavailableException(
                "Routing is busy — try again.", is_retryable=True
            )
        try:
            if agent_id not in await self._redis.get_available_agents():
                return None
            session_id = await self._redis.queue_pop_oldest()
            if not session_id:
                return None
            added = await self._redis.add_agent_active_session(agent_id, session_id)
            if not added:
                payload = await self._redis.get_session_state_payload(session_id)
                queued_at = (payload or {}).get("queued_at")
                await self._redis.queue_add(
                    session_id,
                    requested_at=float(queued_at) if queued_at is not None else None,
                )
                return None
        finally:
            await self._redis.release_routing_lock(lock_token)
        return await self._assign_and_notify(agent_id, session_id)

    async def _load_assignment_context(self, db: AsyncSession, session_id: str) -> dict:
        result = await db.execute(
            select(Conversation)
            .where(Conversation.id == session_id)
            .options(selectinload(Conversation.messages), selectinload(Conversation.user))
        )
        conv = result.scalar_one_or_none()
        if conv is None:
            return {}
        agent_name = None
        if conv.assigned_agent_id:
            agent_row = await db.execute(select(User).where(User.id == conv.assigned_agent_id))
            agent = agent_row.scalar_one_or_none()
            agent_name = agent.full_name if agent else None
        messages = []
        for m in (conv.messages or []):
            meta = None
            if m.metadata_json:
                try:
                    meta = json.loads(m.metadata_json)
                except (json.JSONDecodeError, TypeError):
                    meta = None
            messages.append(
                {
                    "id": m.id,
                    "role": m.role,
                    "content": m.content,
                    "created_at": m.created_at.timestamp(),
                    "metadata": meta,
                }
            )
        last_query = next(
            (m.content for m in reversed(conv.messages or []) if m.role == "user"),
            None,
        )
        clinical = {}
        if conv.clinical_context_json:
            try:
                clinical = json.loads(conv.clinical_context_json)
            except (json.JSONDecodeError, TypeError):
                clinical = {}
        return {
            "patient": {
                "id": conv.user_id,
                "username": conv.user.email if conv.user else conv.user_id,
                "full_name": conv.user.full_name if conv.user else "",
            },
            "agent_name": agent_name,
            "clinical_context": clinical,
            "messages": messages,
            "reason": conv.handoff_reason,
            "last_query": last_query,
        }

    async def resolve(
        self,
        db: AsyncSession,
        conversation_id: str,
        *,
        agent_id: Optional[str] = None,
        tag: str = "other",
        end_reason: str = "issue_resolved",
        issue_status: str = "resolved",
        comments: Optional[str] = None,
    ) -> dict:
        tag_n = (tag or "other").strip().lower()
        end_n = (end_reason or "issue_resolved").strip().lower()
        status_n = (issue_status or "resolved").strip().lower()
        if tag_n not in RESOLVE_TAGS:
            raise ValidationException(
                f"Invalid tag. Allowed: {', '.join(sorted(RESOLVE_TAGS))}"
            )
        if end_n not in RESOLVE_END_REASONS:
            raise ValidationException(
                f"Invalid end_reason. Allowed: {', '.join(sorted(RESOLVE_END_REASONS))}"
            )
        if status_n not in RESOLVE_ISSUE_STATUSES:
            raise ValidationException(
                f"Invalid issue_status. Allowed: {', '.join(sorted(RESOLVE_ISSUE_STATUSES))}"
            )

        result = await db.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        conv = result.scalar_one_or_none()
        if conv is None:
            raise ConversationNotFoundException(f"Conversation {conversation_id} not found")

        freed_agent = agent_id or conv.assigned_agent_id
        resolution: dict[str, Any] = {
            "tag": tag_n,
            "end_reason": end_n,
            "issue_status": status_n,
            "comments": (comments or "").strip() or None,
            "resolved_by": freed_agent,
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        }
        conv.handoff_resolution_json = json.dumps(resolution)

        agent_name = await self._agent_display_name(db, freed_agent)
        end_bits = [
            f"Conversation ended · {agent_name}",
            f"tag: {tag_n}",
            f"end: {end_n}",
            f"issue: {status_n}",
        ]
        if resolution.get("comments"):
            end_bits.append(str(resolution["comments"])[:160])
        await self._record_event(
            db,
            conversation_id,
            " · ".join(end_bits),
            event_type="resolved",
            agent_id=freed_agent,
            agent_name=agent_name,
            tag=tag_n,
            end_reason=end_n,
            issue_status=status_n,
        )

        await self.persist_state(
            db, conversation_id, STATE_RESOLVED, reason=f"resolved:{tag_n}:{end_n}", agent_id=None
        )
        await self._redis.queue_remove(conversation_id)
        if freed_agent:
            await self._redis.remove_agent_active_session(freed_agent, conversation_id)
        await db.commit()

        await connection_manager.send_to_patient(
            conversation_id,
            {
                "type": "conversation_resolved",
                "state": STATE_RESOLVED,
                "resolution": {
                    "tag": tag_n,
                    "end_reason": end_n,
                    "issue_status": status_n,
                },
            },
        )

        if freed_agent:
            await connection_manager.send_to_agent(
                freed_agent,
                {
                    "type": "conversation_resolved",
                    "session_id": conversation_id,
                    "resolution": resolution,
                    "active_count": await self._redis.get_agent_active_count(freed_agent),
                    "max_active": keys.AGENT_MAX_ACTIVE_SESSIONS,
                },
            )
            # Free slot → pull next waiting patient(s) up to capacity
            while await self._redis.get_agent_active_count(freed_agent) < keys.AGENT_MAX_ACTIVE_SESSIONS:
                routed = await self.try_route_next_available()
                if not routed:
                    break

        return {
            "conversation_id": conversation_id,
            "state": STATE_RESOLVED,
            "agent_id": freed_agent,
            "resolution": resolution,
        }

    async def reconcile_agent_sessions(self, db: AsyncSession, agent_id: str) -> Optional[str]:
        """Restore Redis active_sessions from DB HUMAN_ACTIVE (up to max 5)."""
        result = await db.execute(
            select(Conversation)
            .where(
                Conversation.assigned_agent_id == agent_id,
                Conversation.handoff_state == STATE_HUMAN_ACTIVE,
            )
            .order_by(Conversation.updated_at.desc())
        )
        rows = list(result.scalars().all())
        if not rows:
            return None

        keep = rows[: keys.AGENT_MAX_ACTIVE_SESSIONS]
        for orphan in rows[keys.AGENT_MAX_ACTIVE_SESSIONS :]:
            orphan.handoff_resolution_json = json.dumps(
                {
                    "tag": "other",
                    "end_reason": "unresolved",
                    "issue_status": "not_resolved",
                    "comments": "Auto-closed: exceeded agent capacity on reconnect.",
                    "resolved_by": agent_id,
                    "resolved_at": datetime.now(timezone.utc).isoformat(),
                    "auto": True,
                }
            )
            await self.persist_state(
                db,
                orphan.id,
                STATE_RESOLVED,
                reason="resolved:stale_reconcile",
                agent_id=None,
            )
            await self._redis.queue_remove(orphan.id)

        for conv in keep:
            await self._redis.add_agent_active_session(agent_id, conv.id)

        await db.commit()
        return keep[0].id

    async def requeue_on_agent_disconnect(self, agent_id: str) -> None:
        # Another tab/socket may already have replaced this agent.
        if connection_manager.agent_connected(agent_id):
            return
        sessions = await self._redis.get_agent_active_sessions(agent_id)
        await self._redis.agent_set_offline(agent_id)
        for session_id in sessions:
            payload = await self._redis.get_session_state_payload(session_id)
            queued_at = (payload or {}).get("queued_at")
            async with AsyncSessionLocal() as db:
                agent_name = await self._agent_display_name(db, agent_id)
                await self._record_event(
                    db,
                    session_id,
                    f"Specialist disconnected ({agent_name}) — returned to queue",
                    event_type="agent_disconnected",
                    agent_id=agent_id,
                    agent_name=agent_name,
                )
                await self.persist_state(
                    db, session_id, STATE_QUEUED, reason="agent_disconnected", agent_id=None
                )
                await db.commit()
            await self._redis.remove_agent_active_session(agent_id, session_id)
            await self._redis.queue_add(
                session_id,
                requested_at=float(queued_at) if queued_at is not None else None,
            )
            await connection_manager.send_to_patient(
                session_id,
                {
                    "type": "agent_disconnected",
                    "state": STATE_QUEUED,
                    "message": "Your specialist disconnected. You have been re-queued.",
                },
            )

    async def requeue_on_patient_disconnect(self, session_id: str) -> None:
        if connection_manager.patient_connected(session_id):
            return
        payload = await self._redis.get_session_state_payload(session_id)
        if not payload or payload.get("state") != STATE_HUMAN_ACTIVE:
            return
        agent_id = payload.get("agent_id")
        queued_at = payload.get("queued_at")
        async with AsyncSessionLocal() as db:
            await self.persist_state(
                db, session_id, STATE_QUEUED, reason="patient_disconnected", agent_id=None
            )
            await db.commit()
        await self._redis.queue_add(
            session_id,
            requested_at=float(queued_at) if queued_at is not None else None,
        )
        if agent_id:
            await self._redis.remove_agent_active_session(agent_id, session_id)
            await connection_manager.send_to_agent(
                agent_id,
                {"type": "patient_disconnected", "session_id": session_id},
            )
            while await self._redis.get_agent_active_count(agent_id) < keys.AGENT_MAX_ACTIVE_SESSIONS:
                routed = await self.try_route_next_available()
                if not routed:
                    break

    async def evaluate_quality_triggers(
        self,
        session_id: str,
        *,
        faithfulness_verdict: Optional[str],
        answer_text: str,
    ) -> Optional[dict]:
        """Update fail counters; return handoff payload if thresholds met."""
        try:
            if (faithfulness_verdict or "").upper() == "PASS":
                await self._redis.reset_fail_counts(session_id)
            elif (faithfulness_verdict or "").upper() == "FAIL":
                fail_n = await self._redis.increment_fail_count(
                    session_id, "consecutive_fail_count"
                )
                if fail_n >= FAIL_THRESHOLD:
                    return {
                        "reason": "consecutive_faithfulness_fail",
                        "consecutive_fail_count": fail_n,
                    }

            if (answer_text or "").strip() == GROUNDED_ANSWER_NOT_FOUND:
                nf = await self._redis.increment_fail_count(
                    session_id, "consecutive_not_found_count"
                )
                if nf >= NOT_FOUND_THRESHOLD:
                    return {
                        "reason": "consecutive_not_found",
                        "consecutive_not_found_count": nf,
                    }
            elif (answer_text or "").strip():
                counts = await self._redis.get_fail_counts(session_id)
                if counts.get("consecutive_not_found_count", 0) > 0:
                    await self._redis.set_fail_counts(
                        session_id,
                        consecutive_fail_count=counts.get("consecutive_fail_count", 0),
                        consecutive_not_found_count=0,
                    )
        except Exception as exc:
            log_exception(logger, exc)
        return None

    async def queue_snapshot_for_admin(self, db: AsyncSession) -> list[dict]:
        entries = await self._redis.queue_list()
        enriched: list[dict] = []
        for entry in entries:
            sid = entry["session_id"]
            result = await db.execute(
                select(Conversation)
                .where(Conversation.id == sid)
                .options(selectinload(Conversation.user), selectinload(Conversation.messages))
            )
            conv = result.scalar_one_or_none()
            username = conv.user.email if conv and conv.user else sid
            last_query = None
            if conv and conv.messages:
                for m in reversed(conv.messages):
                    if m.role == "user":
                        last_query = m.content
                        break
            enriched.append({
                **entry,
                "patient_username": username,
                "reason": conv.handoff_reason if conv else None,
                "last_query": last_query,
            })
        return enriched

    async def repair_queue_ghosts(self, db: AsyncSession) -> int:
        """Removes any session from the Redis pending queue whose DB state is
        HUMAN_ACTIVE — a split-brain that can happen if a crash lands between
        the state commit and the queue pop, or after a manual Redis edit.
        Called at startup and periodically (Issue 12 / 16)."""
        result = await db.execute(
            select(Conversation.id).where(Conversation.handoff_state == STATE_HUMAN_ACTIVE)
        )
        active_ids = {row[0] for row in result.all()}
        if not active_ids:
            return 0
        return await self._redis.repair_queue_consistency(active_session_ids=active_ids)

    async def active_handoffs_for_admin(self, db: AsyncSession) -> list[dict]:
        """Conversations currently with a human agent (not waiting in queue)."""
        result = await db.execute(
            select(Conversation)
            .where(Conversation.handoff_state == STATE_HUMAN_ACTIVE)
            .options(selectinload(Conversation.user), selectinload(Conversation.messages))
            .order_by(Conversation.updated_at.desc())
        )
        rows = result.scalars().all()
        agent_ids = {c.assigned_agent_id for c in rows if c.assigned_agent_id}
        agent_names: dict[str, str] = {}
        if agent_ids:
            agents = await db.execute(select(User).where(User.id.in_(agent_ids)))
            for agent in agents.scalars().all():
                agent_names[agent.id] = agent.full_name or agent.email

        active: list[dict] = []
        now = datetime.now(timezone.utc)
        for conv in rows:
            last_query = None
            for m in reversed(conv.messages or []):
                if m.role == "user":
                    last_query = m.content
                    break
            started_at = conv.updated_at
            duration_seconds = None
            if started_at is not None:
                if started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=timezone.utc)
                duration_seconds = max(0, int((now - started_at).total_seconds()))
            active.append({
                "session_id": conv.id,
                "patient_username": conv.user.email if conv.user else conv.user_id,
                "agent_id": conv.assigned_agent_id,
                "agent_name": agent_names.get(conv.assigned_agent_id or "", None),
                "reason": conv.handoff_reason,
                "last_query": last_query,
                "duration_seconds": duration_seconds,
                "message_count": len(conv.messages or []),
            })
        return active
