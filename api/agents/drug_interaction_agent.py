"""DrugInteractionAgent — extract drugs, check Redis cache, fall back to retrieval + LLM."""

from __future__ import annotations

from typing import Optional

from api.agents.base import BaseAgent
from api.core.config import Settings
from api.core.constants import DOC_TYPE_FILTERS, DRUG_MONOGRAPH_DOC_TYPE
from api.core.exceptions import GroqException, PineconeQueryException, RedisWriteException
from api.core.logger import log_exception
from api.models.schemas import AgentResult, InteractionResult
from api.services.drug_interaction_service import DrugInteractionService
from api.services.llm_service import LLMService
from api.services.retrieval_service import RetrievalService


class DrugInteractionAgent(BaseAgent):
    name = "Drug Interaction"

    def __init__(
        self,
        drug_service: DrugInteractionService,
        llm_service: LLMService,
        retrieval_service: RetrievalService,
        settings: Settings,
        session_id: Optional[str] = None,
    ):
        super().__init__(session_id=session_id)
        self._drugs = drug_service
        self._llm = llm_service
        self._retrieval = retrieval_service
        self._settings = settings

    async def _track_drugs(self, names: list[str]) -> None:
        for name in names:
            try:
                await self._drugs._redis.track_drug_mention(name)
            except RedisWriteException as exc:
                log_exception(self.logger, exc)

    async def run(self, query: str = "", **kwargs) -> AgentResult:
        with self.track_duration() as timing:
            try:
                names = await self._drugs.extract_drug_names(query, self._llm)
            except GroqException as exc:
                log_exception(self.logger, exc)
                return AgentResult(
                    success=False,
                    not_applicable=True,
                    message="Could not extract drug names",
                    duration_ms=timing["duration_ms"],
                )

            if names:
                await self._track_drugs(names)

            if len(names) < 2:
                return AgentResult(
                    success=True,
                    not_applicable=True,
                    output="fewer than 2 drugs found",
                    duration_ms=timing["duration_ms"],
                )

            drug_a, drug_b = names[0], names[1]

            cached = await self._drugs.check_interaction(drug_a, drug_b)
            if cached is not None:
                return AgentResult(
                    success=True,
                    data=cached,
                    severity=cached.severity,
                    output=f"cached interaction {drug_a} + {drug_b}: {cached.severity}",
                    duration_ms=timing["duration_ms"],
                )

            # Cache miss — retrieve drug monograph chunks then extract via LLM.
            search_query = f"{drug_a} {drug_b} drug interaction"
            try:
                chunks = await self._retrieval.dense_search(
                    search_query,
                    self._settings.dense_top_k,
                    doc_filter=None,
                    doc_type_filter=DOC_TYPE_FILTERS.get("drug_interaction"),
                )
                monographs = [c for c in chunks if c.doc_type == DRUG_MONOGRAPH_DOC_TYPE]
                if not monographs:
                    monographs = chunks
            except PineconeQueryException as exc:
                log_exception(self.logger, exc)
                return AgentResult(
                    success=False,
                    message="Retrieval failed while looking up drug interaction",
                    duration_ms=timing["duration_ms"],
                )

            try:
                extracted = await self._llm.extract_interaction_from_chunks(
                    drug_a, drug_b, monographs[:5]
                )
            except GroqException as exc:
                log_exception(self.logger, exc)
                return AgentResult(
                    success=False,
                    message="Failed to extract interaction from sources",
                    duration_ms=timing["duration_ms"],
                )

            # Prefer authority from retrieved monograph when LLM omits it.
            if not extracted.get("source_authority_level") and monographs:
                extracted["source_authority_level"] = monographs[0].authority_level
            if not extracted.get("source_doc_name") and monographs:
                extracted["source_doc_name"] = monographs[0].doc_name

            result = InteractionResult(
                drug_a=drug_a,
                drug_b=drug_b,
                severity=str(extracted.get("severity", "UNKNOWN")),
                description=str(extracted.get("description", "")),
                recommendation=str(
                    extracted.get("recommendation")
                    or extracted.get("clinical_recommendation")
                    or ""
                ),
                clinical_recommendation=str(
                    extracted.get("clinical_recommendation")
                    or extracted.get("recommendation")
                    or ""
                ),
                monitoring_parameters=list(extracted.get("monitoring_parameters") or []),
                source_doc_name=extracted.get("source_doc_name"),
                source_authority_level=extracted.get("source_authority_level"),
            )
            await self._drugs.store_interaction(drug_a, drug_b, result)

        return AgentResult(
            success=True,
            data=result,
            severity=result.severity,
            output=f"interaction {drug_a} + {drug_b}: {result.severity}",
            duration_ms=timing["duration_ms"],
        )
