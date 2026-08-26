"""RerankerAgent — Cohere rerank + doc_type/recency authority weighting."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from api.agents.base import BaseAgent
from api.core.config import Settings
from api.core.exceptions import CohereRerankException, RedisReadException
from api.core.logger import log_exception
from api.models.schemas import AgentResult, RetrievedChunk
from api.services.redis_service import RedisService
from api.services.retrieval_service import RetrievalService

from api.core.constants import (
    CLINICAL_GUIDELINE_AUTHORITY_HIGH,
    CLINICAL_GUIDELINE_DOC_TYPE,
    CLINICAL_GUIDELINE_MULT_HIGH,
    CLINICAL_GUIDELINE_MULT_LOW,
    DOC_TYPE_MULTIPLIERS,
    RECENCY_CURRENT_MAX_AGE_YEARS,
    RECENCY_MID_MAX_AGE_YEARS,
    RECENCY_MULT_CURRENT,
    RECENCY_MULT_MID,
    RECENCY_MULT_OLD,
    RESEARCH_PAPER_DOC_TYPE,
)

# Cohere relevance below this is effectively "not matching the query".
# For multi-topic questions, scores often collapse near 0 — fall back to RRF order.
_RERANK_MIN_USEFUL_SCORE = 0.05
class RerankerAgent(BaseAgent):
    """Cohere rerank followed by doc-type and recency multipliers.

    final_score = cohere_score * doc_type_mult * recency_mult
    """

    name = "Reranker Agent"

    def __init__(
        self,
        retrieval_service: RetrievalService,
        redis_service: RedisService,
        settings: Settings,
        session_id: Optional[str] = None,
    ):
        super().__init__(session_id=session_id)
        self._retrieval = retrieval_service
        self._redis = redis_service
        self._settings = settings

    async def _enrich_from_redis(self, chunk: RetrievedChunk) -> RetrievedChunk:
        """Fill doc_type / publication_year / authority from Redis meta when missing."""
        needs_type = not chunk.doc_type
        needs_year = chunk.publication_year is None
        needs_auth = not chunk.authority_level or chunk.authority_level <= 1
        if not (needs_type or needs_year or needs_auth):
            return chunk

        try:
            meta = await self._redis.get_chunk_metadata(chunk.chunk_id)
        except RedisReadException as exc:
            log_exception(self.logger, exc)
            return chunk

        if not meta:
            return chunk

        updates: dict = {}
        if needs_type and meta.get("doc_type"):
            updates["doc_type"] = str(meta["doc_type"])
        if needs_year and meta.get("publication_year") is not None:
            try:
                updates["publication_year"] = int(meta["publication_year"])
            except (TypeError, ValueError):
                pass
        if needs_auth and meta.get("authority_level") is not None:
            try:
                updates["authority_level"] = int(meta["authority_level"])
            except (TypeError, ValueError):
                pass
        if meta.get("source_org") and not chunk.source_org:
            updates["source_org"] = str(meta["source_org"])

        return chunk.model_copy(update=updates) if updates else chunk

    @staticmethod
    def _doc_type_multiplier(doc_type: Optional[str], authority: int) -> float:
        dtype = (doc_type or "").lower()
        if dtype == CLINICAL_GUIDELINE_DOC_TYPE:
            return CLINICAL_GUIDELINE_MULT_HIGH if authority >= CLINICAL_GUIDELINE_AUTHORITY_HIGH else CLINICAL_GUIDELINE_MULT_LOW
        return DOC_TYPE_MULTIPLIERS.get(dtype, 1.0)

    @staticmethod
    def _recency_multiplier(publication_year: Optional[int], current_year: int) -> float:
        if publication_year is None:
            return 1.0
        age = current_year - int(publication_year)
        if age <= RECENCY_CURRENT_MAX_AGE_YEARS:
            return RECENCY_MULT_CURRENT
        if age <= RECENCY_MID_MAX_AGE_YEARS:
            return RECENCY_MULT_MID
        return RECENCY_MULT_OLD

    def _ensure_not_all_research(
        self,
        ranked: list[RetrievedChunk],
        all_candidates: list[RetrievedChunk],
    ) -> list[RetrievedChunk]:
        """Research papers never alone: if top-k is all research and other types
        exist among candidates, swap the lowest research with the best non-research.
        """
        if not ranked:
            return ranked
        if not all(c.doc_type == RESEARCH_PAPER_DOC_TYPE for c in ranked):
            return ranked

        non_research = [
            c
            for c in all_candidates
            if c.doc_type and c.doc_type != RESEARCH_PAPER_DOC_TYPE
            and c.chunk_id not in {r.chunk_id for r in ranked}
        ]
        if not non_research:
            # Also consider candidates already scored but filtered out of top-k.
            non_research = [
                c
                for c in all_candidates
                if (c.doc_type or "") != RESEARCH_PAPER_DOC_TYPE
                and c.chunk_id not in {r.chunk_id for r in ranked}
            ]
        if not non_research:
            return ranked

        best_other = max(non_research, key=lambda c: c.score)
        # Replace lowest-scoring research paper in top-k.
        lowest_idx = min(range(len(ranked)), key=lambda i: ranked[i].score)
        self.logger.debug(
            "Swapping research-only top-k: replacing %s with %s (%s)",
            ranked[lowest_idx].chunk_id,
            best_other.chunk_id,
            best_other.doc_type,
            extra={"agent_name": self.name, "session_id": self.session_id},
        )
        swapped = list(ranked)
        swapped[lowest_idx] = best_other
        swapped.sort(key=lambda c: c.score, reverse=True)
        return swapped

    async def run(
        self,
        query: str = "",
        chunks: list[RetrievedChunk] | None = None,
        **kwargs,
    ) -> AgentResult:
        candidates = chunks or []
        current_year = datetime.utcnow().year
        top_k = self._settings.rerank_top_k

        with self.track_duration() as timing:
            try:
                reranked = await self._retrieval.rerank(query, candidates, top_k)
            except CohereRerankException as exc:
                log_exception(self.logger, exc)
                self.logger.warning("Rerank failed; falling back to input order")
                reranked = candidates[:top_k]

            # Multi-topic / vague queries often get near-zero Cohere scores for
            # every chunk. Prefer the fused RRF order rather than trusting noise.
            max_cohere = max((float(c.score) for c in reranked), default=0.0)
            if candidates and (not reranked or max_cohere < _RERANK_MIN_USEFUL_SCORE):
                self.logger.warning(
                    "Cohere rerank scores too low (max=%.4f); using fused ranking",
                    max_cohere,
                    extra={"agent_name": self.name, "session_id": self.session_id},
                )
                reranked = candidates[:top_k]

            # Enrich the broader candidate pool (for research-swap) and reranked set.
            enriched_candidates: list[RetrievedChunk] = []
            for chunk in candidates:
                enriched_candidates.append(await self._enrich_from_redis(chunk))

            boosted: list[RetrievedChunk] = []
            for chunk in reranked:
                enriched = await self._enrich_from_redis(chunk)
                # Prefer enriched metadata already loaded for the same chunk_id.
                for cand in enriched_candidates:
                    if cand.chunk_id == enriched.chunk_id:
                        enriched = enriched.model_copy(
                            update={
                                "doc_type": enriched.doc_type or cand.doc_type,
                                "publication_year": (
                                    enriched.publication_year
                                    if enriched.publication_year is not None
                                    else cand.publication_year
                                ),
                                "authority_level": max(
                                    enriched.authority_level or 1,
                                    cand.authority_level or 1,
                                ),
                                "source_org": enriched.source_org or cand.source_org,
                            }
                        )
                        break

                authority = enriched.authority_level or 1
                dtype = enriched.doc_type
                year = enriched.publication_year
                cohere_score = float(enriched.score)
                doc_mult = self._doc_type_multiplier(dtype, authority)
                recency_mult = self._recency_multiplier(year, current_year)
                final_score = cohere_score * doc_mult * recency_mult

                self.logger.debug(
                    "Rerank adjustment chunk=%s doc_type=%s authority=%s year=%s "
                    "cohere=%.4f doc_mult=%.2f recency_mult=%.2f final=%.4f",
                    enriched.chunk_id,
                    dtype,
                    authority,
                    year,
                    cohere_score,
                    doc_mult,
                    recency_mult,
                    final_score,
                    extra={"agent_name": self.name, "session_id": self.session_id},
                )

                boosted.append(
                    enriched.model_copy(
                        update={
                            "score": final_score,
                            "authority_level": authority,
                            "doc_type": dtype,
                            "publication_year": year,
                        }
                    )
                )

            # Also score remaining candidates so research-swap has comparable scores.
            boosted_ids = {c.chunk_id for c in boosted}
            scored_pool = list(boosted)
            for cand in enriched_candidates:
                if cand.chunk_id in boosted_ids:
                    continue
                authority = cand.authority_level or 1
                doc_mult = self._doc_type_multiplier(cand.doc_type, authority)
                recency_mult = self._recency_multiplier(cand.publication_year, current_year)
                scored_pool.append(
                    cand.model_copy(
                        update={"score": float(cand.score) * doc_mult * recency_mult}
                    )
                )

            boosted.sort(key=lambda c: c.score, reverse=True)
            selected = boosted[:top_k]
            selected = self._ensure_not_all_research(selected, scored_pool)
            ranked = [
                chunk.model_copy(update={"rank": rank})
                for rank, chunk in enumerate(selected, start=1)
            ]

        return AgentResult(
            success=True,
            data=ranked,
            output=f"top {len(ranked)} selected",
            duration_ms=timing["duration_ms"],
        )
