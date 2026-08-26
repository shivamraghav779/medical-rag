"""RetrievalService — Pinecone dense search, BM25 sparse, RRF, Cohere rerank."""

from __future__ import annotations

import asyncio
import re
import time
from typing import Optional

import cohere

from api.core.config import settings
from api.core.exceptions import CohereRerankException, PineconeQueryException
from api.core.logger import conditional_traceable, get_logger, log_api_call, log_exception
from api.models.schemas import RetrievedChunk
from api.services.embedding_service import EmbeddingService
from api.services.redis_service import RedisService

logger = get_logger(__name__)

from api.core.constants import RERANK_MODEL, RRF_K

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
