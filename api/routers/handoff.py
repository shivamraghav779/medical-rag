"""REST endpoints for human handoff queue and agent registration."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from api.core.auth import AdminUserDep, AgentUserDep, CurrentUserDep, DbSessionDep
from api.core.dependencies import RedisServiceDep
from api.core.exceptions import ConversationNotFoundException
from api.core import redis_keys as keys
from api.models.db_models import Conversation, Message, User
from api.models.schemas import (
    ActiveHandoffEntry,
    AgentRegisterRequest,
    AgentRegisterResponse,
    AgentStatusItem,
    AgentStatusListResponse,
    CancelHandoffResponse,
    ConversationDetail,
    ForceResolveResponse,
    HandoffRequestBody,
    HandoffRequestResponse,
    QueueEntry,
    QueueStateResponse,
    ResolveHandoffRequest,
    ResolveHandoffResponse,
    TakeNextPatientResponse,
)
from api.services.conversation_service import ConversationService
from api.services.handoff_service import HandoffService
from api.routers.conversations import _message_response

router = APIRouter(tags=["handoff"])


@router.post("/api/handoff/request", response_model=HandoffRequestResponse)
async def request_handoff(
    body: HandoffRequestBody,
    user: CurrentUserDep,
    db: DbSessionDep,
    redis_service: RedisServiceDep,
) -> HandoffRequestResponse:
    service = HandoffService(redis_service)
    result = await service.request_handoff(
        db,
        body.conversation_id,
        reason=body.reason or "patient_request",
        user_id=user.id,
    )
    return HandoffRequestResponse(**result)


@router.post("/api/handoff/cancel/{session_id}", response_model=CancelHandoffResponse)
async def cancel_handoff(
    session_id: str,
    user: CurrentUserDep,
    db: DbSessionDep,
    redis_service: RedisServiceDep,
) -> CancelHandoffResponse:
    """Patient-only: leave the queue before an agent has taken them. Once
    HUMAN_ACTIVE, use /api/handoff/resolve (agent) or force-resolve (admin)
    instead — this endpoint deliberately doesn't touch an active handoff."""
    service = HandoffService(redis_service)
    result = await service.cancel_handoff(db, session_id, user_id=user.id)
    return CancelHandoffResponse(**result)


@router.get("/api/handoff/queue", response_model=QueueStateResponse)
async def get_handoff_queue(
    user: AgentUserDep,
    db: DbSessionDep,
    redis_service: RedisServiceDep,
) -> QueueStateResponse:
    service = HandoffService(redis_service)
    entries = await service.queue_snapshot_for_admin(db)
    active = await service.active_handoffs_for_admin(db)
    return QueueStateResponse(
        length=len(entries),
        entries=[QueueEntry(**e) for e in entries],
        active=[ActiveHandoffEntry(**e) for e in active],
    )


@router.post("/api/handoff/resolve/{session_id}", response_model=ResolveHandoffResponse)
async def resolve_handoff(
    session_id: str,
    body: ResolveHandoffRequest,
    user: AgentUserDep,
    db: DbSessionDep,
    redis_service: RedisServiceDep,
) -> ResolveHandoffResponse:
    service = HandoffService(redis_service)
    result = await service.resolve(
        db,
        session_id,
        agent_id=user.id,
        tag=body.tag,
        end_reason=body.end_reason,
        issue_status=body.issue_status,
        comments=body.comments,
    )
    return ResolveHandoffResponse(**result)


@router.delete("/api/handoff/force-resolve/{session_id}", response_model=ForceResolveResponse)
async def force_resolve(
    session_id: str,
    admin: AdminUserDep,
    db: DbSessionDep,
    redis_service: RedisServiceDep,
) -> ForceResolveResponse:
    """Admin-only: resolve a conversation regardless of which agent owns it,
    freeing the agent's slot and notifying both sides. (Issue 26)"""
    service = HandoffService(redis_service)
    result = await service.resolve(
        db,
        session_id,
        agent_id=None,  # resolve() falls back to conv.assigned_agent_id
        tag="other",
        end_reason="transferred",
        issue_status="not_resolved",
        comments=f"Force-resolved by admin {admin.email}",
    )
    return ForceResolveResponse(
        success=True,
        session_id=session_id,
        agent_id=result.get("agent_id"),
    )


