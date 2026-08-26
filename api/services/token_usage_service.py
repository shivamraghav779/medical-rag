"""Token usage persistence."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from api.models.db_models import TokenUsage


class TokenUsageService:
    async def record(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        conversation_id: str | None,
        operation: str,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> None:
        total = prompt_tokens + completion_tokens
        db.add(
            TokenUsage(
                user_id=user_id,
                conversation_id=conversation_id,
                operation=operation,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total,
            )
        )
        await db.flush()
