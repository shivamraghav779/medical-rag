from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings

from api.core.constants import CLINICAL_DISCLAIMER, EMERGENCY_TERMS


class Settings(BaseSettings):
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
    database_url: str = "sqlite+aiosqlite:///./data/clinical_rag.db"
    database_path: str = "./data/clinical_rag.db"

    # Conversation context: last N user+assistant turn pairs passed verbatim to LLM
    recent_turns_for_context: int = 5
    # Trigger rolling summary when total messages exceed this count
    summary_trigger_messages: int = 10

    # Grace period (seconds) before a disconnected agent's sessions are
    # requeued — must survive HMR/Strict Mode reconnects. Minimum 5s.
    agent_disconnect_grace_seconds: int = 5

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings():
    return Settings()


settings = get_settings()
