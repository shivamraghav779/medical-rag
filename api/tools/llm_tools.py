import json
import logging
from typing import AsyncGenerator, Optional

from groq import AsyncGroq

from api.core.config import settings
from api.core.constants import (
    ASCII_CLOSE_BRACKET,
    ASCII_OPEN_BRACKET,
    CJK_CLOSE_BRACKET,
    CJK_OPEN_BRACKET,
    EXPANDED_QUERY_COUNT,
    FAITHFULNESS_JUDGE_FAILURE_VIOLATION,
)
from api.core.prompts import get_prompt
from api.models.schemas import FaithfulnessResult, RetrievedChunk

logger = logging.getLogger(__name__)

_client: Optional[AsyncGroq] = None


def get_client() -> AsyncGroq:
    global _client
    if _client is None:
        _client = AsyncGroq(api_key=settings.groq_api_key)
    return _client


def _format_history(conversation_history: list[dict]) -> list[dict]:
    """Trims stored history entries (which carry a timestamp) down to
    the role/content shape the Groq chat API expects."""
    return [
        {"role": entry["role"], "content": entry["content"]}
        for entry in conversation_history
        if entry.get("role") in ("user", "assistant") and entry.get("content")
    ]


async def analyze_query(query: str, conversation_history: list[dict]) -> dict:
    messages = [{"role": "system", "content": get_prompt("legacy", "query_analysis", "system")}]
    messages.extend(_format_history(conversation_history))
    messages.append({"role": "user", "content": f"Analyze this query: {query}"})

    try:
        response = await get_client().chat.completions.create(
            model=settings.groq_model,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0,
        )
        raw = response.choices[0].message.content
        parsed = json.loads(raw)
    except Exception:
        logger.exception("analyze_query failed, falling back to safe defaults")
        return {
            "intent": "unknown",
            "expanded_queries": [query, query, query],
            "requires_retrieval": True,
            "doc_filter": None,
        }

    expanded_queries = parsed.get("expanded_queries") or []
    if not isinstance(expanded_queries, list):
        expanded_queries = [query]
    expanded_queries = [str(q) for q in expanded_queries][:EXPANDED_QUERY_COUNT]
    while len(expanded_queries) < EXPANDED_QUERY_COUNT:
        expanded_queries.append(query)

    doc_filter = parsed.get("doc_filter")
    if doc_filter is not None and not isinstance(doc_filter, list):
        doc_filter = None

    return {
        "intent": str(parsed.get("intent", "unknown")),
        "expanded_queries": expanded_queries,
        "requires_retrieval": bool(parsed.get("requires_retrieval", True)),
        "doc_filter": doc_filter,
    }


def _format_chunks_block(chunks: list[RetrievedChunk]) -> str:
    parts = []
    for chunk in chunks:
        parts.append(
            f"[{chunk.rank}] (doc: {chunk.doc_name}, page {chunk.page_number})\n{chunk.text}"
        )
    return "\n\n".join(parts)


async def generate_answer_stream(
    query: str,
    chunks: list[RetrievedChunk],
    conversation_history: list[dict],
) -> AsyncGenerator[str, None]:
    system_prompt = get_prompt("legacy", "generation", "system").format(
        chunks_block=_format_chunks_block(chunks)
    )

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(_format_history(conversation_history))
    messages.append({"role": "user", "content": query})

    stream = await get_client().chat.completions.create(
        model=settings.groq_model,
        messages=messages,
        stream=True,
        temperature=0,
    )

    async for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta.replace(CJK_OPEN_BRACKET, ASCII_OPEN_BRACKET).replace(
                CJK_CLOSE_BRACKET, ASCII_CLOSE_BRACKET
            )


async def generate_conversational_stream(
    query: str,
    conversation_history: list[dict],
) -> AsyncGenerator[str, None]:
    """Used only when QueryAnalyzerAgent determines requires_retrieval=False."""
    messages = [{"role": "system", "content": get_prompt("legacy", "conversational", "system")}]
    messages.extend(_format_history(conversation_history))
    messages.append({"role": "user", "content": query})

    stream = await get_client().chat.completions.create(
        model=settings.groq_model,
        messages=messages,
        stream=True,
        temperature=0.7,
    )

    async for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta


async def judge_faithfulness(
    query: str,
    answer: str,
    chunks: list[RetrievedChunk],
) -> FaithfulnessResult:
    chunks_block = _format_chunks_block(chunks)
    user_content = (
        f"QUERY:\n{query}\n\nANSWER:\n{answer}\n\nSOURCE CHUNKS:\n{chunks_block}"
    )

    try:
        response = await get_client().chat.completions.create(
            model=settings.groq_model,
            messages=[
                {"role": "system", "content": get_prompt("legacy", "faithfulness", "system")},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        parsed = json.loads(response.choices[0].message.content)
        score = float(parsed.get("score", 0.0))
        score = max(0.0, min(1.0, score))
        violations = parsed.get("violations") or []
        if not isinstance(violations, list):
            violations = [str(violations)]
        violations = [str(v) for v in violations]
    except Exception:
        logger.exception("judge_faithfulness failed, treating as FAIL with no measurable score")
        score = 0.0
        violations = [FAITHFULNESS_JUDGE_FAILURE_VIOLATION]

    if score >= settings.faithfulness_pass_threshold:
        verdict = "PASS"
    elif score >= settings.faithfulness_warn_threshold:
        verdict = "WARN"
    else:
        verdict = "FAIL"

    return FaithfulnessResult(score=score, verdict=verdict, violations=violations)
