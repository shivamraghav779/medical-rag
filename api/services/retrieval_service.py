"""RetrievalService — Pinecone dense search, BM25 sparse, RRF, Cohere rerank."""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime
from typing import Optional

import cohere

from api.core.config import settings
from api.core.exceptions import CohereRerankException, PineconeQueryException, RedisReadException
from api.core.logger import conditional_traceable, get_logger, log_api_call, log_exception
from api.models.schemas import RetrievedChunk
from api.services.embedding_service import EmbeddingService
from api.services.redis_service import RedisService

logger = get_logger(__name__)

from api.core.constants import (
    CLINICAL_GUIDELINE_AUTHORITY_HIGH,
    CLINICAL_GUIDELINE_DOC_TYPE,
    CLINICAL_GUIDELINE_MULT_HIGH,
    CLINICAL_GUIDELINE_MULT_LOW,
    DOC_TYPE_MULTIPLIERS,
    FUSED_TOP_K,
    MAX_DENSE_CANDIDATES,
    RECENCY_CURRENT_MAX_AGE_YEARS,
    RECENCY_MID_MAX_AGE_YEARS,
    RECENCY_MULT_CURRENT,
    RECENCY_MULT_MID,
    RECENCY_MULT_OLD,
    RERANK_MODEL,
    RESEARCH_PAPER_DOC_TYPE,
    RRF_K,
)

# Cohere relevance below this is effectively "not matching the query".
# For multi-topic questions, scores often collapse near 0 — fall back to RRF order.
_RERANK_MIN_USEFUL_SCORE = 0.05

