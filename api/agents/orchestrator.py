"""Orchestrator — clinical multi-agent RAG pipeline with SSE event streaming.

NOTE: ``run`` is an async generator. ``@conditional_traceable`` / LangSmith
``traceable`` can break async-generator semantics, so the decorator is not
applied to ``run``. Tracing is still active on nested LLM / retrieval calls.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any, AsyncGenerator, Optional

from api.agents.drug_interaction_agent import DrugInteractionAgent
from api.agents.emergency_detector import EmergencyDetectorAgent
from api.agents.generator import GeneratorAgent
from api.agents.lab_reference_agent import LabReferenceAgent
from api.agents.query_analyzer import QueryAnalyzerAgent
from api.core.config import Settings
from api.core.exceptions import RedisReadException, RedisWriteException
from api.core.logger import get_logger, log_exception
from api.models.clinical_schemas import ClinicalContext, LabFlag, LabValue
from api.models.schemas import InteractionResult, QueryAnalysis
from api.services.drug_interaction_service import DrugInteractionService
from api.services.web_search_service import WebSearchService
from api.services.embedding_service import EmbeddingService
from api.services.llm_service import LLMService
from api.services.redis_service import RedisService
from api.services.retrieval_service import RetrievalService

from api.core.constants import (
    INTERACTION_QUERY_KEYWORDS,
    INTERACTION_SEVERITY_MAJOR,
    GROUNDED_ANSWER_NOT_FOUND,
    QUERY_TYPES_NO_DISCLAIMER,
    QUERY_TYPES_REQUIRING_DISCLAIMER,
)
from api.services.handoff_service import (
    HandoffService,
    STATE_QUEUED,
    is_manual_handoff_request,
)

logger = get_logger(__name__)


def _looks_like_interaction(query: str) -> bool:
    lowered = (query or "").lower()
    return any(keyword in lowered for keyword in INTERACTION_QUERY_KEYWORDS)


def _as_clinical_context(raw: Optional[dict]) -> ClinicalContext:
    if not raw:
        return ClinicalContext()
    try:
        return ClinicalContext(
            specialty=raw.get("specialty"),
            patient_age_group=raw.get("patient_age_group"),
            patient_weight_kg=raw.get("patient_weight_kg"),
            disclaimer_shown=bool(raw.get("disclaimer_shown", False)),
            query_count=int(raw.get("query_count") or 0),
            last_query_type=raw.get("last_query_type"),
        )
    except Exception:
        return ClinicalContext()


def _severity_value(severity: Any) -> str:
    if severity is None:
        return ""
    if hasattr(severity, "value"):
        return str(severity.value).upper()
    return str(severity).upper()


def _should_show_disclaimer(
    query_type: str,
    disclaimer_shown: bool,
    *,
    interaction_severity: Optional[str] = None,
    lab_values: Optional[list[LabValue]] = None,
) -> bool:
    """Clinical disclaimer gating.

    Show when first of session, or for diagnosis / treatment / emergency, or
    MAJOR drug interactions. Never for drug_information; never for
    lab_interpretation when every flag is NORMAL; never for general_clinical
    purely educational queries.
    """
    qt = (query_type or "").lower()

    if qt in QUERY_TYPES_NO_DISCLAIMER:
        return False
    if qt == "lab_interpretation":
        if lab_values and all(
            (v.flag == LabFlag.NORMAL or (v.flag is not None and str(v.flag).upper() == "NORMAL"))
            for v in lab_values
        ):
            return False
        # Abnormal / unknown labs: show on first session only unless other rules apply.
        return not disclaimer_shown

    if qt in QUERY_TYPES_REQUIRING_DISCLAIMER:
        return True
    if qt == "drug_interaction" and _severity_value(interaction_severity) == INTERACTION_SEVERITY_MAJOR:
        return True
    if not disclaimer_shown:
        return True
    return False


class Orchestrator:
    """Brain of the system. Services are injected once; agents that hold
    per-request mutable state (GeneratorAgent.last_answer) are constructed
    fresh inside ``run`` so concurrent requests never race.
    """

    def __init__(
        self,
        redis_service: RedisService,
        embedding_service: EmbeddingService,
        retrieval_service: RetrievalService,
        llm_service: LLMService,
        drug_service: DrugInteractionService,
        web_search_service: WebSearchService,
        settings: Settings,
    ):
        self._redis = redis_service
        self._embedding = embedding_service
        self._retrieval = retrieval_service
        self._llm = llm_service
        self._drugs = drug_service
        self._web = web_search_service
        self._settings = settings

        self.emergency_detector = EmergencyDetectorAgent(redis_service, settings)
        self.query_analyzer = QueryAnalyzerAgent(llm_service)
        self.drug_interaction_agent = DrugInteractionAgent(
            drug_service, llm_service, retrieval_service, settings
        )
        self.lab_reference_agent = LabReferenceAgent(
            redis_service, retrieval_service, settings
        )
        self._faq_tasks: set[asyncio.Task] = set()
        self._handoff = HandoffService(redis_service)

    def _cache_key(self, query: str, doc_names: Optional[list[str]]) -> str:
        filter_part = ",".join(sorted(doc_names)) if doc_names else ""
        return hashlib.sha256(f"{query}|{filter_part}".encode("utf-8")).hexdigest()

    def _bind_session(self, session_id: str) -> None:
        for agent in (
            self.emergency_detector,
            self.query_analyzer,
            self.drug_interaction_agent,
            self.lab_reference_agent,
        ):
            agent.session_id = session_id

    def _build_doc_type_filter(
        self,
        analysis: QueryAnalysis,
        session_context: ClinicalContext,
    ) -> Optional[list[str]]:
        filters = list(analysis.doc_type_filter or [])
        specialty = (session_context.specialty or "").lower()
        if specialty == "pediatrics":
            # Prefer treatment protocols for pediatric sessions.
            if "treatment_protocol" not in filters:
                filters.append("treatment_protocol")
            if session_context.patient_age_group is None:
                session_context.patient_age_group = "pediatric"
        return filters or None

    async def run(
        self,
        query: str,
        session_id: str,
        doc_names: Optional[list[str]] = None,
        session_context: Optional[dict] = None,
        conversation_history: Optional[list[dict]] = None,
        conversation_summary: Optional[str] = None,
        persist_redis_history: bool = False,
        enable_web_search: bool = False,
    ) -> AsyncGenerator[dict, None]:
        self._bind_session(session_id)
        logger.info(
            "Agent pipeline started",
            extra={"session_id": session_id, "agent_name": "Orchestrator"},
        )

        interaction_severity: Optional[str] = None
        lab_values: list[LabValue] = []
        lab_context: Optional[str] = None
        clinical_ctx = ClinicalContext()

        try:
            # 1. Load history — prefer DB-provided context over Redis
            if conversation_history is not None:
                history = conversation_history
            else:
                try:
                    history = await self._redis.get_conversation_history(session_id)
                except RedisReadException as exc:
                    log_exception(logger, exc)
                    history = []

            stored_context: Optional[dict] = None
            try:
                stored_context = await self._redis.get_session_context(session_id)
            except RedisReadException as exc:
                log_exception(logger, exc)

            merged: dict[str, Any] = {}
            if stored_context:
                merged.update(stored_context)
            if session_context:
                merged.update({k: v for k, v in session_context.items() if v is not None})
            clinical_ctx = _as_clinical_context(merged)

            # Manual human handoff phrase (flexible matching)
            if is_manual_handoff_request(query or ""):
                from api.core.database import AsyncSessionLocal

                async with AsyncSessionLocal() as db:
                    result = await self._handoff.request_handoff(
                        db,
                        session_id,
                        reason="patient_request",
                    )
                yield {
                    "type": "handoff_initiated",
                    "reason": result["reason"],
                    "queue_position": result["queue_position"],
                    "state": result["state"],
                }
                yield {
                    "type": "queue_position",
                    "position": result["queue_position"],
                    "state": result["state"],
                }
                yield {"type": "done"}
                return

            cache_key = self._cache_key(query, doc_names)

            # 3. Emergency detector first
            yield self.emergency_detector.emit_status("running")
            emergency = await self.emergency_detector.run(
                query=query,
                specialty=clinical_ctx.specialty,
            )
            yield self.emergency_detector.emit_status("complete", emergency.output)
            if emergency.is_emergency:
                yield {
                    "type": "emergency_warning",
                    "message": emergency.message,
                    "matched_terms": emergency.matched_terms,
                }

            # 4. Query analyzer
            yield self.query_analyzer.emit_status("running")
            analysis_result = await self.query_analyzer.run(
                query=query,
                conversation_history=history,
                conversation_summary=conversation_summary,
            )
            analysis: QueryAnalysis = analysis_result.data
            yield self.query_analyzer.emit_status("complete", analysis_result.output)

            if emergency.is_emergency:
                analysis.query_type = "emergency_query"

            query_type = (analysis.query_type or analysis.intent or "general_clinical").lower()

            # FAQ clustering — never block the chat pipeline
            task = asyncio.create_task(self._observe_faq(query, analysis))
            self._faq_tasks.add(task)
            task.add_done_callback(self._faq_tasks.discard)

            # Cache check AFTER analysis — skip for drug_interaction / emergency
            if (
                query_type not in ("drug_interaction", "emergency_query")
                and not _looks_like_interaction(query)
            ):
                try:
                    cached_answer = await self._redis.get_cached_answer(cache_key)
                except RedisReadException as exc:
                    log_exception(logger, exc)
                    cached_answer = None
                if cached_answer is not None:
                    yield {"type": "token", "content": cached_answer}
                    clinical_ctx, disclaimer_event = await self._emit_disclaimer_if_needed(
                        query_type,
                        clinical_ctx,
                        session_id,
                        interaction_severity=interaction_severity,
                        lab_values=lab_values,
                    )
                    if disclaimer_event is not None:
                        yield disclaimer_event
                    await self._safe_store_history(
                        session_id, query, cached_answer, persist_redis_history
                    )
                    yield {"type": "done"}
                    return

            # 5. Update session context
            clinical_ctx.query_count = int(clinical_ctx.query_count or 0) + 1
            clinical_ctx.last_query_type = query_type
            await self._safe_store_session(session_id, clinical_ctx)

            # 6. Analytics
            try:
                await self._redis.increment_query_type_counter(query_type)
            except RedisWriteException as exc:
                log_exception(logger, exc)

            for drug_name in analysis.extracted_drug_names or []:
                try:
                    await self._redis.track_drug_mention(drug_name)
                except RedisWriteException as exc:
                    log_exception(logger, exc)

            # 7. Drug interaction branch
            if query_type == "drug_interaction":
                yield self.drug_interaction_agent.emit_status("running")
                drug_result = await self.drug_interaction_agent.run(query=query)
                yield self.drug_interaction_agent.emit_status(
                    "complete", drug_result.output
                )
                if (
                    drug_result.success
                    and not drug_result.not_applicable
                    and isinstance(drug_result.data, InteractionResult)
                ):
                    interaction = drug_result.data
                    interaction_severity = _severity_value(interaction.severity)
                    yield {
                        "type": "drug_interaction",
                        "drug_a": interaction.drug_a,
                        "drug_b": interaction.drug_b,
                        "severity": interaction_severity or interaction.severity,
                        "description": interaction.description,
                        "clinical_recommendation": (
                            interaction.clinical_recommendation
                            or interaction.recommendation
                        ),
                        "monitoring_parameters": interaction.monitoring_parameters,
                        "source_doc_name": interaction.source_doc_name,
                        "source_authority_level": interaction.source_authority_level,
                    }

            # 8. Lab interpretation branch
            if query_type == "lab_interpretation":
                yield self.lab_reference_agent.emit_status("running")
                lab_result = await self.lab_reference_agent.run(query=query)
                yield self.lab_reference_agent.emit_status("complete", lab_result.output)
                if lab_result.success and lab_result.data:
                    if isinstance(lab_result.data, list):
                        lab_values = [
                            v for v in lab_result.data if isinstance(v, LabValue)
                        ]
                    lab_context = lab_result.output or lab_result.message

            # 9. Conversational path (no retrieval)
            if not analysis.requires_retrieval:
                yield {
                    "type": "agent_status",
                    "agent": "Generator",
                    "status": "running",
                    "output": None,
                }
                answer_parts: list[str] = []
                async for token in self._llm.stream_conversational(
                    query, history, conversation_summary=conversation_summary
                ):
                    answer_parts.append(token)
                    yield {"type": "token", "content": token}
                full_answer = "".join(answer_parts)
                yield {
                    "type": "agent_status",
                    "agent": "Generator",
                    "status": "complete",
                    "output": "conversational response generated",
                }
                clinical_ctx, disclaimer_event = await self._emit_disclaimer_if_needed(
                    query_type,
                    clinical_ctx,
                    session_id,
                    interaction_severity=interaction_severity,
                    lab_values=lab_values,
                )
                if disclaimer_event is not None:
                    yield disclaimer_event
            # Also skip caching conversational refusals if any
                if query_type != "drug_interaction" and full_answer.strip() != GROUNDED_ANSWER_NOT_FOUND:
                    await self._safe_cache_answer(cache_key, full_answer)
                await self._safe_store_history(
                    session_id, query, full_answer, persist_redis_history
                )
                yield {"type": "done"}
                return

            # 10. Doc type filter (+ pediatrics preference)
            doc_type_filter = self._build_doc_type_filter(analysis, clinical_ctx)
            effective_doc_filter = doc_names or analysis.doc_filter

            # Persist any patient_age_group mutation from pediatrics preference
            await self._safe_store_session(session_id, clinical_ctx)
            session_dict = clinical_ctx.model_dump()

            # 11. Dense + Sparse + Fusion + Reranker
            # (relocated from DenseRetrieverAgent/SparseRetrieverAgent/FusionAgent/
            # RerankerAgent into RetrievalService methods — same pipeline, same
            # SSE agent_status event shape.)
            yield {"type": "agent_status", "agent": "Dense Retriever", "status": "running", "output": None}
            dense_chunks = await self._retrieval.dense_retrieve_multi(
                sub_queries=analysis.expanded_queries,
                top_k=self._settings.dense_top_k,
                doc_filter=effective_doc_filter,
                doc_type_filter=doc_type_filter,
            )
            yield {
                "type": "agent_status",
                "agent": "Dense Retriever",
                "status": "complete",
                "output": f"{len(dense_chunks)} candidates retrieved",
            }

            yield {"type": "agent_status", "agent": "Sparse Retriever", "status": "running", "output": None}
            try:
                sparse_chunks = await self._retrieval.sparse_search(query, self._settings.sparse_top_k)
                sparse_output = f"{len(sparse_chunks)} candidates retrieved"
            except Exception as exc:
                # Never block the chat pipeline on sparse/BM25 failures —
                # dense results alone are enough for fusion + generation.
                log_exception(logger, exc)
                sparse_chunks = []
                sparse_output = "0 candidates (sparse fallback)"
            yield {
                "type": "agent_status",
                "agent": "Sparse Retriever",
                "status": "complete",
                "output": sparse_output,
            }

            yield {"type": "agent_status", "agent": "Fusion Agent", "status": "running", "output": None}
            fused_chunks = await self._retrieval.fuse(dense_chunks, sparse_chunks)
            yield {
                "type": "agent_status",
                "agent": "Fusion Agent",
                "status": "complete",
                "output": f"{len(fused_chunks)} fused candidates",
            }

            yield {"type": "agent_status", "agent": "Reranker Agent", "status": "running", "output": None}
            reranked_chunks = await self._retrieval.rerank_with_authority(
                query, fused_chunks, self._settings.rerank_top_k
            )
            yield {
                "type": "agent_status",
                "agent": "Reranker Agent",
                "status": "complete",
                "output": f"top {len(reranked_chunks)} selected",
            }

            # 12. Track doc types of top chunks
            for chunk in reranked_chunks or []:
                doc_type = getattr(chunk, "doc_type", None)
                if doc_type:
                    try:
                        await self._redis.increment_doc_type_query(str(doc_type))
                    except RedisWriteException as exc:
                        log_exception(logger, exc)

            # 13. Optional PubMed web search
            web_context: Optional[str] = None
            if enable_web_search and analysis.requires_retrieval:
                yield {
                    "type": "agent_status",
                    "agent": "WebSearch",
                    "status": "running",
                }
                web_results = await self._web.search_pubmed(query)
                web_context = self._web.format_context(web_results)
                yield {
                    "type": "agent_status",
                    "agent": "WebSearch",
                    "status": "complete",
                    "output": f"{len(web_results)} PubMed results",
                }
                if web_results:
                    yield {
                        "type": "web_sources",
                        "results": [item.model_dump() for item in web_results],
                    }

            # 14. Generator (fresh per run) with lab_context
            generator = GeneratorAgent(self._llm, session_id=session_id)
            yield generator.emit_status("running")
            last_faithfulness_verdict: Optional[str] = None
            async for event in generator.stream(
                query,
                reranked_chunks or [],
                history,
                session_dict,
                lab_context=lab_context,
                conversation_summary=conversation_summary,
                web_context=web_context,
            ):
                if event.get("type") == "faithfulness":
                    last_faithfulness_verdict = event.get("verdict")
                    score = event.get("score")
                    if score is not None:
                        try:
                            await self._redis.store_faithfulness_score(float(score))
                        except RedisWriteException as exc:
                            log_exception(logger, exc)
                yield event
            yield generator.emit_status("complete", "answer generated")

            # 14. Conditional disclaimer
            clinical_ctx, disclaimer_event = await self._emit_disclaimer_if_needed(
                query_type,
                clinical_ctx,
                session_id,
                interaction_severity=interaction_severity,
                lab_values=lab_values,
            )
            if disclaimer_event is not None:
                yield disclaimer_event

            full_answer = generator.last_answer

            # Auto handoff on quality failure streaks
            trigger = await self._handoff.evaluate_quality_triggers(
                session_id,
                faithfulness_verdict=last_faithfulness_verdict,
                answer_text=full_answer,
            )
            if trigger:
                from api.core.database import AsyncSessionLocal

                async with AsyncSessionLocal() as db:
                    handoff = await self._handoff.request_handoff(
                        db,
                        session_id,
                        reason=trigger["reason"],
                    )
                yield {
                    "type": "handoff_initiated",
                    "reason": handoff["reason"],
                    "queue_position": handoff["queue_position"],
                    "state": handoff.get("state") or STATE_QUEUED,
                    "details": trigger,
                }
                yield {
                    "type": "queue_position",
                    "position": handoff["queue_position"],
                    "state": handoff.get("state") or STATE_QUEUED,
                }

            # 15. Cache answer except drug_interaction / grounded refusals
            if (
                query_type != "drug_interaction"
                and full_answer.strip() != GROUNDED_ANSWER_NOT_FOUND
            ):
                await self._safe_cache_answer(cache_key, full_answer)

            # 16. Store history
            await self._safe_store_history(
                session_id, query, full_answer, persist_redis_history
            )

            logger.info(
                "Agent pipeline completed",
                extra={"session_id": session_id, "agent_name": "Orchestrator"},
            )
            yield {"type": "done"}

        except Exception as exc:
            log_exception(logger, exc)
            yield {"type": "error", "message": str(exc)}
            yield {"type": "done"}

    async def _observe_faq(self, query: str, analysis: QueryAnalysis) -> None:
        try:
            from api.core.database import AsyncSessionLocal
            from api.services.faq_service import FaqService

            async with AsyncSessionLocal() as db:
                service = FaqService(self._embedding)
                await service.observe(
                    db,
                    query,
                    query_type=analysis.query_type or analysis.intent,
                    requires_retrieval=bool(analysis.requires_retrieval),
                )
                await db.commit()
        except Exception as exc:
            log_exception(logger, exc)
            logger.warning("FAQ observe failed (non-blocking)")

    async def _emit_disclaimer_if_needed(
        self,
        query_type: str,
        clinical_ctx: ClinicalContext,
        session_id: str,
        *,
        interaction_severity: Optional[str] = None,
        lab_values: Optional[list[LabValue]] = None,
    ) -> tuple[ClinicalContext, Optional[dict]]:
        if not _should_show_disclaimer(
            query_type,
            clinical_ctx.disclaimer_shown,
            interaction_severity=interaction_severity,
            lab_values=lab_values,
        ):
            return clinical_ctx, None
        event = {
            "type": "clinical_disclaimer",
            "message": self._settings.clinical_disclaimer,
        }
        clinical_ctx.disclaimer_shown = True
        await self._safe_store_session(session_id, clinical_ctx)
        return clinical_ctx, event

    async def _safe_store_session(self, session_id: str, ctx: ClinicalContext) -> None:
        try:
            await self._redis.store_session_context(session_id, ctx.model_dump())
        except RedisWriteException as exc:
            log_exception(logger, exc)

    async def _safe_store_history(
        self,
        session_id: str,
        query: str,
        answer: str,
        persist_redis: bool = True,
    ) -> None:
        if not persist_redis:
            return
        try:
            await self._redis.store_conversation_message(session_id, "user", query)
            await self._redis.store_conversation_message(session_id, "assistant", answer)
        except RedisWriteException as exc:
            log_exception(logger, exc)

    async def _safe_cache_answer(self, cache_key: str, answer: str) -> None:
        try:
            await self._redis.cache_answer(
                cache_key, answer, self._settings.answer_cache_ttl_seconds
            )
        except RedisWriteException as exc:
            log_exception(logger, exc)
