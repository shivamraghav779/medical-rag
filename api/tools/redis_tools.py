import json
import logging
import time
import uuid
from typing import Any, Optional

from upstash_redis.asyncio import Redis

from api.core.constants import (
    ANSWER_CACHE,
    BM25_INDEX,
    CHUNK_TEXT,
    CHUNKS_INDEX,
    DOC_INDEX,
    DOC_META,
    EMB_CACHE,
    RATE_LIMIT,
    RATE_LIMIT_LUA_SCRIPT,
    SESSION_HISTORY,
)
from api.core.config import settings

logger = logging.getLogger(__name__)

_redis_client: Optional[Redis] = None


def get_redis_client() -> Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = Redis(
            url=settings.upstash_redis_rest_url,
            token=settings.upstash_redis_rest_token,
        )
    return _redis_client


def _serialize_value(value: Any) -> str:
    return json.dumps(value)


def _deserialize_value(value: str) -> Any:
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


# ---------------------------------------------------------------------------
# Generic cache
# ---------------------------------------------------------------------------

async def cache_get(key: str) -> Optional[str]:
    try:
        value = await get_redis_client().get(key)
        if value is None:
            logger.debug("Cache miss: %s", key)
        return value
    except Exception:
        logger.exception("cache_get failed for key=%s", key)
        return None


async def cache_set(key: str, value: str, ttl_seconds: int) -> bool:
    try:
        await get_redis_client().set(key, value, ex=ttl_seconds)
        return True
    except Exception:
        logger.exception("cache_set failed for key=%s", key)
        return False


# ---------------------------------------------------------------------------
# Document registry
# ---------------------------------------------------------------------------

async def store_document_metadata(doc_id: str, metadata_dict: dict) -> bool:
    key = DOC_META.format(doc_id=doc_id)
    try:
        client = get_redis_client()
        values = {k: _serialize_value(v) for k, v in metadata_dict.items()}
        await client.hset(key, values=values)
        await client.sadd(DOC_INDEX, doc_id)
        return True
    except Exception:
        logger.exception("store_document_metadata failed for doc_id=%s", doc_id)
        return False


async def get_document_metadata(doc_id: str) -> Optional[dict]:
    key = DOC_META.format(doc_id=doc_id)
    try:
        data = await get_redis_client().hgetall(key)
        if not data:
            logger.debug("Cache miss: %s", key)
            return None
        return {k: _deserialize_value(v) for k, v in data.items()}
    except Exception:
        logger.exception("get_document_metadata failed for doc_id=%s", doc_id)
        return None


async def list_all_documents() -> list[dict]:
    try:
        client = get_redis_client()
        doc_ids = await client.smembers(DOC_INDEX)
        if not doc_ids:
            return []
        docs = []
        for doc_id in doc_ids:
            metadata = await get_document_metadata(doc_id)
            if metadata is not None:
                docs.append(metadata)
        return docs
    except Exception:
        logger.exception("list_all_documents failed")
        return []


async def delete_document_data(doc_id: str) -> bool:
    """Not part of the original 16-function tool list — required by
    DELETE /api/documents/{doc_id} to clean up metadata + chunk registry."""
    try:
        client = get_redis_client()
        metadata = await get_document_metadata(doc_id)
        chunk_ids = metadata.get("chunk_ids", []) if metadata else []

        for chunk_id in chunk_ids:
            await client.delete(CHUNK_TEXT.format(chunk_id=chunk_id))
            await client.srem(CHUNKS_INDEX, chunk_id)

        await client.delete(DOC_META.format(doc_id=doc_id))
        await client.srem(DOC_INDEX, doc_id)
        return True
    except Exception:
        logger.exception("delete_document_data failed for doc_id=%s", doc_id)
        return False


# ---------------------------------------------------------------------------
# Chunk text registry
# ---------------------------------------------------------------------------

async def store_chunk_text(chunk_id: str, text: str) -> bool:
    """chunk:{chunk_id} is a Redis hash (fields: text, doc_name, page_number),
    not a plain string — this writes only the `text` field so callers relying
    on this exact spec'd signature are unaffected by the doc_name/page_number
    fields added via store_chunk_metadata."""
    try:
        client = get_redis_client()
        await client.hset(CHUNK_TEXT.format(chunk_id=chunk_id), values={"text": text})
        await client.sadd(CHUNKS_INDEX, chunk_id)
        return True
    except Exception:
        logger.exception("store_chunk_text failed for chunk_id=%s", chunk_id)
        return False


async def get_chunk_text(chunk_id: str) -> Optional[str]:
    try:
        data = await get_redis_client().hgetall(CHUNK_TEXT.format(chunk_id=chunk_id))
        if not data or "text" not in data:
            logger.debug("Cache miss: chunk:%s", chunk_id)
            return None
        return data["text"]
    except Exception:
        logger.exception("get_chunk_text failed for chunk_id=%s", chunk_id)
        return None


async def get_all_chunk_texts() -> dict[str, str]:
    try:
        client = get_redis_client()
        chunk_ids = await client.smembers(CHUNKS_INDEX)
        if not chunk_ids:
            return {}
        result: dict[str, str] = {}
        for chunk_id in chunk_ids:
            text = await get_chunk_text(chunk_id)
            if text is not None:
                result[chunk_id] = text
        return result
    except Exception:
        logger.exception("get_all_chunk_texts failed")
        return {}


async def store_chunk_metadata(chunk_id: str, doc_name: str, page_number: int) -> bool:
    """Not part of the original 16-function tool list. Merges doc_name/page_number
    into the same chunk:{chunk_id} hash written by store_chunk_text, so sparse
    retrieval results can carry correct citation metadata without hitting Pinecone."""
    try:
        client = get_redis_client()
        await client.hset(
            CHUNK_TEXT.format(chunk_id=chunk_id),
            values={"doc_name": doc_name, "page_number": page_number},
        )
        return True
    except Exception:
        logger.exception("store_chunk_metadata failed for chunk_id=%s", chunk_id)
        return False


