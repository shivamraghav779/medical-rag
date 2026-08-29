from __future__ import annotations

import os
from functools import lru_cache
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from api.core.constants import CLINICAL_DISCLAIMER, EMERGENCY_TERMS


def _is_serverless() -> bool:
    return bool(os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))


# Query params Neon (and most managed-Postgres providers) put on their
# connection strings for libpq/psycopg clients — asyncpg's connect() doesn't
# accept them as kwargs and raises TypeError if they're left in the URL.
# SSL is instead requested explicitly via connect_args (see database.py).
_LIBPQ_ONLY_QUERY_PARAMS = {"sslmode", "channel_binding"}


def _normalize_postgres_url(url: str) -> str:
    """Rewrite a plain postgres:// / postgresql:// URL (as managed providers
    hand out) into the asyncpg driver URL SQLAlchemy's async engine needs,
    stripping libpq-only query params asyncpg doesn't understand."""
    split = urlsplit(url)
    scheme = split.scheme
    if scheme in ("postgres", "postgresql"):
        scheme = "postgresql+asyncpg"
    query = parse_qs(split.query)
    for param in _LIBPQ_ONLY_QUERY_PARAMS:
        query.pop(param, None)
    return urlunsplit((scheme, split.netloc, split.path, urlencode(query, doseq=True), split.fragment))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    groq_api_key: str
    pinecone_api_key: str
    pinecone_index: str = "rag-platform"
    cohere_api_key: str

    upstash_redis_rest_url: str
    upstash_redis_rest_token: str

    groq_model: str = "openai/gpt-oss-120b"
    embedding_dim: int = 1024

    chunk_size: int = 300
    chunk_overlap: int = 50
    min_chunk_size: int = 50

    dense_top_k: int = 10
    sparse_top_k: int = 10
    rerank_top_k: int = 8

    faithfulness_pass_threshold: float = 0.8
    faithfulness_warn_threshold: float = 0.5

    embedding_cache_ttl_seconds: int = 86400
    answer_cache_ttl_seconds: int = 3600
    conversation_history_max: int = 10
    conversation_history_ttl_seconds: int = 86400

    rate_limit_max_requests: int = 20
    rate_limit_window_seconds: int = 60

    pinecone_upsert_batch_size: int = 100

    drug_interaction_cache_ttl_seconds: int = 604800  # 7 days

    # LangSmith observability (optional)
    langsmith_api_key: str = ""
    langchain_project: str = "rag-platform-clinical"
    langsmith_tracing_enabled: bool = True

    emergency_terms: list[str] = Field(default_factory=lambda: list(EMERGENCY_TERMS))

    clinical_disclaimer: str = CLINICAL_DISCLAIMER

    # JWT authentication
    jwt_secret_key: str = "change-me-in-production-use-long-random-string"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7  # 7 days

    # Persistent storage (SQLite default; use postgresql+asyncpg:// for production)
    # On Vercel/Lambda the FS is read-only except /tmp — defaults flip there.
    database_url: str = "sqlite+aiosqlite:///./data/clinical_rag.db"
    database_path: str = "./data/clinical_rag.db"

    # Conversation context: last N user+assistant turn pairs passed verbatim to LLM
    recent_turns_for_context: int = 5
    # Trigger rolling summary when total messages exceed this count
    summary_trigger_messages: int = 10

    # Grace period (seconds) before a disconnected agent's sessions are
    # requeued — must survive HMR/Strict Mode reconnects. Minimum 5s.
    agent_disconnect_grace_seconds: int = 5

    @model_validator(mode="after")
    def _normalize_database_url(self) -> Settings:
        # A real DATABASE_URL (e.g. Neon/managed Postgres) always wins — only
        # fall back to serverless /tmp SQLite when nothing else was configured.
        # (Previously this checked database_path instead of database_url, so
        # setting DATABASE_URL to Postgres without also changing database_path
        # got silently overwritten back to ephemeral /tmp SQLite on Vercel.)
        if self.database_url.startswith(("postgres://", "postgresql://")):
            self.database_url = _normalize_postgres_url(self.database_url)
        elif _is_serverless() and self.database_url.startswith("sqlite") and self.database_path.startswith("./"):
            self.database_path = "/tmp/clinical_rag.db"
            self.database_url = "sqlite+aiosqlite:////tmp/clinical_rag.db"
        return self

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql+asyncpg://")


@lru_cache()
def get_settings() -> Settings:
    return Settings()


def __getattr__(name: str) -> Any:
    """Lazy ``settings`` so importing this module does not require env vars
    until something actually reads configuration (avoids hard-crash on cold
    start before env is wired; still fails loudly on first use)."""
    if name == "settings":
        return get_settings()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
