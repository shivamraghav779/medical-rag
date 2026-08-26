"""FAQ router — list frequently asked clinical questions."""

from __future__ import annotations

from fastapi import APIRouter, Query

from api.core.auth import CurrentUserDep, DbSessionDep
from api.core.dependencies import EmbeddingServiceDep
from api.models.schemas import FaqListResponse
from api.services.faq_service import FaqService


class FaqRouter:
    def __init__(self) -> None:
        self.router = APIRouter(prefix="/api/faq", tags=["faq"])
        self.register(self.router)

    def register(self, router: APIRouter) -> None:
        @router.get("", response_model=FaqListResponse)
        async def list_faqs(
            user: CurrentUserDep,
            db: DbSessionDep,
            embedding_service: EmbeddingServiceDep,
            limit: int = Query(default=50, ge=1, le=100),
            offset: int = Query(default=0, ge=0),
            query_type: str | None = None,
            sort: str = Query(default="count", pattern="^(count|recent)$"),
        ) -> FaqListResponse:
            service = FaqService(embedding_service)
            items, total = await service.list_faqs(
                db,
                limit=limit,
                offset=offset,
                query_type=query_type,
                sort=sort,
            )
            return FaqListResponse(items=items, total=total)


faq_router = FaqRouter()
router = faq_router.router
