"""FastAPI application entrypoint."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from api.core.constants import (
    APP_DESCRIPTION,
    APP_ROOT_MESSAGE,
    APP_TITLE,
    APP_VERSION,
    CORS_ALLOW_CREDENTIALS,
    CORS_ALLOW_HEADERS,
    CORS_ALLOW_METHODS,
    CORS_ALLOW_ORIGINS,
    EMBED_MODEL,
    RERANK_MODEL,
)
from api.core.dependencies import lifespan
from api.core.exceptions import register_exception_handlers
from api.core.logger import get_logger
from api.middleware.logging_middleware import LoggingMiddleware
from api.middleware.request_id import RequestIDMiddleware
from api.routers import admin, auth, chat, chat_tools, conversations, documents, faq, handoff, upload, websocket

logger = get_logger(__name__)

app = FastAPI(
    title=APP_TITLE,
    description=APP_DESCRIPTION,
    version=APP_VERSION,
    lifespan=lifespan,
)

register_exception_handlers(app)

# Middleware order: last added runs first on the request path.
# RequestID must wrap Logging so request_id is available when logging starts.
app.add_middleware(LoggingMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS,
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=CORS_ALLOW_METHODS,
    allow_headers=CORS_ALLOW_HEADERS,
)

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(conversations.router)
app.include_router(upload.router)
app.include_router(chat_tools.router)
app.include_router(chat.router)
app.include_router(documents.router)
app.include_router(faq.router)
app.include_router(handoff.router)
app.include_router(websocket.router)

_widget_dist = Path(__file__).resolve().parents[1] / "widget" / "dist"
if _widget_dist.is_dir():
    app.mount("/widget", StaticFiles(directory=str(_widget_dist)), name="widget")


@app.get("/health")
async def health():
    from api.core.config import settings

    redis_status = "ok"
    pinecone_status = "ok"

    try:
        redis_service = app.state.redis_service
        pong = await redis_service.ping()
        redis_status = "ok" if pong == "PONG" else f"unexpected response: {pong}"
    except Exception as e:
        redis_status = f"error: {e}"

    try:
        import asyncio

        index = app.state.pinecone_index
        await asyncio.to_thread(index.describe_index_stats)
    except Exception as e:
        pinecone_status = f"error: {e}"

    overall_ok = redis_status == "ok" and pinecone_status == "ok"
    if not overall_ok:
        logger.critical(
            "Health check degraded",
            extra={"redis": redis_status, "pinecone": pinecone_status},
        )

    return {
        "status": "ok" if overall_ok else "degraded",
        "redis": redis_status,
        "pinecone": pinecone_status,
        "model": settings.groq_model,
        "embedding_model": EMBED_MODEL,
        "rerank_model": RERANK_MODEL,
    }


@app.get("/")
async def root():
    return {
        "message": APP_ROOT_MESSAGE,
        "docs": "/docs",
        "health": "/health",
    }
