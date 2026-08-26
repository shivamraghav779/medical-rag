"""Logging middleware — structured request start/end with duration."""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from api.core.logger import get_logger, log_request_end, log_request_start

logger = get_logger(__name__)


def _client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = getattr(request.state, "request_id", None) or "-"
        started = time.perf_counter()
        log_request_start(
            logger,
            request.method,
            request.url.path,
            _client_ip(request),
            request_id,
        )
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - started) * 1000
            log_request_end(
                logger,
                request.method,
                request.url.path,
                500,
                duration_ms,
                request_id,
            )
            raise

        duration_ms = (time.perf_counter() - started) * 1000
        log_request_end(
            logger,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            request_id,
        )
        return response
