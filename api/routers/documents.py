"""Documents router — list, delete, and analytics."""

from __future__ import annotations

from fastapi import APIRouter

from api.core.config import Settings
from api.core.dependencies import RedisServiceDep, RetrievalServiceDep, SettingsDep
from api.core.exceptions import DocumentNotFoundException
from api.core.logger import get_logger, log_exception
from api.models.clinical_schemas import AnalyticsResponse
from api.models.schemas import DocumentInfo

logger = get_logger(__name__)


class DocumentRouter:
    def __init__(self) -> None:
        self.router = APIRouter()
        self.register(self.router)

    def register(self, app_or_router) -> None:
        @app_or_router.get("/api/documents", response_model=list[DocumentInfo])
        async def list_documents(redis_service: RedisServiceDep) -> list[DocumentInfo]:
            return await self._list(redis_service)

        @app_or_router.delete("/api/documents/{doc_id}")
        async def delete_document(
            doc_id: str,
            redis_service: RedisServiceDep,
            retrieval_service: RetrievalServiceDep,
            settings: SettingsDep,
        ) -> dict:
            return await self._delete(doc_id, redis_service, retrieval_service, settings)

        @app_or_router.get("/api/analytics", response_model=AnalyticsResponse)
        async def analytics(redis_service: RedisServiceDep) -> AnalyticsResponse:
            return await self._analytics(redis_service)

    async def _list(self, redis_service) -> list[DocumentInfo]:
        docs = await redis_service.list_all_documents()
        return [
            DocumentInfo(
                doc_id=doc.get("doc_id", ""),
                doc_name=doc.get("doc_name", "unknown"),
                chunk_count=int(doc.get("chunk_count", 0)),
                doc_type=doc.get("doc_type"),
                source_org=doc.get("source_org"),
                authority_level=int(doc["authority_level"]) if doc.get("authority_level") is not None else None,
                version=doc.get("version"),
                publication_year=int(doc["publication_year"]) if doc.get("publication_year") is not None else None,
                upload_timestamp=float(doc["upload_timestamp"]) if doc.get("upload_timestamp") is not None else None,
                parse_method=doc.get("parse_method"),
            )
            for doc in docs
        ]

    async def _delete(
        self,
        doc_id: str,
        redis_service,
        retrieval_service,
        settings: Settings,
    ) -> dict:
        metadata = await redis_service.get_document_metadata(doc_id)
        if metadata is None:
            raise DocumentNotFoundException(f"Document {doc_id} not found.")

        chunk_ids: list[str] = metadata.get("chunk_ids", [])

        if chunk_ids:
            batch_size = settings.pinecone_upsert_batch_size
            for i in range(0, len(chunk_ids), batch_size):
                batch = chunk_ids[i:i + batch_size]
                try:
                    await retrieval_service.delete_vectors(batch)
                except Exception as exc:
                    log_exception(logger, exc)

        await redis_service.delete_document_data(doc_id)
        await redis_service.invalidate_bm25_cache()

        return {
            "success": True,
            "doc_id": doc_id,
            "doc_name": metadata.get("doc_name", "unknown"),
            "message": f"Deleted document {doc_id} and {len(chunk_ids)} chunks.",
        }

    async def _analytics(self, redis_service) -> AnalyticsResponse:
        data = await redis_service.get_all_analytics()
        return AnalyticsResponse(**data)


document_router = DocumentRouter()
router = document_router.router
