from __future__ import annotations

from typing import Optional

from api.agents.base import BaseAgent
from api.models.schemas import AgentResult, QueryAnalysis
from api.services.llm_service import LLMService


class QueryAnalyzerAgent(BaseAgent):
    name = "Query Analyzer"

    def __init__(self, llm_service: LLMService, session_id: Optional[str] = None):
        super().__init__(session_id=session_id)
        self._llm = llm_service

    async def run(
        self,
        query: str,
        conversation_history: list[dict] | None = None,
        conversation_summary: str | None = None,
        **kwargs,
    ) -> AgentResult:
        history = conversation_history or []
        with self.track_duration() as timing:
            analysis: QueryAnalysis = await self._llm.analyze_query(
                query, history, conversation_summary=conversation_summary
            )
        return AgentResult(
            success=True,
            data=analysis,
            output=(
                f"{len(analysis.expanded_queries)} sub-queries generated"
                + ("" if analysis.requires_retrieval else " (retrieval skipped)")
            ),
            duration_ms=timing["duration_ms"],
        )
