"""Chat tool endpoints — query enhancement and web search preview."""

from __future__ import annotations

from fastapi import APIRouter

from api.core.auth import CurrentUserDep, DbSessionDep
from api.core.dependencies import LLMServiceDep, SettingsDep, WebSearchServiceDep
from api.core.exceptions import EmptyQueryException
from api.models.schemas import (
    EnhanceQueryRequest,
    EnhanceQueryResponse,
    WebSearchRequest,
    WebSearchResponse,
)
from api.services.llm_usage import track_llm_usage
from api.services.token_usage_service import TokenUsageService

router = APIRouter()
token_service = TokenUsageService()


@router.post("/api/chat/tools/enhance-query", response_model=EnhanceQueryResponse)
async def enhance_query(
    body: EnhanceQueryRequest,
    user: CurrentUserDep,
    db: DbSessionDep,
    llm_service: LLMServiceDep,
    settings: SettingsDep,
) -> EnhanceQueryResponse:
    if not (body.query or "").strip():
        raise EmptyQueryException("Query must not be empty.")

    with track_llm_usage() as collector:
        enhanced = await llm_service.enhance_query(body.query, body.specialty)

    for rec in collector.records:
        await token_service.record(
            db,
            user_id=user.id,
            conversation_id=None,
            operation=rec.operation,
            model=settings.groq_model,
            prompt_tokens=rec.prompt_tokens,
            completion_tokens=rec.completion_tokens,
        )
    if collector.records:
        await db.commit()

    return EnhanceQueryResponse(enhanced_query=enhanced)


@router.post("/api/chat/tools/web-search", response_model=WebSearchResponse)
async def web_search(
    body: WebSearchRequest,
    _user: CurrentUserDep,
    web_search_service: WebSearchServiceDep,
) -> WebSearchResponse:
    if not (body.query or "").strip():
        raise EmptyQueryException("Query must not be empty.")
    max_results = max(1, min(body.max_results, 10))
    results = await web_search_service.search_pubmed(body.query, max_results=max_results)
    return WebSearchResponse(results=results)
