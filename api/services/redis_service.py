"""RedisService — all Redis I/O for the platform.

Wraps the Upstash async client. Every method raises a typed RedisException
subclass on failure. Cache helpers emit structured hit/miss logs.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Optional

from upstash_redis.asyncio import Redis

from api.core import redis_keys as keys
from api.core.config import settings
from api.core.exceptions import (
    RedisConnectionException,
    RedisReadException,
    RedisWriteException,
)
from api.core.logger import get_logger, log_cache_hit, log_cache_miss, log_exception

logger = get_logger(__name__)

from api.core.constants import (
    ANALYTICS_EMERGENCY_MAX,
    ANALYTICS_FAITHFULNESS_MAX,
    ANALYTICS_TOP_DRUGS_DEFAULT_LIMIT,
    LOCK_QUEUE_ROUTING,
    LOCK_RELEASE_LUA_SCRIPT,
    QUEUE_ROUTING_LOCK_POLL_INTERVAL_SECONDS,
    QUEUE_ROUTING_LOCK_TTL_SECONDS,
    QUEUE_ROUTING_LOCK_WAIT_SECONDS,
    RATE_LIMIT_LUA_SCRIPT,
)

def _serialize_value(value: Any) -> str:
    return json.dumps(value)


def _deserialize_value(value: str) -> Any:
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


class RedisService:
    def __init__(self, client: Redis):
        self._client = client
        # Process-local BM25 cache — Redis often can't hold the full index
        # (payload too large / eviction), which forced a full rebuild every query.
        self._bm25_local: Optional[dict] = None

    # ------------------------------------------------------------------
    # Generic cache
    # ------------------------------------------------------------------

    async def cache_get(self, key: str) -> Optional[str]:
        try:
            value = await self._client.get(key)
            if value is None:
                log_cache_miss(logger, "generic", key.split(":")[0])
            else:
                log_cache_hit(logger, "generic", key.split(":")[0])
            return value
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisReadException(f"cache_get failed for key={key}") from exc

    async def cache_set(self, key: str, value: str, ttl_seconds: int) -> bool:
        try:
            await self._client.set(key, value, ex=ttl_seconds)
            return True
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisWriteException(f"cache_set failed for key={key}") from exc

    # ------------------------------------------------------------------
    # Document registry
    # ------------------------------------------------------------------

    async def store_document_metadata(self, doc_id: str, metadata_dict: dict) -> bool:
        key = keys.DOC_META.format(doc_id=doc_id)
        try:
            values = {k: _serialize_value(v) for k, v in metadata_dict.items()}
            await self._client.hset(key, values=values)
            await self._client.sadd(keys.DOC_INDEX, doc_id)
            return True
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisWriteException(f"store_document_metadata failed for doc_id={doc_id}") from exc

    async def get_document_metadata(self, doc_id: str) -> Optional[dict]:
        key = keys.DOC_META.format(doc_id=doc_id)
        try:
            data = await self._client.hgetall(key)
            if not data:
                log_cache_miss(logger, "doc_meta", "doc")
                return None
            log_cache_hit(logger, "doc_meta", "doc")
            return {k: _deserialize_value(v) for k, v in data.items()}
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisReadException(f"get_document_metadata failed for doc_id={doc_id}") from exc

    async def list_all_documents(self) -> list[dict]:
        try:
            doc_ids = await self._client.smembers(keys.DOC_INDEX)
            if not doc_ids:
                return []
            docs = []
            for doc_id in doc_ids:
                metadata = await self.get_document_metadata(doc_id)
                if metadata is not None:
                    docs.append(metadata)
            return docs
        except RedisReadException:
            raise
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisReadException("list_all_documents failed") from exc

    async def delete_document_data(self, doc_id: str) -> bool:
        try:
            metadata = await self.get_document_metadata(doc_id)
            chunk_ids = metadata.get("chunk_ids", []) if metadata else []

            for chunk_id in chunk_ids:
                await self._client.delete(keys.CHUNK_TEXT.format(chunk_id=chunk_id))
                await self._client.delete(keys.CHUNK_META.format(chunk_id=chunk_id))
                await self._client.srem(keys.CHUNKS_INDEX, chunk_id)

            await self._client.delete(keys.DOC_META.format(doc_id=doc_id))
            await self._client.srem(keys.DOC_INDEX, doc_id)
            return True
        except RedisReadException:
            raise
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisWriteException(f"delete_document_data failed for doc_id={doc_id}") from exc

    # ------------------------------------------------------------------
    # Chunk text + metadata (split keys)
    # ------------------------------------------------------------------

    async def store_chunk_text(self, chunk_id: str, text: str) -> bool:
        try:
            await self._client.set(keys.CHUNK_TEXT.format(chunk_id=chunk_id), text)
            await self._client.sadd(keys.CHUNKS_INDEX, chunk_id)
            return True
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisWriteException(f"store_chunk_text failed for chunk_id={chunk_id}") from exc

    async def get_chunk_text(self, chunk_id: str) -> Optional[str]:
        text_key = keys.CHUNK_TEXT.format(chunk_id=chunk_id)
        try:
            value = await self._client.get(text_key)
            if isinstance(value, str) and value:
                return value
            # Legacy fallback: older uploads stored a hash at chunk:{id}.
            data = await self._client.hgetall(text_key)
            if data and "text" in data:
                return data["text"]
            log_cache_miss(logger, "chunk_text", "chunk")
            return None
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisReadException(f"get_chunk_text failed for chunk_id={chunk_id}") from exc

    async def store_chunk_metadata(
        self,
        chunk_id: str,
        doc_name: str,
        page_number: int,
        *,
        authority_level: int = 1,
        doc_type: Optional[str] = None,
        source_org: Optional[str] = None,
        **extra: Any,
    ) -> bool:
        try:
            values: dict[str, Any] = {
                "doc_name": doc_name,
                "page_number": page_number,
                "authority_level": authority_level,
            }
            if doc_type is not None:
                values["doc_type"] = doc_type
            if source_org is not None:
                values["source_org"] = source_org
            values.update(extra)
            await self._client.hset(
                keys.CHUNK_META.format(chunk_id=chunk_id),
                values={k: _serialize_value(v) for k, v in values.items()},
            )
            return True
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisWriteException(f"store_chunk_metadata failed for chunk_id={chunk_id}") from exc

    async def get_chunk_metadata(self, chunk_id: str) -> Optional[dict]:
        meta_key = keys.CHUNK_META.format(chunk_id=chunk_id)
        text_key = keys.CHUNK_TEXT.format(chunk_id=chunk_id)
        try:
            data = await self._client.hgetall(meta_key)
            if data:
                return {k: _deserialize_value(v) for k, v in data.items()}
            # Legacy: metadata lived on the same hash as text.
            legacy = await self._client.hgetall(text_key)
            if legacy:
                return {
                    k: _deserialize_value(v)
                    for k, v in legacy.items()
                    if k != "text"
                }
            return None
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisReadException(f"get_chunk_metadata failed for chunk_id={chunk_id}") from exc

    async def get_all_chunk_texts(self) -> dict[str, str]:
        try:
            chunk_ids = await self._client.smembers(keys.CHUNKS_INDEX)
            if not chunk_ids:
                return {}
            result: dict[str, str] = {}
            for chunk_id in chunk_ids:
                text = await self.get_chunk_text(chunk_id)
                if text is not None:
                    result[chunk_id] = text
            return result
        except RedisReadException:
            raise
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisReadException("get_all_chunk_texts failed") from exc

    async def get_all_chunk_records(self) -> dict[str, dict]:
        """Load all chunk text+meta for BM25 rebuild.

        Uses bounded concurrency — sequential Upstash REST calls for hundreds of
        chunks can take 60–90s+ and stall the chat pipeline.
        """
        import asyncio

        try:
            chunk_ids = list(await self._client.smembers(keys.CHUNKS_INDEX) or [])
            if not chunk_ids:
                return {}

            sem = asyncio.Semaphore(20)

            async def _one(chunk_id: str) -> Optional[tuple[str, dict]]:
                async with sem:
                    text = await self.get_chunk_text(chunk_id)
                    if text is None:
                        return None
                    meta = await self.get_chunk_metadata(chunk_id) or {}
                    return chunk_id, {
                        "text": text,
                        "doc_name": meta.get("doc_name", "unknown"),
                        "page_number": int(meta.get("page_number", 1) or 1),
                        "authority_level": int(meta.get("authority_level", 1) or 1),
                        "doc_type": meta.get("doc_type"),
                        "source_org": meta.get("source_org"),
                    }

            rows = await asyncio.gather(*(_one(cid) for cid in chunk_ids))
            result: dict[str, dict] = {}
            for item in rows:
                if item is None:
                    continue
                cid, record = item
                result[cid] = record
            return result
        except RedisReadException:
            raise
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisReadException("get_all_chunk_records failed") from exc

    # ------------------------------------------------------------------
    # Conversation history + session context
    # ------------------------------------------------------------------

    async def store_conversation_message(self, session_id: str, role: str, content: str) -> bool:
        key = keys.SESSION_HISTORY.format(session_id=session_id)
        try:
            message = json.dumps({
                "role": role,
                "content": content,
                "timestamp": time.time(),
            })
            await self._client.rpush(key, message)
            await self._client.ltrim(key, -settings.conversation_history_max, -1)
            await self._client.expire(key, settings.conversation_history_ttl_seconds)
            return True
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisWriteException(
                f"store_conversation_message failed for session_id={session_id}"
            ) from exc

    async def get_conversation_history(self, session_id: str) -> list[dict]:
        key = keys.SESSION_HISTORY.format(session_id=session_id)
        try:
            raw_messages = await self._client.lrange(key, 0, -1)
            history = []
            for raw in raw_messages:
                try:
                    history.append(json.loads(raw))
                except (json.JSONDecodeError, TypeError):
                    logger.warning("Skipping malformed history entry", extra={"session_id": session_id})
            return history
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisReadException(
                f"get_conversation_history failed for session_id={session_id}"
            ) from exc

    async def store_session_context(self, session_id: str, context: dict) -> bool:
        key = keys.SESSION_CONTEXT.format(session_id=session_id)
        try:
            values = {k: _serialize_value(v) for k, v in context.items() if v is not None}
            if not values:
                return True
            await self._client.hset(key, values=values)
            await self._client.expire(key, settings.conversation_history_ttl_seconds)
            return True
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisWriteException(
                f"store_session_context failed for session_id={session_id}"
            ) from exc

    async def get_session_context(self, session_id: str) -> Optional[dict]:
        key = keys.SESSION_CONTEXT.format(session_id=session_id)
        try:
            data = await self._client.hgetall(key)
            if not data:
                return None
            return {k: _deserialize_value(v) for k, v in data.items()}
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisReadException(
                f"get_session_context failed for session_id={session_id}"
            ) from exc

    # ------------------------------------------------------------------
    # Embedding / answer / BM25 caches
    # ------------------------------------------------------------------

    async def get_cached_embedding(self, text_hash: str) -> Optional[list[float]]:
        key = keys.EMB_CACHE.format(text_hash=text_hash)
        try:
            raw = await self._client.get(key)
            if raw is None:
                log_cache_miss(logger, "embedding", "emb")
                return None
            log_cache_hit(logger, "embedding", "emb")
            return json.loads(raw)
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisReadException(f"get_cached_embedding failed for text_hash={text_hash}") from exc

    async def cache_embedding(self, text_hash: str, embedding: list[float], ttl: int) -> bool:
        key = keys.EMB_CACHE.format(text_hash=text_hash)
        try:
            await self._client.set(key, json.dumps(embedding), ex=ttl)
            return True
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisWriteException(f"cache_embedding failed for text_hash={text_hash}") from exc

    async def get_cached_answer(self, query_hash: str) -> Optional[str]:
        key = keys.ANSWER_CACHE.format(query_hash=query_hash)
        try:
            value = await self._client.get(key)
            if value is None:
                log_cache_miss(logger, "answer", "answer")
            else:
                log_cache_hit(logger, "answer", "answer")
            return value
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisReadException(f"get_cached_answer failed for query_hash={query_hash}") from exc

    async def cache_answer(self, query_hash: str, answer: str, ttl: int) -> bool:
        key = keys.ANSWER_CACHE.format(query_hash=query_hash)
        try:
            await self._client.set(key, answer, ex=ttl)
            return True
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisWriteException(f"cache_answer failed for query_hash={query_hash}") from exc

    async def invalidate_bm25_cache(self) -> bool:
        self._bm25_local = None
        try:
            await self._client.delete(keys.BM25_INDEX)
            await self._client.delete(keys.BM25_BUILT_AT)
            return True
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisWriteException("invalidate_bm25_cache failed") from exc

    async def get_cached_bm25_index(self) -> Optional[dict]:
        if self._bm25_local is not None:
            log_cache_hit(logger, "bm25", "bm25")
            return self._bm25_local
        try:
            raw = await self._client.get(keys.BM25_INDEX)
            if raw is None:
                log_cache_miss(logger, "bm25", "bm25", critical=True)
                return None
            log_cache_hit(logger, "bm25", "bm25")
            parsed = json.loads(raw)
            self._bm25_local = parsed
            return parsed
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisReadException("get_cached_bm25_index failed") from exc

    async def cache_bm25_index(self, index_data: dict) -> bool:
        # Always keep a process-local copy so chat stays fast even if Redis
        # rejects / evicts the large serialized index.
        self._bm25_local = index_data
        try:
            await self._client.set(keys.BM25_INDEX, json.dumps(index_data))
            await self._client.set(keys.BM25_BUILT_AT, str(time.time()))
            return True
        except Exception as exc:
            log_exception(logger, exc)
            logger.warning(
                "BM25 Redis cache write failed; using in-memory index only",
                extra={"cache_type": "bm25"},
            )
            return False

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    async def check_rate_limit(self, identifier: str) -> tuple[bool, int]:
        """Returns (allowed, retry_after_seconds). Fail-open on Redis errors."""
        key = keys.RATE_LIMIT.format(ip=identifier)
        try:
            now = time.time()
            member = str(uuid.uuid4())
            result = await self._client.eval(
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
            if not allowed:
                logger.warning(
                    "Rate limit exceeded",
                    extra={"client_ip": identifier, "retry_after": settings.rate_limit_window_seconds},
                )
            return allowed, 0 if allowed else settings.rate_limit_window_seconds
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisConnectionException(
                f"check_rate_limit failed for identifier={identifier}"
            ) from exc

    # ------------------------------------------------------------------
    # Healthcare / drug graph
    # ------------------------------------------------------------------

    async def store_drug_info(self, drug_name: str, drug_dict: dict) -> bool:
        name = drug_name.strip().lower()
        try:
            await self._client.set(
                keys.DRUG_INFO.format(drug_name=name),
                json.dumps(drug_dict),
            )
            await self._client.sadd(keys.DRUG_INDEX, name)
            return True
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisWriteException(f"store_drug_info failed for drug={name}") from exc

    async def get_drug_info(self, drug_name: str) -> Optional[dict]:
        name = drug_name.strip().lower()
        try:
            raw = await self._client.get(keys.DRUG_INFO.format(drug_name=name))
            if raw is None:
                log_cache_miss(logger, "drug_info", "drug")
                return None
            log_cache_hit(logger, "drug_info", "drug")
            return json.loads(raw)
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisReadException(f"get_drug_info failed for drug={name}") from exc

    async def store_interaction(self, drug_a: str, drug_b: str, result_dict: dict) -> bool:
        a, b = sorted([drug_a.strip().lower(), drug_b.strip().lower()])
        try:
            await self._client.set(
                keys.INTERACTION.format(drug_a=a, drug_b=b),
                json.dumps(result_dict),
                ex=settings.drug_interaction_cache_ttl_seconds,
            )
            return True
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisWriteException(f"store_interaction failed for {a}/{b}") from exc

    async def get_interaction(self, drug_a: str, drug_b: str) -> Optional[dict]:
        a, b = sorted([drug_a.strip().lower(), drug_b.strip().lower()])
        try:
            raw = await self._client.get(keys.INTERACTION.format(drug_a=a, drug_b=b))
            if raw is None:
                log_cache_miss(logger, "interaction", "interaction")
                return None
            log_cache_hit(logger, "interaction", "interaction")
            return json.loads(raw)
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisReadException(f"get_interaction failed for {a}/{b}") from exc

    async def add_to_drug_index(self, drug_name: str) -> bool:
        name = drug_name.strip().lower()
        try:
            await self._client.sadd(keys.DRUG_INDEX, name)
            return True
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisWriteException(f"add_to_drug_index failed for drug={name}") from exc

    async def get_all_drugs(self) -> list[str]:
        try:
            members = await self._client.smembers(keys.DRUG_INDEX)
            return sorted(members) if members else []
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisReadException("get_all_drugs failed") from exc

    async def log_emergency_query(
        self,
        session_id: str,
        query: str,
        timestamp: float,
        matched_terms: Optional[list[str]] = None,
    ) -> bool:
        try:
            entry = json.dumps({
                "session_id": session_id,
                "query": query,
                "matched_terms": matched_terms or [],
                "timestamp": timestamp,
            })
            # Cap at 100 most recent emergency flags (newest first).
            await self._client.lpush(keys.ANALYTICS_EMERGENCY, entry)
            await self._client.ltrim(keys.ANALYTICS_EMERGENCY, 0, ANALYTICS_EMERGENCY_MAX - 1)
            return True
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisWriteException("log_emergency_query failed") from exc

    async def get_emergency_query_log(self) -> list[dict]:
        try:
            raw_entries = await self._client.lrange(keys.ANALYTICS_EMERGENCY, 0, -1)
            results = []
            for raw in raw_entries or []:
                try:
                    results.append(json.loads(raw))
                except (json.JSONDecodeError, TypeError):
                    continue
            return results
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisReadException("get_emergency_query_log failed") from exc

    async def increment_query_type_counter(self, query_type: str) -> bool:
        try:
            await self._client.hincrby(keys.ANALYTICS_QUERY_TYPES, query_type, 1)
            return True
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisWriteException(
                f"increment_query_type_counter failed for query_type={query_type}"
            ) from exc

    async def get_query_type_analytics(self) -> dict:
        try:
            data = await self._client.hgetall(keys.ANALYTICS_QUERY_TYPES)
            if not data:
                return {}
            return {k: int(v) for k, v in data.items()}
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisReadException("get_query_type_analytics failed") from exc

    # ------------------------------------------------------------------
    # Lab ranges
    # ------------------------------------------------------------------

    async def store_lab_range(self, parameter: str, range_dict: dict) -> bool:
        param = parameter.strip().lower().replace(" ", "_")
        try:
            await self._client.set(
                keys.LAB_RANGE.format(parameter=param),
                json.dumps(range_dict),
            )
            await self._client.sadd(keys.LAB_RANGE_INDEX, param)
            return True
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisWriteException(f"store_lab_range failed for {param}") from exc

    async def get_lab_range(self, parameter: str) -> Optional[dict]:
        param = parameter.strip().lower().replace(" ", "_")
        try:
            raw = await self._client.get(keys.LAB_RANGE.format(parameter=param))
            if raw is None:
                log_cache_miss(logger, "lab_range", "lab")
                return None
            log_cache_hit(logger, "lab_range", "lab")
            return json.loads(raw)
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisReadException(f"get_lab_range failed for {param}") from exc

    async def add_to_lab_index(self, parameter: str) -> bool:
        param = parameter.strip().lower().replace(" ", "_")
        try:
            await self._client.sadd(keys.LAB_RANGE_INDEX, param)
            return True
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisWriteException(f"add_to_lab_index failed for {param}") from exc

    # ------------------------------------------------------------------
    # Extended analytics
    # ------------------------------------------------------------------

    async def track_drug_mention(self, drug_name: str) -> bool:
        name = drug_name.strip().lower()
        try:
            await self._client.zincrby(keys.ANALYTICS_TOP_DRUGS, 1, name)
            return True
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisWriteException(f"track_drug_mention failed for {name}") from exc

    async def get_top_drugs(self, limit: int = ANALYTICS_TOP_DRUGS_DEFAULT_LIMIT) -> list[dict]:
        try:
            rows = await self._client.zrange(
                keys.ANALYTICS_TOP_DRUGS, 0, limit - 1, withscores=True, rev=True
            )
            # upstash may return flat [member, score, ...] or list of pairs
            results = []
            if not rows:
                return []
            if isinstance(rows[0], (list, tuple)) and len(rows[0]) == 2:
                for member, score in rows:
                    results.append({"drug": member, "count": int(score)})
            else:
                for i in range(0, len(rows), 2):
                    results.append({"drug": rows[i], "count": int(float(rows[i + 1]))})
            return results
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisReadException("get_top_drugs failed") from exc

    async def increment_doc_type_query(self, doc_type: str) -> bool:
        try:
            await self._client.hincrby(keys.ANALYTICS_DOC_TYPE_QUERIES, doc_type, 1)
            return True
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisWriteException(
                f"increment_doc_type_query failed for doc_type={doc_type}"
            ) from exc

    async def get_doc_type_query_analytics(self) -> dict:
        try:
            data = await self._client.hgetall(keys.ANALYTICS_DOC_TYPE_QUERIES)
            if not data:
                return {}
            return {k: int(v) for k, v in data.items()}
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisReadException("get_doc_type_query_analytics failed") from exc

    async def store_faithfulness_score(self, score: float) -> bool:
        try:
            await self._client.lpush(keys.ANALYTICS_FAITHFULNESS, str(float(score)))
            await self._client.ltrim(keys.ANALYTICS_FAITHFULNESS, 0, ANALYTICS_FAITHFULNESS_MAX - 1)
            return True
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisWriteException("store_faithfulness_score failed") from exc

    async def get_faithfulness_scores(self) -> list[float]:
        try:
            raw = await self._client.lrange(keys.ANALYTICS_FAITHFULNESS, 0, -1)
            scores = []
            for item in raw or []:
                try:
                    scores.append(float(item))
                except (TypeError, ValueError):
                    continue
            return scores
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisReadException("get_faithfulness_scores failed") from exc

    async def get_all_analytics(self) -> dict:
        """Single call payload for GET /api/analytics."""
        query_types = await self.get_query_type_analytics()
        top_drugs = await self.get_top_drugs()
        flagged = await self.get_emergency_query_log()
        doc_types = await self.get_doc_type_query_analytics()
        scores = await self.get_faithfulness_scores()
        rolling = sum(scores) / len(scores) if scores else None
        return {
            "query_types": query_types,
            "top_drugs": top_drugs,
            "flagged_emergency": flagged,
            "doc_type_queries": doc_types,
            "faithfulness_scores": scores,
            "faithfulness_rolling_avg": rolling,
        }

    async def ping(self) -> str:
        try:
            return await self._client.ping()
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisConnectionException("Redis ping failed") from exc

    # ------------------------------------------------------------------
    # Human handoff queue (sorted set by request timestamp)
    # ------------------------------------------------------------------

    async def queue_add(self, session_id: str, requested_at: Optional[float] = None) -> int:
        """Add session to pending handoff queue. Score = unix timestamp. Returns position (0-based)."""
        score = float(requested_at if requested_at is not None else time.time())
        try:
            # Preserve original wait time if already queued (do not reset score)
            existing = await self._client.zscore(keys.QUEUE_PENDING, session_id)
            if existing is None:
                await self._client.zadd(keys.QUEUE_PENDING, {session_id: score})
            else:
                score = float(existing)
            # Persist original queue timestamp for disconnect requeue
            state_key = keys.SESSION_STATE.format(session_id=session_id)
            raw = await self._client.get(state_key)
            payload = json.loads(raw) if isinstance(raw, str) and raw else {}
            if not isinstance(payload, dict):
                payload = {}
            payload.setdefault("queued_at", score)
            payload["state"] = payload.get("state") or "QUEUED"
            payload["updated_at"] = time.time()
            await self._client.set(
                state_key,
                json.dumps(payload),
                ex=settings.conversation_history_ttl_seconds,
            )
            position = await self.queue_position(session_id)
            logger.info(
                "Handoff queue add",
                extra={"session_id": session_id, "queue_position": position},
            )
            return position if position is not None else 0
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisWriteException(f"queue_add failed for session_id={session_id}") from exc

    async def queue_remove(self, session_id: str) -> bool:
        try:
            await self._client.zrem(keys.QUEUE_PENDING, session_id)
            logger.info("Handoff queue remove", extra={"session_id": session_id})
            return True
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisWriteException(f"queue_remove failed for session_id={session_id}") from exc

    async def queue_position(self, session_id: str) -> Optional[int]:
        """0-based rank among waiting sessions (lower score = longer wait = earlier)."""
        try:
            rank = await self._client.zrank(keys.QUEUE_PENDING, session_id)
            if rank is None:
                return None
            return int(rank)
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisReadException(f"queue_position failed for session_id={session_id}") from exc

    async def queue_pop_oldest(self) -> Optional[str]:
        """Remove and return the longest-waiting session_id, or None if empty."""
        try:
            # Upstash: zpopmin returns list of [member, score] pairs or empty
            result = await self._client.zpopmin(keys.QUEUE_PENDING, count=1)
            if not result:
                return None
            first = result[0]
            if isinstance(first, (list, tuple)):
                session_id = str(first[0])
            else:
                session_id = str(first)
            logger.info("Handoff queue pop_oldest", extra={"session_id": session_id})
            return session_id
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisWriteException("queue_pop_oldest failed") from exc

    async def queue_length(self) -> int:
        try:
            return int(await self._client.zcard(keys.QUEUE_PENDING) or 0)
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisReadException("queue_length failed") from exc

    async def queue_list(self) -> list[dict]:
        """Full queue ordered by wait time (oldest first)."""
        try:
            raw = await self._client.zrange(keys.QUEUE_PENDING, 0, -1, withscores=True)
            entries: list[dict] = []
            if not raw:
                return entries
            # withscores may return flat [m,s,m,s] or [[m,s],...]
            pairs: list[tuple[str, float]] = []
            if raw and isinstance(raw[0], (list, tuple)):
                for item in raw:
                    pairs.append((str(item[0]), float(item[1])))
            else:
                for i in range(0, len(raw), 2):
                    pairs.append((str(raw[i]), float(raw[i + 1])))
            now = time.time()
            for idx, (session_id, score) in enumerate(pairs):
                entries.append({
                    "session_id": session_id,
                    "requested_at": score,
                    "wait_seconds": max(0, int(now - score)),
                    "queue_position": idx,
                })
            return entries
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisReadException("queue_list failed") from exc

    # ------------------------------------------------------------------
    # Agent availability
    # ------------------------------------------------------------------

    async def agent_set_online(self, agent_id: str) -> bool:
        try:
            await self._client.sadd(keys.AGENTS_ONLINE, agent_id)
            logger.info("Agent online", extra={"agent_id": agent_id})
            return True
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisWriteException(f"agent_set_online failed for agent_id={agent_id}") from exc

    async def agent_set_offline(self, agent_id: str) -> bool:
        try:
            await self._client.srem(keys.AGENTS_ONLINE, agent_id)
            logger.info("Agent offline", extra={"agent_id": agent_id})
            return True
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisWriteException(f"agent_set_offline failed for agent_id={agent_id}") from exc

    async def agent_list_online(self) -> list[str]:
        try:
            members = await self._client.smembers(keys.AGENTS_ONLINE)
            return [str(m) for m in (members or [])]
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisReadException("agent_list_online failed") from exc

    async def agent_set_status(
        self,
        agent_id: str,
        *,
        status: str,
        current_session_id: Optional[str] = None,
        last_freed_at: Optional[float] = None,
    ) -> bool:
        key = keys.AGENT_STATUS.format(agent_id=agent_id)
        try:
            payload = {
                "status": status,
                "last_freed_at": last_freed_at if last_freed_at is not None else time.time(),
                "current_session_id": current_session_id,
            }
            await self._client.set(key, json.dumps(payload))
            logger.info(
                "Agent status update",
                extra={"agent_id": agent_id, "status": status, "session_id": current_session_id},
            )
            return True
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisWriteException(f"agent_set_status failed for agent_id={agent_id}") from exc

    async def agent_get_status(self, agent_id: str) -> Optional[dict]:
        key = keys.AGENT_STATUS.format(agent_id=agent_id)
        try:
            raw = await self._client.get(key)
            if raw is None:
                return None
            data = json.loads(raw) if isinstance(raw, str) else raw
            return data if isinstance(data, dict) else None
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisReadException(f"agent_get_status failed for agent_id={agent_id}") from exc

    async def agent_list_statuses(self) -> list[dict]:
        try:
            online = await self.agent_list_online()
            results: list[dict] = []
            max_n = keys.AGENT_MAX_ACTIVE_SESSIONS
            for agent_id in online:
                status = await self.agent_get_status(agent_id) or {}
                active_count = await self.get_agent_active_count(agent_id)
                active_sessions = await self.get_agent_active_sessions(agent_id)
                results.append({
                    "agent_id": agent_id,
                    "last_freed_at": status.get("last_freed_at"),
                    "current_session_id": active_sessions[0] if active_sessions else status.get("current_session_id"),
                    "active_count": active_count,
                    "active_sessions": active_sessions,
                    "max_active": max_n,
                    "status": "available" if active_count < max_n else "full",
                })
            return results
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisReadException("agent_list_statuses failed") from exc

    # ------------------------------------------------------------------
    # Agent capacity (max 5 concurrent sessions)
    # ------------------------------------------------------------------

    async def get_agent_active_count(self, agent_id: str) -> int:
        key = keys.AGENT_ACTIVE_SESSIONS.format(agent_id=agent_id)
        try:
            return int(await self._client.scard(key) or 0)
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisReadException(f"get_agent_active_count failed for agent_id={agent_id}") from exc

    async def get_agent_active_sessions(self, agent_id: str) -> list[str]:
        key = keys.AGENT_ACTIVE_SESSIONS.format(agent_id=agent_id)
        try:
            members = await self._client.smembers(key)
            return [str(m) for m in (members or [])]
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisReadException(
                f"get_agent_active_sessions failed for agent_id={agent_id}"
            ) from exc

    async def add_agent_active_session(self, agent_id: str, session_id: str) -> bool:
        """Add session to agent's active set if under capacity. Returns False if at max."""
        key = keys.AGENT_ACTIVE_SESSIONS.format(agent_id=agent_id)
        try:
            count = await self.get_agent_active_count(agent_id)
            if count >= keys.AGENT_MAX_ACTIVE_SESSIONS:
                # Already a member counts as success (idempotent)
                if await self.validate_agent_session(agent_id, session_id):
                    return True
                return False
            await self._client.sadd(key, session_id)
            active_count = await self.get_agent_active_count(agent_id)
            await self.agent_set_status(
                agent_id,
                status="available" if active_count < keys.AGENT_MAX_ACTIVE_SESSIONS else "full",
                current_session_id=session_id,
                last_freed_at=time.time(),
            )
            # Refresh status with active_count mirror
            status_key = keys.AGENT_STATUS.format(agent_id=agent_id)
            raw = await self._client.get(status_key)
            payload = json.loads(raw) if isinstance(raw, str) and raw else {}
            if not isinstance(payload, dict):
                payload = {}
            payload["active_count"] = active_count
            payload["status"] = (
                "available" if active_count < keys.AGENT_MAX_ACTIVE_SESSIONS else "full"
            )
            payload["current_session_id"] = session_id
            await self._client.set(status_key, json.dumps(payload))
            logger.info(
                "Agent active session added",
                extra={"agent_id": agent_id, "session_id": session_id, "active_count": active_count},
            )
            return True
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisWriteException(
                f"add_agent_active_session failed for agent_id={agent_id}"
            ) from exc

    async def remove_agent_active_session(self, agent_id: str, session_id: str) -> bool:
        key = keys.AGENT_ACTIVE_SESSIONS.format(agent_id=agent_id)
        try:
            await self._client.srem(key, session_id)
            active_count = await self.get_agent_active_count(agent_id)
            remaining = await self.get_agent_active_sessions(agent_id)
            status_key = keys.AGENT_STATUS.format(agent_id=agent_id)
            raw = await self._client.get(status_key)
            payload = json.loads(raw) if isinstance(raw, str) and raw else {}
            if not isinstance(payload, dict):
                payload = {}
            payload["active_count"] = active_count
            payload["status"] = (
                "available" if active_count < keys.AGENT_MAX_ACTIVE_SESSIONS else "full"
            )
            payload["current_session_id"] = remaining[0] if remaining else None
            payload["last_freed_at"] = time.time()
            await self._client.set(status_key, json.dumps(payload))
            logger.info(
                "Agent active session removed",
                extra={"agent_id": agent_id, "session_id": session_id, "active_count": active_count},
            )
            return True
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisWriteException(
                f"remove_agent_active_session failed for agent_id={agent_id}"
            ) from exc

    async def get_available_agents(self) -> list[str]:
        """Online agents with active_sessions size strictly less than max."""
        try:
            online = await self.agent_list_online()
            available: list[str] = []
            for agent_id in online:
                if await self.get_agent_active_count(agent_id) < keys.AGENT_MAX_ACTIVE_SESSIONS:
                    available.append(agent_id)
            return available
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisReadException("get_available_agents failed") from exc

    async def validate_agent_session(self, agent_id: str, session_id: str) -> bool:
        key = keys.AGENT_ACTIVE_SESSIONS.format(agent_id=agent_id)
        try:
            return bool(await self._client.sismember(key, session_id))
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisReadException(
                f"validate_agent_session failed for agent_id={agent_id}"
            ) from exc

    async def queue_score(self, session_id: str) -> Optional[float]:
        """Original queue timestamp score for a pending session, if present."""
        try:
            score = await self._client.zscore(keys.QUEUE_PENDING, session_id)
            return float(score) if score is not None else None
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisReadException(f"queue_score failed for session_id={session_id}") from exc

    # ------------------------------------------------------------------
    # Distributed lock — serializes queue routing (auto-route vs take-next race)
    # ------------------------------------------------------------------

    async def acquire_routing_lock(
        self,
        *,
        wait_seconds: float = QUEUE_ROUTING_LOCK_WAIT_SECONDS,
        ttl_seconds: int = QUEUE_ROUTING_LOCK_TTL_SECONDS,
    ) -> Optional[str]:
        """Blocks up to wait_seconds polling for the lock. Returns a token to
        pass to release_routing_lock on success, or None if not acquired."""
        token = str(uuid.uuid4())
        deadline = time.time() + wait_seconds
        while True:
            try:
                acquired = await self._client.set(
                    LOCK_QUEUE_ROUTING, token, nx=True, ex=ttl_seconds
                )
            except Exception as exc:
                log_exception(logger, exc)
                raise RedisWriteException("acquire_routing_lock failed") from exc
            if acquired:
                return token
            if time.time() >= deadline:
                return None
            await asyncio.sleep(QUEUE_ROUTING_LOCK_POLL_INTERVAL_SECONDS)

    async def release_routing_lock(self, token: str) -> bool:
        try:
            result = await self._client.eval(
                LOCK_RELEASE_LUA_SCRIPT, keys=[LOCK_QUEUE_ROUTING], args=[token]
            )
            return int(result) == 1
        except Exception as exc:
            log_exception(logger, exc)
            # Non-fatal — the lock's own TTL will expire it either way.
            return False

    # ------------------------------------------------------------------
    # Queue/state consistency repair (Issue 12 / 16 — ghost queue entries)
    # ------------------------------------------------------------------

    async def repair_queue_consistency(self, *, active_session_ids: set[str]) -> int:
        """Removes any session from the pending queue whose DB state is no
        longer QUEUED (e.g. HUMAN_ACTIVE/RESOLVED elsewhere). active_session_ids
        is the caller-supplied set of session_ids that ARE legitimately
        HUMAN_ACTIVE right now (so they can be pruned from the queue too, in
        case a crash mid-transition left them in both places). Returns the
        number of ghost entries removed."""
        try:
            queued_ids = {e["session_id"] for e in await self.queue_list()}
        except RedisReadException:
            return 0
        ghosts = queued_ids & active_session_ids
        for session_id in ghosts:
            try:
                await self.queue_remove(session_id)
            except RedisWriteException:
                continue
        if ghosts:
            logger.warning(
                "Removed ghost queue entries (session active elsewhere)",
                extra={"count": len(ghosts), "session_ids": sorted(ghosts)},
            )
        return len(ghosts)

    # ------------------------------------------------------------------
    # Per-conversation Redis message log (session-scoped isolation)
    # ------------------------------------------------------------------

    async def store_message(
        self,
        session_id: str,
        role: str,
        content: str,
        agent_id: Optional[str] = None,
    ) -> bool:
        key = keys.CONVERSATION_MESSAGES.format(session_id=session_id)
        try:
            payload = {
                "role": role,
                "content": content,
                "agent_id": agent_id,
                "ts": time.time(),
            }
            await self._client.rpush(key, json.dumps(payload))
            await self._client.expire(key, settings.conversation_history_ttl_seconds)
            return True
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisWriteException(f"store_message failed for session_id={session_id}") from exc

    async def get_messages(self, session_id: str) -> list[dict]:
        key = keys.CONVERSATION_MESSAGES.format(session_id=session_id)
        try:
            raw_list = await self._client.lrange(key, 0, -1) or []
            out: list[dict] = []
            for raw in raw_list:
                try:
                    data = json.loads(raw) if isinstance(raw, str) else raw
                    if isinstance(data, dict):
                        out.append(data)
                except (json.JSONDecodeError, TypeError):
                    continue
            return out
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisReadException(f"get_messages failed for session_id={session_id}") from exc

    # ------------------------------------------------------------------
    # Conversation handoff state + fail counters
    # ------------------------------------------------------------------

    async def get_session_state(self, session_id: str) -> Optional[str]:
        key = keys.SESSION_STATE.format(session_id=session_id)
        try:
            value = await self._client.get(key)
            return str(value) if value is not None else None
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisReadException(f"get_session_state failed for session_id={session_id}") from exc

    async def set_session_state(
        self,
        session_id: str,
        state: str,
        *,
        reason: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> bool:
        key = keys.SESSION_STATE.format(session_id=session_id)
        try:
            payload = {
                "state": state,
                "reason": reason,
                "agent_id": agent_id,
                "updated_at": time.time(),
            }
            await self._client.set(key, json.dumps(payload), ex=settings.conversation_history_ttl_seconds)
            # Mirror state into session context hash for existing readers
            await self.store_session_context(
                session_id,
                {
                    "handoff_state": state,
                    "handoff_reason": reason,
                    "assigned_agent_id": agent_id,
                },
            )
            logger.info(
                "Session state transition",
                extra={"session_id": session_id, "state": state, "reason": reason, "agent_id": agent_id},
            )
            return True
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisWriteException(f"set_session_state failed for session_id={session_id}") from exc

    async def get_session_state_payload(self, session_id: str) -> Optional[dict]:
        key = keys.SESSION_STATE.format(session_id=session_id)
        try:
            raw = await self._client.get(key)
            if raw is None:
                return None
            data = json.loads(raw) if isinstance(raw, str) else raw
            return data if isinstance(data, dict) else None
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisReadException(
                f"get_session_state_payload failed for session_id={session_id}"
            ) from exc

    async def increment_fail_count(self, session_id: str, field: str) -> int:
        """Increment consecutive_fail_count or consecutive_not_found_count. Returns new value."""
        key = keys.SESSION_FAIL_COUNT.format(session_id=session_id)
        try:
            value = await self._client.hincrby(key, field, 1)
            await self._client.expire(key, settings.conversation_history_ttl_seconds)
            return int(value)
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisWriteException(
                f"increment_fail_count failed for session_id={session_id} field={field}"
            ) from exc

    async def reset_fail_counts(self, session_id: str) -> bool:
        key = keys.SESSION_FAIL_COUNT.format(session_id=session_id)
        try:
            await self._client.hset(
                key,
                values={
                    "consecutive_fail_count": "0",
                    "consecutive_not_found_count": "0",
                },
            )
            await self._client.expire(key, settings.conversation_history_ttl_seconds)
            return True
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisWriteException(f"reset_fail_counts failed for session_id={session_id}") from exc

    async def set_fail_counts(
        self,
        session_id: str,
        *,
        consecutive_fail_count: int,
        consecutive_not_found_count: int,
    ) -> bool:
        key = keys.SESSION_FAIL_COUNT.format(session_id=session_id)
        try:
            await self._client.hset(
                key,
                values={
                    "consecutive_fail_count": str(int(consecutive_fail_count)),
                    "consecutive_not_found_count": str(int(consecutive_not_found_count)),
                },
            )
            await self._client.expire(key, settings.conversation_history_ttl_seconds)
            return True
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisWriteException(f"set_fail_counts failed for session_id={session_id}") from exc

    async def get_fail_counts(self, session_id: str) -> dict[str, int]:
        key = keys.SESSION_FAIL_COUNT.format(session_id=session_id)
        try:
            data = await self._client.hgetall(key) or {}
            return {
                "consecutive_fail_count": int(data.get("consecutive_fail_count") or 0),
                "consecutive_not_found_count": int(data.get("consecutive_not_found_count") or 0),
            }
        except Exception as exc:
            log_exception(logger, exc)
            raise RedisReadException(f"get_fail_counts failed for session_id={session_id}") from exc
