from __future__ import annotations

from typing import Optional

from api.agents.base import BaseAgent
from api.core.config import Settings
from api.core.logger import log_exception
from api.models.schemas import AgentResult
from api.services.retrieval_service import RetrievalService


class SparseRetrieverAgent(BaseAgent):
    name = "Sparse Retriever"

    def __init__(
        self,
        retrieval_service: RetrievalService,
        settings: Settings,
        session_id: Optional[str] = None,
    ):
        super().__init__(session_id=session_id)
        self._retrieval = retrieval_service
        self._settings = settings

    async def run(self, query: str = "", **kwargs) -> AgentResult:
        with self.track_duration() as timing:
            try:
                results = await self._retrieval.sparse_search(
                    query, self._settings.sparse_top_k
                )
                output = f"{len(results)} candidates retrieved"
            except Exception as exc:
                # Never block the chat pipeline on sparse/BM25 failures —
                # dense results alone are enough for fusion + generation.
                log_exception(self.logger, exc)
                results = []
                output = "0 candidates (sparse fallback)"

        return AgentResult(
            success=True,
            data=results,
            output=output,
            duration_ms=timing["duration_ms"],
        )
