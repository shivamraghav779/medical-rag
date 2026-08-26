from __future__ import annotations

import asyncio
from typing import Optional

from api.agents.base import BaseAgent
from api.core.config import Settings
from api.models.schemas import AgentResult, RetrievedChunk
from api.services.embedding_service import EmbeddingService
from api.services.retrieval_service import RetrievalService

from api.core.constants import MAX_DENSE_CANDIDATES

class DenseRetrieverAgent(BaseAgent):
    name = "Dense Retriever"

    def __init__(
        self,
        embedding_service: EmbeddingService,
        retrieval_service: RetrievalService,
        settings: Settings,
        session_id: Optional[str] = None,
    ):
        super().__init__(session_id=session_id)
        self._embedding = embedding_service
        self._retrieval = retrieval_service
        self._settings = settings

    async def run(
        self,
        sub_queries: list[str] | None = None,
        doc_filter: Optional[list[str]] = None,
        doc_type_filter: Optional[list[str]] = None,
        **kwargs,
    ) -> AgentResult:
        queries = sub_queries or []
        with self.track_duration() as timing:
            embeddings = await asyncio.gather(
                *(self._embedding.get_embedding(q, "search_query") for q in queries)
            )
            result_lists = await asyncio.gather(
                *(
                    self._retrieval.dense_search(
                        emb,
                        self._settings.dense_top_k,
                        doc_filter=doc_filter,
                        doc_type_filter=doc_type_filter,
                    )
                    for emb in embeddings
                )
            )

            deduped: dict[str, RetrievedChunk] = {}
            for results in result_lists:
                for chunk in results:
                    existing = deduped.get(chunk.chunk_id)
                    if existing is None or chunk.score > existing.score:
                        deduped[chunk.chunk_id] = chunk

            top_candidates = sorted(
                deduped.values(), key=lambda c: c.score, reverse=True
            )[:MAX_DENSE_CANDIDATES]
            ranked = [
                chunk.model_copy(update={"rank": rank})
                for rank, chunk in enumerate(top_candidates, start=1)
            ]

        return AgentResult(
            success=True,
            data=ranked,
            output=f"{len(ranked)} candidates retrieved",
            duration_ms=timing["duration_ms"],
        )