@router.post("/api/handoff/take-next", response_model=TakeNextPatientResponse)
async def take_next_patient(
    user: AgentUserDep,
    redis_service: RedisServiceDep,
) -> TakeNextPatientResponse:
    """Route the longest-waiting patient to this agent if under capacity."""
    await redis_service.agent_set_online(user.id)
    active_count = await redis_service.get_agent_active_count(user.id)
    if active_count >= keys.AGENT_MAX_ACTIVE_SESSIONS:
        return TakeNextPatientResponse(
            assigned=False,
            message=f"At capacity ({active_count}/{keys.AGENT_MAX_ACTIVE_SESSIONS}).",
        )

    service = HandoffService(redis_service)
    waiting = await redis_service.queue_length()
    if waiting <= 0:
        return TakeNextPatientResponse(
            assigned=False,
            message="No patients waiting.",
        )

    routed = await service.try_route_to_agent(user.id)
    if not routed:
        return TakeNextPatientResponse(
            assigned=False,
            message="Could not assign patient — try again.",
        )
    return TakeNextPatientResponse(
        assigned=True,
        conversation_id=routed.get("session_id"),
        message="Patient assigned.",
    )


@router.get("/api/agents/status", response_model=AgentStatusListResponse)
async def agents_status(
    user: AgentUserDep,
    db: DbSessionDep,
    redis_service: RedisServiceDep,
) -> AgentStatusListResponse:
    statuses = await redis_service.agent_list_statuses()
    items: list[AgentStatusItem] = []
    for row in statuses:
        agent_id = row.get("agent_id")
        full_name = None
        if agent_id:
            result = await db.execute(select(User).where(User.id == agent_id))
            agent = result.scalar_one_or_none()
            full_name = agent.full_name if agent else None
        items.append(
            AgentStatusItem(
                agent_id=str(agent_id),
                status=str(row.get("status") or "available"),
                last_freed_at=row.get("last_freed_at"),
                current_session_id=row.get("current_session_id"),
                full_name=full_name,
                active_count=int(row.get("active_count") or 0),
                max_active=int(row.get("max_active") or 5),
                active_sessions=list(row.get("active_sessions") or []),
            )
        )
    return AgentStatusListResponse(agents=items)


@router.post("/api/agents/register", response_model=AgentRegisterResponse)
async def register_agent(
    body: AgentRegisterRequest,
    user: AgentUserDep,
    db: DbSessionDep,
    redis_service: RedisServiceDep,
) -> AgentRegisterResponse:
    agent_id = body.agent_id or user.id
    if agent_id != user.id and (user.role or "") != "admin":
        agent_id = user.id
    await redis_service.agent_set_online(agent_id)
    service = HandoffService(redis_service)
    # Restore Redis active set from DB only — do not auto-drain the queue here.
    # The agent WebSocket handler fills capacity once on connect.
    await service.reconcile_agent_sessions(db, agent_id)
    active_count = await redis_service.get_agent_active_count(agent_id)
    return AgentRegisterResponse(
        agent_id=agent_id,
        status=(
            "available"
            if active_count < keys.AGENT_MAX_ACTIVE_SESSIONS
            else "full"
        ),
        online=True,
    )


@router.get("/api/handoff/conversation/{session_id}", response_model=ConversationDetail)
async def get_handoff_conversation(
    session_id: str,
    user: AgentUserDep,
    db: DbSessionDep,
) -> ConversationDetail:
    """Agents can load any queued/active conversation for handoff context."""
    result = await db.execute(
        select(Conversation)
        .where(Conversation.id == session_id)
        .options(
            selectinload(Conversation.messages).selectinload(Message.feedback),
        )
    )
    conv = result.scalar_one_or_none()
    if conv is None:
        raise ConversationNotFoundException(f"Conversation {session_id} not found")
    clinical = await ConversationService().load_clinical_context(conv)

    return ConversationDetail(
        id=conv.id,
        title=conv.title,
        summary=conv.summary,
        clinical_context=clinical or None,
        messages=[_message_response(m) for m in (conv.messages or [])],
        created_at=conv.created_at.timestamp(),
        updated_at=conv.updated_at.timestamp(),
        handoff_state=conv.handoff_state or "BOT_ACTIVE",
        handoff_reason=conv.handoff_reason,
        assigned_agent_id=conv.assigned_agent_id,
    )
