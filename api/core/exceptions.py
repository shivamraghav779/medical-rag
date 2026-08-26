"""Application exception hierarchy and FastAPI global exception handlers.

Every unhandled error returns a uniform JSON body — never a stack trace —
and ExceptionTracker suppresses duplicate ERROR/CRITICAL alerts when the
same exception is logged at multiple layers of the call stack.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from api.core.logger import _origin_frame_info, get_exception_tracker, get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class BaseAppException(Exception):
    """Root of all typed application errors.

    Subclasses set class-level defaults for code / http_status /
    is_retryable / log_level; the constructor may override any of them
    per-instance when a more specific message or status is needed.
    """

    code: str = "INTERNAL_ERROR"
    http_status: int = 500
    is_retryable: bool = False
    log_level: str = "ERROR"

    def __init__(
        self,
        message: str,
        *,
        code: Optional[str] = None,
        http_status: Optional[int] = None,
        is_retryable: Optional[bool] = None,
        log_level: Optional[str] = None,
    ):
        self.message = message
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status
        if is_retryable is not None:
            self.is_retryable = is_retryable
        if log_level is not None:
            self.log_level = log_level
        super().__init__(message)


# ---------------------------------------------------------------------------
# Infrastructure — third-party service failures
# ---------------------------------------------------------------------------

class InfrastructureException(BaseAppException):
    code = "INFRASTRUCTURE_ERROR"
    http_status = 503
    is_retryable = True
    log_level = "ERROR"


class RedisException(InfrastructureException):
    code = "REDIS_ERROR"
    http_status = 503
    is_retryable = True


class RedisConnectionException(RedisException):
    code = "REDIS_CONNECTION_FAILED"


class RedisTimeoutException(RedisException):
    code = "REDIS_TIMEOUT"


class RedisWriteException(RedisException):
    code = "REDIS_WRITE_FAILED"


class RedisReadException(RedisException):
    code = "REDIS_READ_FAILED"


class PineconeException(InfrastructureException):
    code = "PINECONE_ERROR"
    http_status = 503
    is_retryable = True


class PineconeConnectionException(PineconeException):
    code = "PINECONE_CONNECTION_FAILED"
    log_level = "CRITICAL"


class PineconeUpsertException(PineconeException):
    code = "PINECONE_UPSERT_FAILED"


class PineconeQueryException(PineconeException):
    code = "PINECONE_QUERY_FAILED"


class GroqException(InfrastructureException):
    code = "GROQ_ERROR"
    http_status = 502
    is_retryable = True


class GroqRateLimitException(GroqException):
    code = "GROQ_RATE_LIMITED"
    http_status = 429


class GroqTimeoutException(GroqException):
    code = "GROQ_TIMEOUT"


class GroqStreamException(GroqException):
    code = "GROQ_STREAM_FAILED"


class CohereException(InfrastructureException):
    code = "COHERE_ERROR"
    http_status = 502
    is_retryable = True


class CohereEmbedException(CohereException):
    code = "COHERE_EMBED_FAILED"


class CohereRerankException(CohereException):
    code = "COHERE_RERANK_FAILED"


# ---------------------------------------------------------------------------
# Application — business logic failures
# ---------------------------------------------------------------------------

class ApplicationException(BaseAppException):
    code = "APPLICATION_ERROR"
    http_status = 500
    is_retryable = False
    log_level = "ERROR"


class DocumentException(ApplicationException):
    code = "DOCUMENT_ERROR"


class DocumentNotFoundException(DocumentException):
    code = "DOCUMENT_NOT_FOUND"
    http_status = 404


class DocumentParsingException(DocumentException):
    code = "DOCUMENT_PARSING_FAILED"
    http_status = 422


class DocumentTooLargeException(DocumentException):
    code = "DOCUMENT_TOO_LARGE"
    http_status = 413


class DocumentAlreadyExistsException(DocumentException):
    code = "DOCUMENT_ALREADY_EXISTS"
    http_status = 409


class AgentException(ApplicationException):
    code = "AGENT_ERROR"
    http_status = 500


class AgentTimeoutException(AgentException):
    code = "AGENT_TIMEOUT"


class AgentOutputParseException(AgentException):
    code = "AGENT_OUTPUT_PARSE_FAILED"


class OrchestratorException(AgentException):
    code = "ORCHESTRATOR_FAILED"


class QueryException(ApplicationException):
    code = "QUERY_ERROR"
    http_status = 400


class EmptyQueryException(QueryException):
    code = "EMPTY_QUERY"
    http_status = 400


class QueryTooLongException(QueryException):
    code = "QUERY_TOO_LONG"
    http_status = 400


class ValidationException(QueryException):
    code = "VALIDATION_ERROR"
    http_status = 422


class ConversationNotFoundException(ApplicationException):
    code = "CONVERSATION_NOT_FOUND"
    http_status = 404
    log_level = "WARNING"


class MessageNotFoundException(ApplicationException):
    code = "MESSAGE_NOT_FOUND"
    http_status = 404
    log_level = "WARNING"


class HandoffException(ApplicationException):
    code = "HANDOFF_ERROR"
    http_status = 400
    log_level = "WARNING"


class AgentUnavailableException(HandoffException):
    code = "AGENT_UNAVAILABLE"
    http_status = 503
    is_retryable = True


class SessionNotQueuedError(HandoffException):
    code = "SESSION_NOT_QUEUED"
    http_status = 409


class WebSocketConnectionError(HandoffException):
    code = "WEBSOCKET_CONNECTION_ERROR"
    http_status = 400


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------

class SecurityException(BaseAppException):
    code = "SECURITY_ERROR"
    http_status = 403
    is_retryable = False
    log_level = "WARNING"


class RateLimitException(SecurityException):
    code = "RATE_LIMIT_EXCEEDED"
    http_status = 429
    is_retryable = True

    def __init__(self, message: str = "Rate limit exceeded.", *, retry_after_seconds: int = 60, **kwargs):
        super().__init__(message, **kwargs)
        self.retry_after_seconds = retry_after_seconds


class UnauthorizedException(SecurityException):
    code = "UNAUTHORIZED"
    http_status = 401


class ForbiddenException(SecurityException):
    """Authenticated, but the account's role doesn't permit this action.

    Distinct from UnauthorizedException (401 — no/invalid/expired token):
    the frontend treats any 401 as an expired session and force-logs the
    user out (api/client.ts). A logged-in user/patient hitting an
    agent-only endpoint is not the same as their session being invalid,
    and forcing a logout there was a real bug (they were never told why
    they got signed out, and had to log back in as the same account)."""

    code = "FORBIDDEN"
    http_status = 403


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------

def _request_id(request: Request) -> Optional[str]:
    return getattr(request.state, "request_id", None)


def _error_body(
    *,
    code: str,
    message: str,
    is_retryable: bool,
    request_id: Optional[str],
) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "is_retryable": is_retryable,
            "request_id": request_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    }


def _log_if_new(exc: BaseException, *, level: str, request_id: Optional[str], with_traceback: bool) -> None:
    """Log once per ExceptionTracker fingerprint; duplicates become DEBUG."""
    tracker = get_exception_tracker()
    module, function = _origin_frame_info(exc)
    is_duplicate, fingerprint = tracker.check_and_record(
        type(exc).__name__, str(exc), module, function
    )
    extra = {"request_id": request_id} if request_id else {}

    if is_duplicate:
        logger.debug(f"suppressed duplicate: {fingerprint[:8]}", extra=extra)
        return

    level_name = level.upper()
    if with_traceback:
        if level_name == "CRITICAL":
            logger.critical(str(exc), exc_info=exc, extra=extra)
        else:
            logger.error(str(exc), exc_info=exc, extra=extra)
    else:
        if level_name == "WARNING":
            logger.warning(str(exc), extra=extra)
        elif level_name == "CRITICAL":
            logger.critical(str(exc), extra=extra)
        else:
            logger.error(str(exc), extra=extra)


# ---------------------------------------------------------------------------
# Handler registration
# ---------------------------------------------------------------------------

def register_exception_handlers(app: FastAPI) -> None:
    """Attach global handlers for typed app errors, FastAPI/Pydantic errors,
    and a CRITICAL catch-all. Safe to call once from the app factory."""

    @app.exception_handler(BaseAppException)
    async def handle_app_exception(request: Request, exc: BaseAppException) -> JSONResponse:
        request_id = _request_id(request)
        is_5xx = exc.http_status >= 500
        _log_if_new(
            exc,
            level="WARNING" if not is_5xx else exc.log_level,
            request_id=request_id,
            with_traceback=is_5xx,
        )

        headers = {}
        if isinstance(exc, RateLimitException):
            headers["Retry-After"] = str(exc.retry_after_seconds)

        return JSONResponse(
            status_code=exc.http_status,
            content=_error_body(
                code=exc.code,
                message=exc.message,
                is_retryable=exc.is_retryable,
                request_id=request_id,
            ),
            headers=headers or None,
        )

    @app.exception_handler(HTTPException)
    async def handle_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
        request_id = _request_id(request)
        is_5xx = exc.status_code >= 500
        _log_if_new(
            exc,
            level="WARNING" if not is_5xx else "ERROR",
            request_id=request_id,
            with_traceback=is_5xx,
        )

        detail = exc.detail
        message = detail if isinstance(detail, str) else str(detail)
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(
                code="HTTP_ERROR",
                message=message,
                is_retryable=exc.status_code >= 500,
                request_id=request_id,
            ),
            headers=dict(exc.headers) if exc.headers else None,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        request_id = _request_id(request)
        _log_if_new(exc, level="WARNING", request_id=request_id, with_traceback=False)

        # Keep the message human-readable; never dump the raw errors array as
        # the top-level message (clients still get a stable code).
        first = exc.errors()[0] if exc.errors() else {}
        loc = " → ".join(str(p) for p in first.get("loc", ())) or "body"
        msg = first.get("msg", "Request validation failed")
        return JSONResponse(
            status_code=422,
            content=_error_body(
                code="VALIDATION_ERROR",
                message=f"{loc}: {msg}",
                is_retryable=False,
                request_id=request_id,
            ),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        request_id = _request_id(request)
        _log_if_new(exc, level="CRITICAL", request_id=request_id, with_traceback=True)
        return JSONResponse(
            status_code=500,
            content=_error_body(
                code="INTERNAL_ERROR",
                message="An unexpected error occurred.",
                is_retryable=False,
                request_id=request_id,
            ),
        )
