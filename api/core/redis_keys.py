"""Centralized Redis key patterns.

Backward-compatible re-exports from api.core.constants — import keys from here
or directly from constants; do not hardcode Redis key strings elsewhere.
"""

from api.core.constants import (
    ANALYTICS_DOC_TYPE_QUERIES,
    ANALYTICS_EMERGENCY,
    ANALYTICS_FAITHFULNESS,
    ANALYTICS_QUERY_TYPES,
    ANALYTICS_TOP_DRUGS,
    ANSWER_CACHE,
    BM25_BUILT_AT,
    BM25_INDEX,
    CHUNKS_INDEX,
    CHUNK_META,
    CHUNK_TEXT,
    DOC_INDEX,
    DOC_META,
    DRUG_INDEX,
    DRUG_INFO,
    EMB_CACHE,
    INTERACTION,
    LAB_RANGE,
    LAB_RANGE_INDEX,
    RATE_LIMIT,
    SESSION_CONTEXT,
    SESSION_HISTORY,
)

# ---------------------------------------------------------------------------
# Human handoff / agent queue (Day 2)
# ---------------------------------------------------------------------------

QUEUE_PENDING = "queue:pending_handoffs"
AGENTS_ONLINE = "agents:online"
AGENT_STATUS = "agent:{agent_id}:status"
AGENT_ACTIVE_SESSIONS = "agent:{agent_id}:active_sessions"
SESSION_STATE = "session:{session_id}:state"
SESSION_FAIL_COUNT = "session:{session_id}:fail_count"
CONVERSATION_MESSAGES = "conversation:{session_id}:messages"

# Max simultaneous patient chats per agent (Rule 1)
AGENT_MAX_ACTIVE_SESSIONS = 5

__all__ = [
    "DOC_META",
    "DOC_INDEX",
    "CHUNK_TEXT",
    "CHUNK_META",
    "CHUNKS_INDEX",
    "SESSION_HISTORY",
    "SESSION_CONTEXT",
    "EMB_CACHE",
    "ANSWER_CACHE",
    "BM25_INDEX",
    "BM25_BUILT_AT",
    "DRUG_INFO",
    "DRUG_INDEX",
    "INTERACTION",
    "LAB_RANGE",
    "LAB_RANGE_INDEX",
    "ANALYTICS_QUERY_TYPES",
    "ANALYTICS_EMERGENCY",
    "ANALYTICS_TOP_DRUGS",
    "ANALYTICS_DOC_TYPE_QUERIES",
    "ANALYTICS_FAITHFULNESS",
    "RATE_LIMIT",
    "QUEUE_PENDING",
    "AGENTS_ONLINE",
    "AGENT_STATUS",
    "AGENT_ACTIVE_SESSIONS",
    "SESSION_STATE",
    "SESSION_FAIL_COUNT",
    "CONVERSATION_MESSAGES",
    "AGENT_MAX_ACTIVE_SESSIONS",
]
