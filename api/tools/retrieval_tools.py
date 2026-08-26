import asyncio
import logging
import re
from typing import Optional

import cohere
from pinecone import Pinecone
from rank_bm25 import BM25Okapi

from api.core.config import settings
from api.models.schemas import RetrievedChunk
from api.tools import redis_tools

logger = logging.getLogger(__name__)

from api.core.constants import RERANK_MODEL, RRF_K
_pinecone_client: Optional[Pinecone] = None
_pinecone_index = None
_cohere_client: Optional[cohere.AsyncClient] = None


def get_pinecone_index():
    global _pinecone_client, _pinecone_index
    if _pinecone_index is None:
        _pinecone_client = Pinecone(api_key=settings.pinecone_api_key)
        _pinecone_index = _pinecone_client.Index(settings.pinecone_index)
    return _pinecone_index


def get_cohere_client() -> cohere.AsyncClient:
    global _cohere_client
    if _cohere_client is None:
        _cohere_client = cohere.AsyncClient(settings.cohere_api_key)
    return _cohere_client


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


# ---------------------------------------------------------------------------
# Dense retrieval (Pinecone)
# ---------------------------------------------------------------------------

async def dense_search(
    query_embedding: list[float],
    top_k: int,
    filter_doc_names: Optional[list[str]] = None,
) -> list[RetrievedChunk]:
    index = get_pinecone_index()
    query_filter = {"doc_name": {"$in": filter_doc_names}} if filter_doc_names else None

    try:
        response = await asyncio.to_thread(
            index.query,
            vector=query_embedding,
            top_k=top_k,
            filter=query_filter,
            include_metadata=True,
        )
    except Exception:
        logger.exception("dense_search failed")
        return []

    # Do not trust the API's return order for rank — sort explicitly. Observed
    # live that Pinecone serverless can return matches out of score order
    # shortly after upsert (scores themselves were correct, ordering wasn't).
    sorted_matches = sorted(response.matches, key=lambda m: m.score, reverse=True)

    results = []
    for rank, match in enumerate(sorted_matches, start=1):
        metadata = match.metadata or {}
        results.append(RetrievedChunk(
            chunk_id=match.id,
            doc_name=metadata.get("doc_name", "unknown"),
            page_number=int(metadata.get("page_number", 1)),
            text=metadata.get("text", ""),
            score=float(match.score),
            rank=rank,
        ))
    return results


# ---------------------------------------------------------------------------
# Sparse retrieval (BM25, backed by Redis chunk registry)
# ---------------------------------------------------------------------------

async def _load_bm25_index() -> Optional[dict]:
    cached = await redis_tools.get_cached_bm25_index()
    if cached is not None:
        logger.debug("BM25 index cache hit")
        return cached

    logger.debug("BM25 index cache miss, rebuilding from Redis chunk registry")
    records = await redis_tools.get_all_chunk_records()
    if not records:
        return None

    chunk_ids = list(records.keys())
    index_data = {
        "chunk_ids": chunk_ids,
        "tokenized_corpus": [_tokenize(records[cid]["text"]) for cid in chunk_ids],
        "records": records,
    }
    await redis_tools.cache_bm25_index(index_data)
    return index_data


async def sparse_search(query: str, top_k: int) -> list[RetrievedChunk]:
    index_data = await _load_bm25_index()
    if not index_data:
        return []

    chunk_ids = index_data["chunk_ids"]
    tokenized_corpus = index_data["tokenized_corpus"]
    records = index_data["records"]

    bm25 = BM25Okapi(tokenized_corpus)
    scores = bm25.get_scores(_tokenize(query))

    ranked = sorted(zip(chunk_ids, scores), key=lambda pair: pair[1], reverse=True)[:top_k]

    results = []
    for rank, (chunk_id, score) in enumerate(ranked, start=1):
        record = records.get(chunk_id)
        if record is None:
            continue
        results.append(RetrievedChunk(
            chunk_id=chunk_id,
            doc_name=record["doc_name"],
            page_number=int(record["page_number"]),
            text=record["text"],
            score=float(score),
            rank=rank,
        ))
    return results


# ---------------------------------------------------------------------------
# Reciprocal Rank Fusion
# ---------------------------------------------------------------------------

def rrf_fusion(
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


# ---------------------------------------------------------------------------
# Reranking (Cohere)
# ---------------------------------------------------------------------------

async def rerank_chunks(query: str, chunks: list[RetrievedChunk], top_k: int) -> list[RetrievedChunk]:
    if not chunks:
        return []

    try:
        response = await get_cohere_client().rerank(
            model=RERANK_MODEL,
            query=query,
            documents=[chunk.text for chunk in chunks],
            top_n=top_k,
        )
    except Exception:
        logger.exception("rerank_chunks failed, falling back to input order")
        return chunks[:top_k]

    results = []
    for rank, result in enumerate(response.results, start=1):
        original = chunks[result.index]
        results.append(original.model_copy(update={
            "score": float(result.relevance_score),
            "rank": rank,
        }))
    return results
