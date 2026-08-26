"""Conversation persistence, context window, and rolling summaries."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.core.config import Settings
from api.core.exceptions import ConversationNotFoundException, MessageNotFoundException
from api.core.logger import get_logger, log_exception
from api.models.db_models import Conversation, Message, MessageFeedback, User
from api.services.llm_service import LLMService

logger = get_logger(__name__)


class ConversationService:
    async def create(
        self,
        db: AsyncSession,
        user_id: str,
        title: str = "New conversation",
    ) -> Conversation:
        conv = Conversation(user_id=user_id, title=title)
        db.add(conv)
        await db.flush()
        return conv

    async def list_for_user(
        self,
        db: AsyncSession,
        user_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Conversation]:
        result = await db.execute(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def get_owned(
        self,
        db: AsyncSession,
        user_id: str,
        conversation_id: str,
        *,
        with_messages: bool = False,
    ) -> Conversation:
        stmt = select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
        if with_messages:
            stmt = stmt.options(
                selectinload(Conversation.messages).selectinload(Message.feedback)
            )
        result = await db.execute(stmt)
        conv = result.scalar_one_or_none()
        if conv is None:
            raise ConversationNotFoundException(f"Conversation {conversation_id} not found.")
        return conv

    async def delete(self, db: AsyncSession, user_id: str, conversation_id: str) -> None:
        conv = await self.get_owned(db, user_id, conversation_id)
        await db.delete(conv)

    async def update_title(
        self,
        db: AsyncSession,
        user_id: str,
        conversation_id: str,
        title: str,
    ) -> Conversation:
        conv = await self.get_owned(db, user_id, conversation_id)
        conv.title = title.strip() or "New conversation"
        conv.updated_at = datetime.now(timezone.utc)
        await db.flush()
        return conv

    async def save_clinical_context(
        self,
        db: AsyncSession,
        conversation_id: str,
        context: dict[str, Any],
    ) -> None:
        result = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
        conv = result.scalar_one_or_none()
        if conv is None:
            return
        conv.clinical_context_json = json.dumps(context)
        conv.updated_at = datetime.now(timezone.utc)
        await db.flush()

    async def load_clinical_context(self, conv: Conversation) -> dict[str, Any]:
        if not conv.clinical_context_json:
            return {}
        try:
            return json.loads(conv.clinical_context_json)
        except json.JSONDecodeError:
            return {}

    async def add_message(
        self,
        db: AsyncSession,
        conversation_id: str,
        role: str,
        content: str,
        *,
        metadata: Optional[dict] = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> Message:
        msg = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            metadata_json=json.dumps(metadata) if metadata else None,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        db.add(msg)

        result = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
        conv = result.scalar_one_or_none()
        if conv:
            conv.updated_at = datetime.now(timezone.utc)
            # Auto-title from first user message
            if conv.title == "New conversation" and role == "user":
                conv.title = content[:80] + ("…" if len(content) > 80 else "")

        await db.flush()
        return msg

    async def upsert_message_feedback(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        conversation_id: str,
        message_id: str,
        rating: str,
        comment: Optional[str] = None,
        correct_answer: Optional[str] = None,
    ) -> MessageFeedback:
        rating_norm = (rating or "").strip().lower()
        if rating_norm not in ("up", "down"):
            from api.core.exceptions import ValidationException

            raise ValidationException('rating must be "up" or "down"')

        await self.get_owned(db, user_id, conversation_id)

        result = await db.execute(
            select(Message).where(
                Message.id == message_id,
                Message.conversation_id == conversation_id,
            )
        )
        message = result.scalar_one_or_none()
        if message is None:
            raise MessageNotFoundException(f"Message {message_id} not found.")
        if message.role != "assistant":
            from api.core.exceptions import ValidationException

            raise ValidationException("Feedback is only allowed on assistant messages.")

        existing = await db.execute(
            select(MessageFeedback).where(MessageFeedback.message_id == message_id)
        )
        feedback = existing.scalar_one_or_none()
        if feedback is None:
            feedback = MessageFeedback(
                message_id=message_id,
                user_id=user_id,
                conversation_id=conversation_id,
                rating=rating_norm,
                comment=(comment or "").strip() or None,
                correct_answer=(correct_answer or "").strip() or None,
            )
            db.add(feedback)
        else:
            feedback.rating = rating_norm
            feedback.comment = (comment or "").strip() or None
            feedback.correct_answer = (correct_answer or "").strip() or None
            feedback.updated_at = datetime.now(timezone.utc)

        # Thumbs-up clears free-text fields
        if rating_norm == "up":
            feedback.comment = None
            feedback.correct_answer = None

        await db.flush()
        return feedback

    async def get_messages(
        self,
        db: AsyncSession,
        conversation_id: str,
    ) -> list[Message]:
        result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc())
        )
        return list(result.scalars().all())

    async def build_llm_context(
        self,
        db: AsyncSession,
        conversation: Conversation,
        settings: Settings,
    ) -> tuple[list[dict], Optional[str]]:
        """Return (recent_messages_for_llm, summary_string).

        Passes the permanent conversation summary plus the last N turn pairs
        (recent_turns_for_context) so the model retains long-term context without
        blowing the context window.
        """
        messages = await self.get_messages(db, conversation.id)
        chat_only = [m for m in messages if m.role in ("user", "assistant")]
        recent_limit = settings.recent_turns_for_context * 2  # user + assistant pairs
        recent = chat_only[-recent_limit:] if recent_limit else chat_only

        history = [
            {"role": m.role, "content": m.content, "timestamp": m.created_at.timestamp()}
            for m in recent
        ]
        summary = conversation.summary if conversation.summary else None
        return history, summary

    async def maybe_update_summary(
        self,
        db: AsyncSession,
        conversation_id: str,
        llm_service: LLMService,
        settings: Settings,
    ) -> None:
        """Summarize older messages permanently when count exceeds threshold."""
        messages = await self.get_messages(db, conversation_id)
        chat_only = [m for m in messages if m.role in ("user", "assistant")]
        if len(chat_only) < settings.summary_trigger_messages:
            return

        result = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
        conv = result.scalar_one_or_none()
        if conv is None:
            return

        recent_limit = settings.recent_turns_for_context * 2
        older = chat_only[:-recent_limit] if recent_limit else []
        if not older:
            return

        # Skip if we already summarized these messages
        if (conv.summary_covers_message_count or 0) >= len(older):
            return

        try:
            summary, _usage = await llm_service.summarize_conversation(
                older_messages=[
                    {"role": m.role, "content": m.content} for m in older
                ],
                existing_summary=conv.summary,
            )
        except Exception as exc:
            log_exception(logger, exc)
            return

        conv.summary = summary
        conv.summary_covers_message_count = len(older)
        conv.summary_updated_at = datetime.now(timezone.utc)
        await db.flush()

    async def message_count(self, db: AsyncSession, conversation_id: str) -> int:
        result = await db.execute(
            select(func.count(Message.id)).where(Message.conversation_id == conversation_id)
        )
        return int(result.scalar_one())

    async def ensure_access(
        self,
        db: AsyncSession,
        user: User,
        conversation_id: str,
    ) -> Conversation:
        conv = await self.get_owned(db, user.id, conversation_id)
        return conv
