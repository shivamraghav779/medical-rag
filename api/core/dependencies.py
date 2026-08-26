"""FastAPI dependency injection for singleton services.

Services are constructed once during app lifespan and stored on
``app.state``. Route handlers (and the Orchestrator) receive them via
``Depends(...)`` — never by instantiating clients inside a request.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Annotated, AsyncIterator

import cohere
from fastapi import Depends, FastAPI, Request
from groq import AsyncGroq
from pinecone import Pinecone
from upstash_redis.asyncio import Redis

from api.agents.orchestrator import Orchestrator
from api.core.config import Settings, get_settings
from api.core.document_parser import DocumentParser
from api.core.logger import get_logger, log_exception
from api.core.database import AsyncSessionLocal, init_db
from api.services.auth_service import AuthService
from api.services.conversation_service import ConversationService
from api.services.drug_interaction_service import DrugInteractionService
from api.services.handoff_service import HandoffService
from api.services.token_usage_service import TokenUsageService
from api.services.embedding_service import EmbeddingService
from api.services.llm_service import LLMService
from api.services.redis_service import RedisService
from api.services.retrieval_service import RetrievalService
from api.services.web_search_service import WebSearchService

QUEUE_REPAIR_INTERVAL_SECONDS = 60

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logger.info("Application lifespan startup — initializing shared clients")

    redis_client = Redis(
        url=settings.upstash_redis_rest_url,
        token=settings.upstash_redis_rest_token,
    )
    cohere_client = cohere.AsyncClient(settings.cohere_api_key)
    groq_client = AsyncGroq(api_key=settings.groq_api_key)
    pinecone_index = Pinecone(api_key=settings.pinecone_api_key).Index(settings.pinecone_index)
    document_parser = DocumentParser()

    redis_service = RedisService(redis_client)
    embedding_service = EmbeddingService(cohere_client, redis_service)
    retrieval_service = RetrievalService(
        pinecone_index=pinecone_index,
        cohere_client=cohere_client,
        redis_service=redis_service,
        embedding_service=embedding_service,
    )
    llm_service = LLMService(groq_client, settings)
    drug_service = DrugInteractionService(redis_service)
    web_search_service = WebSearchService()
    auth_service = AuthService()
    conversation_service = ConversationService()
    token_usage_service = TokenUsageService()
    orchestrator = Orchestrator(
        redis_service=redis_service,
        embedding_service=embedding_service,
        retrieval_service=retrieval_service,
        llm_service=llm_service,
        drug_service=drug_service,
        web_search_service=web_search_service,
        settings=settings,
    )

    app.state.settings = settings
    app.state.redis_client = redis_client
    app.state.cohere_client = cohere_client
    app.state.groq_client = groq_client
    app.state.pinecone_index = pinecone_index
    app.state.document_parser = document_parser
    app.state.redis_service = redis_service
    app.state.embedding_service = embedding_service
    app.state.retrieval_service = retrieval_service
    app.state.llm_service = llm_service
    app.state.drug_service = drug_service
    app.state.web_search_service = web_search_service
    app.state.auth_service = auth_service
    app.state.conversation_service = conversation_service
    app.state.token_usage_service = token_usage_service
    app.state.orchestrator = orchestrator

    await init_db()

    handoff_service = HandoffService(redis_service)

    async def _repair_queue_loop() -> None:
        """Startup + periodic sweep removing sessions from the pending queue
        whose DB state is actually HUMAN_ACTIVE (split-brain repair, Issue
        12/16). Runs forever until cancelled at shutdown."""
        while True:
            try:
                async with AsyncSessionLocal() as db:
                    removed = await handoff_service.repair_queue_ghosts(db)
                if removed:
                    logger.warning(
                        "Queue repair removed ghost entries", extra={"count": removed}
                    )
            except Exception as exc:
                log_exception(logger, exc)
            await asyncio.sleep(QUEUE_REPAIR_INTERVAL_SECONDS)

    repair_task = asyncio.create_task(_repair_queue_loop())

    logger.info("Application lifespan startup complete")
    try:
        yield
    finally:
        repair_task.cancel()
        logger.info("Application lifespan shutdown")


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_redis_service(request: Request) -> RedisService:
    return request.app.state.redis_service


def get_embedding_service(request: Request) -> EmbeddingService:
    return request.app.state.embedding_service


def get_retrieval_service(request: Request) -> RetrievalService:
    return request.app.state.retrieval_service


def get_llm_service(request: Request) -> LLMService:
    return request.app.state.llm_service


def get_drug_service(request: Request) -> DrugInteractionService:
    return request.app.state.drug_service


def get_web_search_service(request: Request) -> WebSearchService:
    return request.app.state.web_search_service


def get_orchestrator(request: Request) -> Orchestrator:
    return request.app.state.orchestrator


def get_document_parser(request: Request) -> DocumentParser:
    return request.app.state.document_parser


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
RedisServiceDep = Annotated[RedisService, Depends(get_redis_service)]
EmbeddingServiceDep = Annotated[EmbeddingService, Depends(get_embedding_service)]
RetrievalServiceDep = Annotated[RetrievalService, Depends(get_retrieval_service)]
LLMServiceDep = Annotated[LLMService, Depends(get_llm_service)]
DrugServiceDep = Annotated[DrugInteractionService, Depends(get_drug_service)]
WebSearchServiceDep = Annotated[WebSearchService, Depends(get_web_search_service)]
OrchestratorDep = Annotated[Orchestrator, Depends(get_orchestrator)]
DocumentParserDep = Annotated[DocumentParser, Depends(get_document_parser)]
