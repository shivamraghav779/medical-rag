import asyncio
import hashlib
import logging
from typing import Optional

import cohere

from api.core.config import settings
from api.tools import redis_tools

logger = logging.getLogger(__name__)

from api.core.constants import EMBED_MODEL
_client: Optional[cohere.AsyncClient] = None


def get_client() -> cohere.AsyncClient:
    global _client
    if _client is None:
        _client = cohere.AsyncClient(settings.cohere_api_key)
    return _client


def _hash_key(text: str, input_type: str) -> str:
    return hashlib.sha256(f"{input_type}:{text}".encode("utf-8")).hexdigest()


async def get_embedding(text: str, input_type: str) -> list[float]:
    text_hash = _hash_key(text, input_type)

    cached = await redis_tools.get_cached_embedding(text_hash)
    if cached is not None:
        logger.debug("Embedding cache hit for hash=%s", text_hash)
        return cached

    logger.debug("Embedding cache miss for hash=%s, calling Cohere", text_hash)
    response = await get_client().embed(
        texts=[text],
        model=EMBED_MODEL,
        input_type=input_type,
    )
    embedding = list(response.embeddings[0])

    await redis_tools.cache_embedding(text_hash, embedding, settings.embedding_cache_ttl_seconds)
    return embedding


async def get_embeddings_batch(texts: list[str], input_type: str) -> list[list[float]]:
    if not texts:
        return []

    hashes = [_hash_key(text, input_type) for text in texts]
    cached_results = await asyncio.gather(
        *(redis_tools.get_cached_embedding(h) for h in hashes)
    )

    results: list[Optional[list[float]]] = list(cached_results)
    miss_indices = [i for i, cached in enumerate(results) if cached is None]

    if miss_indices:
        logger.debug(
            "Embedding batch: %d/%d cache hits, calling Cohere for %d misses",
            len(texts) - len(miss_indices), len(texts), len(miss_indices),
        )
        miss_texts = [texts[i] for i in miss_indices]
        response = await get_client().embed(
            texts=miss_texts,
            model=EMBED_MODEL,
            input_type=input_type,
        )

        cache_writes = []
        for miss_position, text_index in enumerate(miss_indices):
            embedding = list(response.embeddings[miss_position])
            results[text_index] = embedding
            cache_writes.append(
                redis_tools.cache_embedding(
                    hashes[text_index], embedding, settings.embedding_cache_ttl_seconds
                )
            )
        await asyncio.gather(*cache_writes)
    else:
        logger.debug("Embedding batch: all %d texts served from cache", len(texts))

    return results