class RetrievalService:
    def __init__(
        self,
        pinecone_index,
        cohere_client: cohere.AsyncClient,
        redis_service: RedisService,
        embedding_service: EmbeddingService,
    ):
        self._index = pinecone_index
        self._cohere = cohere_client
        self._redis = redis_service
        self._embedding = embedding_service

    @property
    def pinecone_index(self):
        return self._index

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return re.findall(r"\w+", text.lower())

    @staticmethod
    def _build_filter(
        doc_filter: Optional[list[str]],
        doc_type_filter: Optional[list[str]],
    ) -> Optional[dict]:
        clauses: list[dict] = []
        if doc_filter:
            clauses.append({"doc_name": {"$in": doc_filter}})
        if doc_type_filter:
            clauses.append({"doc_type": {"$in": doc_type_filter}})
        if not clauses:
            return None
        if len(clauses) == 1:
            return clauses[0]
        return {"$and": clauses}

    @staticmethod
    def _chunk_from_metadata(
        chunk_id: str,
        metadata: dict,
        score: float,
        rank: int,
    ) -> RetrievedChunk:
        publication_year = metadata.get("publication_year")
        try:
            publication_year = int(publication_year) if publication_year is not None else None
        except (TypeError, ValueError):
            publication_year = None

        return RetrievedChunk(
            chunk_id=chunk_id,
            doc_name=metadata.get("doc_name", "unknown"),
            page_number=int(metadata.get("page_number", 1) or 1),
            text=metadata.get("text", ""),
            score=float(score),
            rank=rank,
            authority_level=int(metadata.get("authority_level", 1) or 1),
            doc_type=metadata.get("doc_type"),
            source_org=metadata.get("source_org"),
            publication_year=publication_year,
        )

    @conditional_traceable(name="RetrievalService.dense_search", run_type="retriever")
    async def dense_search(
        self,
        query: str | list[float],
        top_k: int,
        doc_filter: Optional[list[str]] = None,
        doc_type_filter: Optional[list[str]] = None,
    ) -> list[RetrievedChunk]:
        """Accepts a query string (embeds internally) or a precomputed embedding."""
        if isinstance(query, str):
            query_embedding = await self._embedding.embed_query(query)
        else:
            query_embedding = query

        query_filter = self._build_filter(doc_filter, doc_type_filter)
        started = time.perf_counter()
        try:
            response = await asyncio.to_thread(
                self._index.query,
                vector=query_embedding,
                top_k=top_k,
                filter=query_filter,
                include_metadata=True,
            )
        except Exception as exc:
            log_api_call(logger, "pinecone", "query", (time.perf_counter() - started) * 1000, False)
            log_exception(logger, exc)
            raise PineconeQueryException("dense_search Pinecone query failed") from exc

        log_api_call(logger, "pinecone", "query", (time.perf_counter() - started) * 1000, True)

        # Pinecone serverless can return matches out of score order shortly
        # after upsert — sort explicitly rather than trusting API order.
        sorted_matches = sorted(response.matches, key=lambda m: m.score, reverse=True)

        results = []
        for rank, match in enumerate(sorted_matches, start=1):
            metadata = match.metadata or {}
            results.append(
                self._chunk_from_metadata(match.id, metadata, float(match.score), rank)
            )
        return results

    async def _load_bm25_index(self) -> Optional[dict]:
        cached = await self._redis.get_cached_bm25_index()
        if cached is not None:
            return cached

        records = await self._redis.get_all_chunk_records()
        if not records:
            return None

        chunk_ids = list(records.keys())
        index_data = {
            "chunk_ids": chunk_ids,
            "tokenized_corpus": [self._tokenize(records[cid]["text"]) for cid in chunk_ids],
            "records": records,
        }
        # Best-effort Redis cache; in-memory copy is always set inside cache_bm25_index.
        try:
            await self._redis.cache_bm25_index(index_data)
        except Exception as exc:
            log_exception(logger, exc)
        return index_data

    @conditional_traceable(name="RetrievalService.sparse_search", run_type="retriever")
    async def sparse_search(self, query: str, top_k: int) -> list[RetrievedChunk]:
        from rank_bm25 import BM25Okapi

        # Cap rebuild time so a Redis cache miss cannot stall the whole chat.
        try:
            index_data = await asyncio.wait_for(self._load_bm25_index(), timeout=20.0)
        except asyncio.TimeoutError:
            logger.warning(
                "BM25 index load timed out after 20s; continuing with dense-only results"
            )
            return []
        except Exception as exc:
            log_exception(logger, exc)
            logger.warning("BM25 sparse search failed; continuing with dense-only results")
            return []

        if not index_data:
            return []

        chunk_ids = index_data["chunk_ids"]
        tokenized_corpus = index_data["tokenized_corpus"]
        records = index_data["records"]

        bm25 = BM25Okapi(tokenized_corpus)
        scores = bm25.get_scores(self._tokenize(query))
        ranked = sorted(zip(chunk_ids, scores), key=lambda pair: pair[1], reverse=True)[:top_k]

        results = []
        for rank, (chunk_id, score) in enumerate(ranked, start=1):
            record = records.get(chunk_id)
            if record is None:
                continue
            results.append(self._chunk_from_metadata(chunk_id, record, float(score), rank))
        return results

    @conditional_traceable(name="RetrievalService.rrf_fusion", run_type="chain")
    async def rrf_fusion(
        self,
        dense_results: list[RetrievedChunk],
        sparse_results: list[RetrievedChunk],
        k: int = RRF_K,
    ) -> list[RetrievedChunk]:
        rrf_scores: dict[str, float] = {}
        chunk_lookup: dict[str, RetrievedChunk] = {}

        for result_list in (dense_results, sparse_results):
            for position, chunk in enumerate(result_list, start=1):
                rrf_scores[chunk.chunk_id] = rrf_scores.get(chunk.chunk_id, 0.0) + 1.0 / (k + position)
                chunk_lookup.setdefault(chunk.chunk_id, chunk)

        ranked_ids = sorted(rrf_scores.keys(), key=lambda cid: rrf_scores[cid], reverse=True)

        fused = []
        for rank, chunk_id in enumerate(ranked_ids, start=1):
            base = chunk_lookup[chunk_id]
            fused.append(base.model_copy(update={"score": rrf_scores[chunk_id], "rank": rank}))
        return fused

    @conditional_traceable(name="RetrievalService.rerank", run_type="retriever")
    async def rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        top_k: int,
    ) -> list[RetrievedChunk]:
        if not chunks:
            return []

        started = time.perf_counter()
        try:
            response = await self._cohere.rerank(
                model=RERANK_MODEL,
                query=query,
                documents=[chunk.text for chunk in chunks],
                top_n=top_k,
            )
        except Exception as exc:
            log_api_call(logger, "cohere", "rerank", (time.perf_counter() - started) * 1000, False)
            log_exception(logger, exc)
            raise CohereRerankException("Cohere rerank failed") from exc

        log_api_call(logger, "cohere", "rerank", (time.perf_counter() - started) * 1000, True)

        results = []
        for rank, result in enumerate(response.results, start=1):
            original = chunks[result.index]
            results.append(original.model_copy(update={
                "score": float(result.relevance_score),
                "rank": rank,
            }))
        return results

    @conditional_traceable(name="RetrievalService.dense_retrieve_multi", run_type="retriever")
    async def dense_retrieve_multi(
        self,
        sub_queries: list[str] | None,
        top_k: int,
        doc_filter: Optional[list[str]] = None,
        doc_type_filter: Optional[list[str]] = None,
    ) -> list[RetrievedChunk]:
        """Dense-search several expanded sub-queries in parallel, dedupe by
        chunk_id keeping the highest score, and cap to MAX_DENSE_CANDIDATES.

        Relocated from the former DenseRetrieverAgent — same embed-per-query,
        parallel-search, dedupe, and cap behavior.
        """
        queries = sub_queries or []
        embeddings = await asyncio.gather(
            *(self._embedding.get_embedding(q, "search_query") for q in queries)
        )
        result_lists = await asyncio.gather(
            *(
                self.dense_search(emb, top_k, doc_filter=doc_filter, doc_type_filter=doc_type_filter)
                for emb in embeddings
            )
        )

        deduped: dict[str, RetrievedChunk] = {}
        for results in result_lists:
            for chunk in results:
                existing = deduped.get(chunk.chunk_id)
                if existing is None or chunk.score > existing.score:
                    deduped[chunk.chunk_id] = chunk

        top_candidates = sorted(deduped.values(), key=lambda c: c.score, reverse=True)[
            :MAX_DENSE_CANDIDATES
        ]
        return [
            chunk.model_copy(update={"rank": rank})
            for rank, chunk in enumerate(top_candidates, start=1)
        ]

    async def fuse(
        self,
        dense_results: list[RetrievedChunk] | None,
        sparse_results: list[RetrievedChunk] | None,
        top_k: int = FUSED_TOP_K,
    ) -> list[RetrievedChunk]:
        """RRF-fuse dense + sparse results and cap to top_k.

        Relocated from the former FusionAgent (rrf_fusion + FUSED_TOP_K cap).
        """
        fused = await self.rrf_fusion(dense_results or [], sparse_results or [])
        return fused[:top_k]

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
            log_exception(logger, exc)
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
            return (
                CLINICAL_GUIDELINE_MULT_HIGH
                if authority >= CLINICAL_GUIDELINE_AUTHORITY_HIGH
                else CLINICAL_GUIDELINE_MULT_LOW
            )
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

    @staticmethod
    def _ensure_not_all_research(
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
            if c.doc_type
            and c.doc_type != RESEARCH_PAPER_DOC_TYPE
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
        logger.debug(
            "Swapping research-only top-k: replacing %s with %s (%s)",
            ranked[lowest_idx].chunk_id,
            best_other.chunk_id,
            best_other.doc_type,
        )
        swapped = list(ranked)
        swapped[lowest_idx] = best_other
        swapped.sort(key=lambda c: c.score, reverse=True)
        return swapped

    @conditional_traceable(name="RetrievalService.rerank_with_authority", run_type="retriever")
    async def rerank_with_authority(
        self,
        query: str,
        chunks: list[RetrievedChunk] | None,
        top_k: int,
    ) -> list[RetrievedChunk]:
        """Cohere rerank followed by doc-type and recency multipliers.

        final_score = cohere_score * doc_type_mult * recency_mult

        Relocated from the former RerankerAgent — same low-score RRF fallback,
        Redis metadata enrichment, authority/recency weighting, and
        never-all-research-papers swap.
        """
        candidates = chunks or []
        current_year = datetime.utcnow().year

        try:
            reranked = await self.rerank(query, candidates, top_k)
        except CohereRerankException as exc:
            log_exception(logger, exc)
            logger.warning("Rerank failed; falling back to input order")
            reranked = candidates[:top_k]

        # Multi-topic / vague queries often get near-zero Cohere scores for
        # every chunk. Prefer the fused RRF order rather than trusting noise.
        max_cohere = max((float(c.score) for c in reranked), default=0.0)
        if candidates and (not reranked or max_cohere < _RERANK_MIN_USEFUL_SCORE):
            logger.warning(
                "Cohere rerank scores too low (max=%.4f); using fused ranking", max_cohere
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

            logger.debug(
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
                cand.model_copy(update={"score": float(cand.score) * doc_mult * recency_mult})
            )

        boosted.sort(key=lambda c: c.score, reverse=True)
        selected = boosted[:top_k]
        selected = self._ensure_not_all_research(selected, scored_pool)
        return [
            chunk.model_copy(update={"rank": rank})
            for rank, chunk in enumerate(selected, start=1)
        ]

    async def hybrid_retrieve(
        self,
        query: str,
        top_k: int,
        doc_filter: Optional[list[str]] = None,
    ) -> list[RetrievedChunk]:
        """Convenience single-query pipeline: dense + sparse + fuse + rerank.

        For the richer multi-sub-query / doc_type_filter path the Orchestrator
        pipeline uses, call dense_retrieve_multi / fuse / rerank_with_authority
        directly — this wrapper is the plain single-query case (e.g. for
        LLM-callable tools that only have one query string to work with).
        """
        dense_task = self.dense_search(query, top_k, doc_filter=doc_filter)
        sparse_task = self.sparse_search(query, top_k)
        dense_results, sparse_results = await asyncio.gather(dense_task, sparse_task)
        fused = await self.fuse(dense_results, sparse_results, top_k=top_k)
        return await self.rerank_with_authority(query, fused, top_k)

    async def upsert_vectors(self, vectors: list[dict]) -> None:
        from api.core.exceptions import PineconeUpsertException

        started = time.perf_counter()
        try:
            await asyncio.to_thread(self._index.upsert, vectors=vectors)
        except Exception as exc:
            log_api_call(logger, "pinecone", "upsert", (time.perf_counter() - started) * 1000, False)
            log_exception(logger, exc)
            raise PineconeUpsertException("Pinecone upsert failed") from exc
        log_api_call(logger, "pinecone", "upsert", (time.perf_counter() - started) * 1000, True)

    async def delete_vectors(self, ids: list[str]) -> None:
        from api.core.exceptions import PineconeException

        try:
            await asyncio.to_thread(self._index.delete, ids=ids)
        except Exception as exc:
            log_exception(logger, exc)
            raise PineconeException(f"Pinecone delete failed for {len(ids)} ids") from exc
