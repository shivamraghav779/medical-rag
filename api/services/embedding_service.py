"""EmbeddingService — Cohere embeddings with Redis cache."""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Optional

import cohere

from api.core.config import settings
from api.core.exceptions import CohereEmbedException, RedisReadException, RedisWriteException
from api.core.logger import conditional_traceable, get_logger, log_api_call, log_exception
from api.services.redis_service import RedisService

logger = get_logger(__name__)

from api.core.constants import EMBED_MODEL

class EmbeddingService:
    def __init__(self, cohere_client: cohere.AsyncClient, redis_service: RedisService):
        self._client = cohere_client
        self._redis = redis_service

    @staticmethod
    def _hash_key(text: str, input_type: str) -> str:
        return hashlib.sha256(f"{input_type}:{text}".encode("utf-8")).hexdigest()

    @conditional_traceable(name="EmbeddingService.embed_query", run_type="embedding")
    async def embed_query(self, text: str) -> list[float]:
        return await self._embed_one(text, "search_query")

    @conditional_traceable(name="EmbeddingService.embed_documents", run_type="embedding")
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._embed_many(texts, "search_document")

    async def get_embedding(self, text: str, input_type: str) -> list[float]:
        """Back-compat alias used by RetrievalService.dense_retrieve_multi."""
        return await self._embed_one(text, input_type)

    async def get_embeddings_batch(self, texts: list[str], input_type: str) -> list[list[float]]:
        return await self._embed_many(texts, input_type)

    async def _embed_one(self, text: str, input_type: str) -> list[float]:
        text_hash = self._hash_key(text, input_type)

        cached: Optional[list[float]] = None
        try:
            cached = await self._redis.get_cached_embedding(text_hash)
        except RedisReadException as exc:
            # Cache failure is non-fatal for embedding — continue to Cohere.
            log_exception(logger, exc)
            logger.warning("Embedding cache read failed; continuing without cache")

        if cached is not None:
            return cached

        started = time.perf_counter()
        try:
            response = await self._client.embed(
                texts=[text],
                model=EMBED_MODEL,
                input_type=input_type,
            )
            embedding = list(response.embeddings[0])
        except Exception as exc:
            log_api_call(logger, "cohere", "embed", (time.perf_counter() - started) * 1000, False)
            log_exception(logger, exc)
            raise CohereEmbedException(f"Cohere embed failed for input_type={input_type}") from exc

        log_api_call(logger, "cohere", "embed", (time.perf_counter() - started) * 1000, True)

        try:
            await self._redis.cache_embedding(text_hash, embedding, settings.embedding_cache_ttl_seconds)
        except RedisWriteException as exc:
            log_exception(logger, exc)
            logger.warning("Embedding cache write failed; returning embedding anyway")

        return embedding

    async def _embed_many(self, texts: list[str], input_type: str) -> list[list[float]]:
        if not texts:
            return []

        hashes = [self._hash_key(text, input_type) for text in texts]
        results: list[Optional[list[float]]] = [None] * len(texts)

        async def _safe_cache_get(h: str) -> Optional[list[float]]:
            try:
                return await self._redis.get_cached_embedding(h)
            except RedisReadException as exc:
                log_exception(logger, exc)
                return None

        cached_results = await asyncio.gather(*(_safe_cache_get(h) for h in hashes))
        for i, cached in enumerate(cached_results):
            results[i] = cached

        miss_indices = [i for i, cached in enumerate(results) if cached is None]
        if not miss_indices:
            logger.debug(f"Embedding batch: all {len(texts)} texts served from cache")
            return results  # type: ignore[return-value]

        miss_texts = [texts[i] for i in miss_indices]
        started = time.perf_counter()
        try:
            response = await self._client.embed(
                texts=miss_texts,
                model=EMBED_MODEL,
                input_type=input_type,
            )
        except Exception as exc:
            log_api_call(logger, "cohere", "embed_batch", (time.perf_counter() - started) * 1000, False)
            log_exception(logger, exc)
            raise CohereEmbedException(
                f"Cohere batch embed failed for {len(miss_texts)} texts"
            ) from exc

        log_api_call(logger, "cohere", "embed_batch", (time.perf_counter() - started) * 1000, True)

        cache_writes = []
        for miss_position, text_index in enumerate(miss_indices):
            embedding = list(response.embeddings[miss_position])
            results[text_index] = embedding
            cache_writes.append(
                self._safe_cache_write(hashes[text_index], embedding)
            )
        await asyncio.gather(*cache_writes)
        return results  # type: ignore[return-value]

    async def _safe_cache_write(self, text_hash: str, embedding: list[float]) -> None:
        try:
            await self._redis.cache_embedding(
                text_hash, embedding, settings.embedding_cache_ttl_seconds
            )
        except RedisWriteException as exc:
            log_exception(logger, exc)