async def get_all_chunk_records() -> dict[str, dict]:
    """Not part of the original 16-function tool list. Returns chunk_id -> full
    record (text, doc_name, page_number) in one hash read per chunk, used by
    sparse_search to rebuild the BM25 index with correct citation metadata."""
    try:
        client = get_redis_client()
        chunk_ids = await client.smembers(CHUNKS_INDEX)
        if not chunk_ids:
            return {}
        result: dict[str, dict] = {}
        for chunk_id in chunk_ids:
            data = await client.hgetall(CHUNK_TEXT.format(chunk_id=chunk_id))
            if not data or "text" not in data:
                continue
            result[chunk_id] = {
                "text": data["text"],
                "doc_name": data.get("doc_name", "unknown"),
                "page_number": int(data["page_number"]) if data.get("page_number") else 1,
            }
        return result
    except Exception:
        logger.exception("get_all_chunk_records failed")
        return {}


# ---------------------------------------------------------------------------
# Conversation history
# ---------------------------------------------------------------------------

async def store_conversation_message(session_id: str, role: str, content: str) -> bool:
    key = SESSION_HISTORY.format(session_id=session_id)
    try:
        client = get_redis_client()
        message = json.dumps({
            "role": role,
            "content": content,
            "timestamp": time.time(),
        })
        await client.rpush(key, message)
        await client.ltrim(key, -settings.conversation_history_max, -1)
        await client.expire(key, settings.conversation_history_ttl_seconds)
        return True
    except Exception:
        logger.exception("store_conversation_message failed for session_id=%s", session_id)
        return False


async def get_conversation_history(session_id: str) -> list[dict]:
    key = SESSION_HISTORY.format(session_id=session_id)
    try:
        raw_messages = await get_redis_client().lrange(key, 0, -1)
        history = []
        for raw in raw_messages:
            try:
                history.append(json.loads(raw))
            except (json.JSONDecodeError, TypeError):
                logger.warning("Skipping malformed history entry in %s", key)
        return history
    except Exception:
        logger.exception("get_conversation_history failed for session_id=%s", session_id)
        return []


# ---------------------------------------------------------------------------
# Embedding cache
# ---------------------------------------------------------------------------

async def get_cached_embedding(text_hash: str) -> Optional[list[float]]:
    key = EMB_CACHE.format(text_hash=text_hash)
    try:
        raw = await get_redis_client().get(key)
        if raw is None:
            logger.debug("Cache miss: %s", key)
            return None
        return json.loads(raw)
    except Exception:
        logger.exception("get_cached_embedding failed for text_hash=%s", text_hash)
        return None


async def cache_embedding(text_hash: str, embedding: list[float], ttl: int) -> bool:
    key = EMB_CACHE.format(text_hash=text_hash)
    try:
        await get_redis_client().set(key, json.dumps(embedding), ex=ttl)
        return True
    except Exception:
        logger.exception("cache_embedding failed for text_hash=%s", text_hash)
        return False


# ---------------------------------------------------------------------------
# Answer cache
# ---------------------------------------------------------------------------

async def get_cached_answer(query_hash: str) -> Optional[str]:
    key = ANSWER_CACHE.format(query_hash=query_hash)
    try:
        value = await get_redis_client().get(key)
        if value is None:
            logger.debug("Cache miss: %s", key)
        return value
    except Exception:
        logger.exception("get_cached_answer failed for query_hash=%s", query_hash)
        return None


async def cache_answer(query_hash: str, answer: str, ttl: int) -> bool:
    key = ANSWER_CACHE.format(query_hash=query_hash)
    try:
        await get_redis_client().set(key, answer, ex=ttl)
        return True
    except Exception:
        logger.exception("cache_answer failed for query_hash=%s", query_hash)
        return False


# ---------------------------------------------------------------------------
# BM25 index cache
# ---------------------------------------------------------------------------

async def invalidate_bm25_cache() -> bool:
    try:
        await get_redis_client().delete(BM25_INDEX)
        return True
    except Exception:
        logger.exception("invalidate_bm25_cache failed")
        return False


async def get_cached_bm25_index() -> Optional[dict]:
    try:
        raw = await get_redis_client().get(BM25_INDEX)
        if raw is None:
            logger.debug("Cache miss: %s", BM25_INDEX)
            return None
        return json.loads(raw)
    except Exception:
        logger.exception("get_cached_bm25_index failed")
        return None


async def cache_bm25_index(index_data: dict) -> bool:
    try:
        await get_redis_client().set(BM25_INDEX, json.dumps(index_data))
        return True
    except Exception:
        logger.exception("cache_bm25_index failed")
        return False


# ---------------------------------------------------------------------------
# Rate limiting (sliding window) — not part of the original 16-function list,
# required by implementation rule #8.
# ---------------------------------------------------------------------------

async def check_rate_limit(identifier: str) -> tuple[bool, int]:
    """Returns (allowed, retry_after_seconds)."""
    key = RATE_LIMIT.format(ip=identifier)
    try:
        client = get_redis_client()
        now = time.time()
        member = str(uuid.uuid4())

        result = await client.eval(
            RATE_LIMIT_LUA_SCRIPT,
            keys=[key],
            args=[
                str(now),
                str(settings.rate_limit_window_seconds),
                str(settings.rate_limit_max_requests),
                member,
            ],
        )

        allowed = int(result) == 1
        return allowed, 0 if allowed else settings.rate_limit_window_seconds
    except Exception:
        logger.exception("check_rate_limit failed for identifier=%s", identifier)
        return True, 0
