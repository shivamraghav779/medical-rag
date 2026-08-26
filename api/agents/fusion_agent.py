from __future__ import annotations

from typing import Optional

from api.agents.base import BaseAgent
from api.models.schemas import AgentResult, RetrievedChunk
from api.services.retrieval_service import RetrievalService

from api.core.constants import FUSED_TOP_K

class FusionAgent(BaseAgent):
    name = "Fusion Agent"

    def __init__(self, retrieval_service: RetrievalService, session_id: Optional[str] = None):
        super().__init__(session_id=session_id)
        self._retrieval = retrieval_service

    async def run(
        self,
        dense_results: list[RetrievedChunk] | None = None,
        sparse_results: list[RetrievedChunk] | None = None,
        **kwargs,
    ) -> AgentResult:
        dense = dense_results or []
        sparse = sparse_results or []
        with self.track_duration() as timing:
            fused = await self._retrieval.rrf_fusion(dense, sparse)
            fused = fused[:FUSED_TOP_K]
        return AgentResult(
            success=True,
            data=fused,
            output=f"{len(fused)} fused candidates",
            duration_ms=timing["duration_ms"],
        )
