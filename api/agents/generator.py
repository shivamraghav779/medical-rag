from __future__ import annotations

from typing import AsyncGenerator, Optional

from api.agents.base import BaseAgent
from api.core.constants import (
    FAITHFULNESS_CHECK_FAILED_MESSAGE,
    GROUNDED_ANSWER_NOT_FOUND,
)
from api.models.schemas import AgentResult, RetrievedChunk
from api.services.llm_service import LLMService


class GeneratorAgent(BaseAgent):
    """Streams the final answer token-by-token, then judges faithfulness.

    NOTE: `last_answer` is instance state — the orchestrator must create a
    fresh GeneratorAgent per request (or reset state), never share one across
    concurrent requests, or this will race.
    """

    name = "Generator"

    def __init__(self, llm_service: LLMService, session_id: Optional[str] = None):
        super().__init__(session_id=session_id)
        self._llm = llm_service
        self.last_answer: str = ""

    async def run(self, **kwargs) -> AgentResult:
        """Non-streaming entrypoint unused by the orchestrator — prefer `stream`."""
        query = kwargs.get("query", "")
        chunks: list[RetrievedChunk] = kwargs.get("chunks") or []
        history = kwargs.get("conversation_history") or []
        session_context = kwargs.get("session_context")
        lab_context = kwargs.get("lab_context")
        summary = kwargs.get("conversation_summary")
        parts: list[str] = []
        async for token in self._llm.stream_answer(
            query, chunks, history, session_context,
            lab_context=lab_context,
            conversation_summary=summary,
        ):
            parts.append(token)
        self.last_answer = "".join(parts)
        faithfulness = await self._llm.judge_faithfulness(query, self.last_answer, chunks)
        return AgentResult(
            success=True,
            data={"answer": self.last_answer, "faithfulness": faithfulness, "chunks": chunks},
            output="answer generated",
        )

    async def stream(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        conversation_history: list[dict],
        session_context: Optional[dict] = None,
        lab_context: Optional[str] = None,
        conversation_summary: Optional[str] = None,
        web_context: Optional[str] = None,
    ) -> AsyncGenerator[dict, None]:
        answer_parts: list[str] = []
        async for token in self._llm.stream_answer(
            query,
            chunks,
            conversation_history,
            session_context,
            lab_context=lab_context,
            conversation_summary=conversation_summary,
            web_context=web_context,
        ):
            answer_parts.append(token)
            yield {"type": "token", "content": token}

        full_answer = "".join(answer_parts)
        self.last_answer = full_answer

        yield {
            "type": "citations",
            "chunks": [chunk.model_dump() for chunk in chunks],
        }

        faithfulness = await self._llm.judge_faithfulness(query, full_answer, chunks)
        yield {
            "type": "faithfulness",
            "score": faithfulness.score,
            "verdict": faithfulness.verdict,
            "violations": faithfulness.violations,
        }

        if (
            faithfulness.verdict == "FAIL"
            and full_answer.strip() != GROUNDED_ANSWER_NOT_FOUND
        ):
            yield {
                "type": "error",
                "message": FAITHFULNESS_CHECK_FAILED_MESSAGE,
            }
