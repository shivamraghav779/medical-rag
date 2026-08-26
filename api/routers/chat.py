"""Chat router — authenticated SSE streaming with persistent conversations."""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from api.core.auth import CurrentUserDep
from api.core.database import AsyncSessionLocal
from api.core.dependencies import OrchestratorDep, RedisServiceDep, SettingsDep
from api.core.exceptions import EmptyQueryException, RateLimitException, RedisConnectionException
from api.core.logger import get_logger, log_exception
from api.models.schemas import ChatRequest
from api.services.conversation_service import ConversationService
from api.services.llm_usage import track_llm_usage
from api.services.token_usage_service import TokenUsageService

logger = get_logger(__name__)
conv_service = ConversationService()
token_service = TokenUsageService()


async def _persist_usage(
    db,
    *,
    user_id: str,
    conversation_id: str | None,
    model: str,
    collector,
) -> None:
    for rec in collector.records:
        await token_service.record(
            db,
            user_id=user_id,
            conversation_id=conversation_id,
            operation=rec.operation,
            model=model,
            prompt_tokens=rec.prompt_tokens,
            completion_tokens=rec.completion_tokens,
        )


def _client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class ChatRouter:
    def __init__(self) -> None:
        self.router = APIRouter()
        self.register(self.router)

    def register(self, app_or_router) -> None:
        @app_or_router.post("/api/chat")
        async def chat(
            request: Request,
            chat_request: ChatRequest,
            user: CurrentUserDep,
            orchestrator: OrchestratorDep,
            redis_service: RedisServiceDep,
            settings: SettingsDep,
        ) -> EventSourceResponse:
            return await self._chat(
                request, chat_request, user, orchestrator, redis_service, settings
            )

    async def _chat(
        self,
        request: Request,
        chat_request: ChatRequest,
        user,
        orchestrator,
        redis_service,
        settings,
    ) -> EventSourceResponse:
        if not (chat_request.query or "").strip():
            raise EmptyQueryException("Query must not be empty.")

        rate_key = f"user:{user.id}"
        try:
            allowed, retry_after = await redis_service.check_rate_limit(rate_key)
        except RedisConnectionException as exc:
            log_exception(logger, exc)
            allowed, retry_after = True, 0

        if not allowed:
            raise RateLimitException(
                "Rate limit exceeded. Max 20 requests per minute.",
                retry_after_seconds=retry_after,
            )

        async def event_stream():
            answer_parts: list[str] = []
            async with AsyncSessionLocal() as db:
                try:
                    if chat_request.conversation_id:
                        conv = await conv_service.get_owned(
                            db, user.id, chat_request.conversation_id
                        )
                    else:
                        conv = await conv_service.create(db, user.id)
                        await db.commit()

                    conversation_id = conv.id
                    yield {
                        "data": json.dumps({
                            "type": "conversation",
                            "conversation_id": conversation_id,
                        })
                    }

                    # Merge clinical context
                    stored = await conv_service.load_clinical_context(conv)
                    session_context = dict(stored)
                    if chat_request.specialty is not None:
                        session_context["specialty"] = chat_request.specialty
                    if chat_request.patient_age_group is not None:
                        session_context["patient_age_group"] = chat_request.patient_age_group
                    if chat_request.patient_weight_kg is not None:
                        session_context["patient_weight_kg"] = chat_request.patient_weight_kg
                    session_context.setdefault("disclaimer_shown", False)
                    session_context.setdefault("query_count", 0)

                    await conv_service.save_clinical_context(db, conversation_id, session_context)
                    await db.commit()

                    history, summary = await conv_service.build_llm_context(db, conv, settings)

                    with track_llm_usage() as usage_collector:
                        async for event in orchestrator.run(
                            chat_request.query,
                            conversation_id,
                            chat_request.doc_names,
                            session_context,
                            conversation_history=history,
                            conversation_summary=summary,
                            persist_redis_history=False,
                            enable_web_search=chat_request.enable_web_search,
                        ):
                            if event.get("type") == "token":
                                answer_parts.append(event.get("content", ""))
                            yield {"data": json.dumps(event)}

                        full_answer = "".join(answer_parts)
                        if full_answer.strip():
                            user_msg = await conv_service.add_message(
                                db, conversation_id, "user", chat_request.query
                            )
                            assistant_msg = await conv_service.add_message(
                                db, conversation_id, "assistant", full_answer
                            )
                            await conv_service.maybe_update_summary(
                                db,
                                conversation_id,
                                orchestrator._llm,
                                settings,
                            )
                            await _persist_usage(
                                db,
                                user_id=user.id,
                                conversation_id=conversation_id,
                                model=settings.groq_model,
                                collector=usage_collector,
                            )
                            await db.commit()
                            yield {
                                "data": json.dumps({
                                    "type": "message_saved",
                                    "conversation_id": conversation_id,
                                    "user_message_id": user_msg.id,
                                    "assistant_message_id": assistant_msg.id,
                                })
                            }
                        elif usage_collector.records:
                            await _persist_usage(
                                db,
                                user_id=user.id,
                                conversation_id=conversation_id,
                                model=settings.groq_model,
                                collector=usage_collector,
                            )
                            await db.commit()

                except Exception as exc:
                    log_exception(logger, exc)
                    await db.rollback()
                    yield {
                        "data": json.dumps({
                            "type": "error",
                            "message": str(exc),
                        })
                    }
                    yield {"data": json.dumps({"type": "done"})}

        return EventSourceResponse(event_stream())


chat_router = ChatRouter()
router = chat_router.router
