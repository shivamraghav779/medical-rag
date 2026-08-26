"""Conversations router — list, create, read, update, delete."""

from __future__ import annotations

import json

from fastapi import APIRouter
from sqlalchemy import func, select

from api.core.auth import CurrentUserDep, DbSessionDep
from api.models.db_models import Message
from api.models.schemas import (
    ConversationCreateRequest,
    ConversationDetail,
    ConversationSummary,
    ConversationUpdateRequest,
    MessageFeedbackRequest,
    MessageFeedbackResponse,
    MessageResponse,
)
from api.services.conversation_service import ConversationService

conv_service = ConversationService()


def _message_response(m) -> MessageResponse:
    meta = None
    if m.metadata_json:
        try:
            meta = json.loads(m.metadata_json)
        except json.JSONDecodeError:
            meta = None
    # Never trigger async lazy-loads (MissingGreenlet) — only use feedback if already loaded.
    fb = None
    try:
        from sqlalchemy import inspect as sa_inspect

        unloaded = sa_inspect(m).unloaded
        if "feedback" not in unloaded:
            fb = m.feedback
    except Exception:
        fb = None
    return MessageResponse(
        id=m.id,
        role=m.role,
        content=m.content,
        created_at=m.created_at.timestamp(),
        metadata=meta,
        feedback_rating=fb.rating if fb else None,
        feedback_comment=fb.comment if fb else None,
        feedback_correct_answer=fb.correct_answer if fb else None,
    )


class ConversationsRouter:
    def __init__(self) -> None:
        self.router = APIRouter(prefix="/api/conversations", tags=["conversations"])
        self.register(self.router)

    def register(self, router: APIRouter) -> None:
        @router.get("", response_model=list[ConversationSummary])
        async def list_conversations(
            user: CurrentUserDep,
            db: DbSessionDep,
            limit: int = 50,
            offset: int = 0,
        ) -> list[ConversationSummary]:
            convs = await conv_service.list_for_user(db, user.id, limit=limit, offset=offset)
            summaries: list[ConversationSummary] = []
            for conv in convs:
                count_result = await db.execute(
                    select(func.count(Message.id)).where(Message.conversation_id == conv.id)
                )
                count = int(count_result.scalar_one())
                summaries.append(
                    ConversationSummary(
                        id=conv.id,
                        title=conv.title,
                        summary=conv.summary,
                        message_count=count,
                        created_at=conv.created_at.timestamp(),
                        updated_at=conv.updated_at.timestamp(),
                    )
                )
            return summaries

        @router.post("", response_model=ConversationSummary)
        async def create_conversation(
            body: ConversationCreateRequest,
            user: CurrentUserDep,
            db: DbSessionDep,
        ) -> ConversationSummary:
            conv = await conv_service.create(db, user.id, title=body.title)
            await db.commit()
            return ConversationSummary(
                id=conv.id,
                title=conv.title,
                summary=None,
                message_count=0,
                created_at=conv.created_at.timestamp(),
                updated_at=conv.updated_at.timestamp(),
            )

        @router.get("/{conversation_id}", response_model=ConversationDetail)
        async def get_conversation(
            conversation_id: str,
            user: CurrentUserDep,
            db: DbSessionDep,
        ) -> ConversationDetail:
            conv = await conv_service.get_owned(
                db, user.id, conversation_id, with_messages=True
            )
            clinical = await conv_service.load_clinical_context(conv)
            messages = [_message_response(m) for m in conv.messages]
            return ConversationDetail(
                id=conv.id,
                title=conv.title,
                summary=conv.summary,
                summary_updated_at=(
                    conv.summary_updated_at.timestamp() if conv.summary_updated_at else None
                ),
                clinical_context=clinical or None,
                messages=messages,
                created_at=conv.created_at.timestamp(),
                updated_at=conv.updated_at.timestamp(),
                handoff_state=getattr(conv, "handoff_state", None) or "BOT_ACTIVE",
                handoff_reason=getattr(conv, "handoff_reason", None),
                assigned_agent_id=getattr(conv, "assigned_agent_id", None),
            )

        @router.patch("/{conversation_id}", response_model=ConversationSummary)
        async def update_conversation(
            conversation_id: str,
            body: ConversationUpdateRequest,
            user: CurrentUserDep,
            db: DbSessionDep,
        ) -> ConversationSummary:
            conv = await conv_service.get_owned(db, user.id, conversation_id)
            if body.title is not None:
                conv = await conv_service.update_title(db, user.id, conversation_id, body.title)
            await db.commit()
            count = await conv_service.message_count(db, conversation_id)
            return ConversationSummary(
                id=conv.id,
                title=conv.title,
                summary=conv.summary,
                message_count=count,
                created_at=conv.created_at.timestamp(),
                updated_at=conv.updated_at.timestamp(),
            )

        @router.post(
            "/{conversation_id}/messages/{message_id}/feedback",
            response_model=MessageFeedbackResponse,
        )
        async def submit_message_feedback(
            conversation_id: str,
            message_id: str,
            body: MessageFeedbackRequest,
            user: CurrentUserDep,
            db: DbSessionDep,
        ) -> MessageFeedbackResponse:
            feedback = await conv_service.upsert_message_feedback(
                db,
                user_id=user.id,
                conversation_id=conversation_id,
                message_id=message_id,
                rating=body.rating,
                comment=body.comment,
                correct_answer=body.correct_answer,
            )
            await db.commit()
            return MessageFeedbackResponse(
                success=True,
                message_id=message_id,
                rating=feedback.rating,
                comment=feedback.comment,
                correct_answer=feedback.correct_answer,
            )

        @router.delete("/{conversation_id}")
        async def delete_conversation(
            conversation_id: str,
            user: CurrentUserDep,
            db: DbSessionDep,
        ) -> dict:
            await conv_service.delete(db, user.id, conversation_id)
            await db.commit()
            return {"success": True, "conversation_id": conversation_id}


conversations_router = ConversationsRouter()
router = conversations_router.router
