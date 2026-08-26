"""Central logging: JSON-formatted console + rotating daily file output,
plus cross-layer exception deduplication so one error doesn't fan out into
3-4 alerts as it propagates tool -> agent -> orchestrator -> router."""

import hashlib
import json
import logging
import os
import re
import threading
import time
import traceback
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

from api.core.constants import (
    EXCEPTION_TRACKER_MAXLEN,
    EXCEPTION_TRACKER_TTL_SECONDS,
    LOG_DIR,
    LOG_RETENTION_DAYS,
    MAX_LOG_FILE_BYTES,
    PROJECT_ROOT,
    SLOW_REQUEST_THRESHOLD_MS,
)

MAX_FILE_BYTES = MAX_LOG_FILE_BYTES
RETENTION_DAYS = LOG_RETENTION_DAYS
LOG_LEVEL = getattr(logging, os.environ.get("LOG_LEVEL", "DEBUG").upper(), logging.DEBUG)

F = TypeVar("F", bound=Callable[..., Any])

# ---------------------------------------------------------------------------
# LangSmith optional tracing bootstrap
# ---------------------------------------------------------------------------

_langsmith_configured = False


def configure_langsmith_from_env() -> bool:
    """Set LangSmith env vars from Settings when an API key is present.
    Returns True when tracing is active. Safe to call repeatedly."""
    global _langsmith_configured
    try:
        from api.core.config import get_settings
        cfg = get_settings()
    except Exception:
        return False

    api_key = (cfg.langsmith_api_key or os.environ.get("LANGSMITH_API_KEY") or "").strip()
    if not api_key or not cfg.langsmith_tracing_enabled:
        _langsmith_configured = False
        return False

    os.environ["LANGSMITH_API_KEY"] = api_key
    os.environ["LANGCHAIN_API_KEY"] = api_key
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_PROJECT"] = (
        cfg.langchain_project
        or os.environ.get("LANGCHAIN_PROJECT")
        or "rag-platform-clinical"
    )
    _langsmith_configured = True
    return True


def is_langsmith_enabled() -> bool:
    return _langsmith_configured or bool(os.environ.get("LANGSMITH_API_KEY"))


def conditional_traceable(name: str, run_type: str = "chain") -> Callable[[F], F]:
    """Wrap with langsmith.traceable only when LangSmith is configured.
    Otherwise the original function is returned unchanged."""

    def decorator(fn: F) -> F:
        if not configure_langsmith_from_env():
            return fn
        try:
            from langsmith import traceable
            return traceable(name=name, run_type=run_type)(fn)  # type: ignore[return-value]
        except Exception:
            return fn

    return decorator


# Attempt configuration once at import so get_logger callers see status.
try:
    _ls_on = configure_langsmith_from_env()
    logging.getLogger(__name__).info(
        "LangSmith tracing %s",
        "ENABLED" if _ls_on else "DISABLED",
    )
except Exception:
    pass

_FILENAME_RE = re.compile(r"^app_(\d{4}-\d{2}-\d{2})\.log(\.\d+)?$")

# Standard LogRecord attributes — anything else attached via extra={} is
# treated as custom structured data and passed through to the JSON output.
_STANDARD_RECORD_ATTRS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "taskName",
}


class JSONFormatter(logging.Formatter):
    """One JSON object per line. Always includes timestamp, level,
    logger_name, module, function_name, line_number, message. Adds
    exception_type/exception_message/traceback when exc_info is present,
    and passes through any extra fields (request_id, session_id,
    agent_name, etc.) attached via logger calls' extra={}."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger_name": record.name,
            "module": record.module,
            "function_name": record.funcName,
            "line_number": record.lineno,
            "message": record.getMessage(),
        }

        if record.exc_info:
            exc_type, exc_value, exc_tb = record.exc_info
            log_obj["exception_type"] = exc_type.__name__ if exc_type else None
            log_obj["exception_message"] = str(exc_value) if exc_value else None
            log_obj["traceback"] = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))

        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_ATTRS and key not in log_obj:
                log_obj[key] = value

        return json.dumps(log_obj, default=str)


class DailyRotatingJSONFileHandler(logging.Handler):
    """Rotates to a new app_YYYY-MM-DD.log at midnight, AND mid-day if the
    current file would exceed max_bytes (stdlib's TimedRotatingFileHandler
    only rotates on time, RotatingFileHandler only on size — neither does
    both, and neither produces the app_YYYY-MM-DD.log naming pattern, so
    this is a small custom handler rather than a stdlib subclass).
    Same-day overflow files are suffixed app_YYYY-MM-DD.log.1, .2, etc.
    Deletes files older than retention_days whenever the date rolls over."""

    def __init__(self, log_dir: Path, max_bytes: int = MAX_FILE_BYTES, retention_days: int = RETENTION_DAYS):
        super().__init__()
        self.log_dir = log_dir
        self.max_bytes = max_bytes
        self.retention_days = retention_days
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self._current_date: Optional[str] = None
        self._current_suffix = 0
        self._stream = None
        today = datetime.now().strftime("%Y-%m-%d")
        self._current_date = today
        self._current_suffix = self._find_resume_suffix(today)
        self._open_current()
        self._cleanup_old_logs()

    def _base_filename(self, date_str: str) -> Path:
        return self.log_dir / f"app_{date_str}.log"

    def _filepath_for(self, date_str: str, suffix: int) -> Path:
        base = self._base_filename(date_str)
        return base if suffix == 0 else Path(f"{base}.{suffix}")

    def _find_resume_suffix(self, date_str: str) -> int:
        """On (re)open for a given date, resume writing into the highest
        existing suffix for that date unless it's already at/over max_bytes,
        in which case start a new one — avoids clobbering on process restart."""
        suffix = 0
        while self._filepath_for(date_str, suffix + 1).exists():
            suffix += 1
        current = self._filepath_for(date_str, suffix)
        if current.exists() and current.stat().st_size >= self.max_bytes:
            suffix += 1
        return suffix

    def _open_current(self) -> None:
        if self._stream:
            self._stream.close()
        path = self._filepath_for(self._current_date, self._current_suffix)
        self._stream = open(path, "a", encoding="utf-8")

    def _roll_if_needed(self, next_line_bytes: int) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self._current_date:
            self._current_date = today
            self._current_suffix = self._find_resume_suffix(today)
            self._open_current()
            self._cleanup_old_logs()
            return

        if self._stream.tell() + next_line_bytes > self.max_bytes:
            self._current_suffix += 1
            self._open_current()

    def _cleanup_old_logs(self) -> None:
        cutoff = datetime.now().date() - timedelta(days=self.retention_days)
        try:
            for path in self.log_dir.iterdir():
                match = _FILENAME_RE.match(path.name)
                if not match:
                    continue
                file_date = datetime.strptime(match.group(1), "%Y-%m-%d").date()
                if file_date < cutoff:
                    path.unlink(missing_ok=True)
        except OSError:
            pass

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record) + "\n"
            line_bytes = len(line.encode("utf-8"))
            self._roll_if_needed(line_bytes)
            self._stream.write(line)
            self._stream.flush()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        if self._stream:
            self._stream.close()
        super().close()


class ExceptionTracker:
    """Thread-safe, TTL-bounded fingerprint tracker. A fingerprint is
    SHA256(exception_type + exception_message + module + function), where
    module/function are taken from the exception's ORIGINAL raise site
    (deepest frame in its traceback) — not from wherever it's being logged
    from — so the same exception produces the same fingerprint no matter
    which layer (tool/agent/orchestrator/router) calls check_and_record on
    it as it propagates. First caller within the TTL window logs for real;
    every other caller within that window is suppressed."""

    def __init__(
        self,
        ttl_seconds: int = EXCEPTION_TRACKER_TTL_SECONDS,
        maxlen: int = EXCEPTION_TRACKER_MAXLEN,
    ):
        self._ttl = ttl_seconds
        self._expiry: dict[str, float] = {}
        self._order: deque = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    @staticmethod
    def fingerprint(exc_type: str, exc_message: str, module: str, function: str) -> str:
        raw = f"{exc_type}|{exc_message}|{module}|{function}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _evict_expired(self, now: float) -> None:
        expired = [fp for fp, exp in self._expiry.items() if exp <= now]
        for fp in expired:
            self._expiry.pop(fp, None)

    def check_and_record(self, exc_type: str, exc_message: str, module: str, function: str) -> tuple[bool, str]:
        """Returns (is_duplicate, fingerprint)."""
        fp = self.fingerprint(exc_type, exc_message, module, function)
        now = time.time()
        with self._lock:
            self._evict_expired(now)
            expiry = self._expiry.get(fp)
            if expiry is not None and expiry > now:
                return True, fp

            if fp not in self._expiry:
                if len(self._order) >= (self._order.maxlen or 0):
                    evicted = self._order.popleft()
                    self._expiry.pop(evicted, None)
                self._order.append(fp)
            self._expiry[fp] = now + self._ttl
            return False, fp


_exception_tracker = ExceptionTracker()


def get_exception_tracker() -> ExceptionTracker:
    """Shared app-wide instance — used here by log_exception(), and directly
    by api/core/exceptions.py's global handlers per Deliverable 2."""
    return _exception_tracker


def _origin_frame_info(exc: BaseException) -> tuple[str, str]:
    tb = exc.__traceback__
    if tb is None:
        return "unknown", "unknown"
    while tb.tb_next is not None:
        tb = tb.tb_next
    frame = tb.tb_frame
    module = frame.f_globals.get("__name__", "unknown")
    function = frame.f_code.co_name
    return module, function


def log_exception(logger: logging.Logger, exc: BaseException, level: int = logging.ERROR, **extra_fields) -> None:
    """Not one of the 7 explicitly-named exports, but necessary: this is how
    any layer logs a caught exception through the dedup tracker instead of
    calling logger.error()/logger.critical() directly, which would bypass
    deduplication entirely."""
    exc_type = type(exc).__name__
    exc_message = str(exc)
    module, function = _origin_frame_info(exc)

    is_duplicate, fingerprint = _exception_tracker.check_and_record(exc_type, exc_message, module, function)
    if is_duplicate:
        logger.debug(f"suppressed duplicate: {fingerprint[:8]}", extra=extra_fields)
        return
    logger.log(level, exc_message, exc_info=exc, extra=extra_fields)


# ---------------------------------------------------------------------------
# Handlers + logger factory
# ---------------------------------------------------------------------------

_file_handler_instance: Optional[logging.Handler] = None
_console_handler_instance: Optional[logging.Handler] = None
_handler_init_lock = threading.Lock()
_loggers: dict[str, logging.Logger] = {}
_loggers_lock = threading.Lock()


def _get_file_handler() -> logging.Handler:
    global _file_handler_instance
    if _file_handler_instance is None:
        with _handler_init_lock:
            if _file_handler_instance is None:
                log_dir = LOG_DIR
                # Vercel/Lambda: only /tmp is writable.
                if os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
                    log_dir = Path("/tmp/logs")
                try:
                    handler = DailyRotatingJSONFileHandler(log_dir)
                    handler.setFormatter(JSONFormatter())
                    _file_handler_instance = handler
                except OSError:
                    # Read-only filesystem — fall back to console-only.
                    _file_handler_instance = logging.NullHandler()
    return _file_handler_instance


def _get_console_handler() -> logging.Handler:
    global _console_handler_instance
    if _console_handler_instance is None:
        with _handler_init_lock:
            if _console_handler_instance is None:
                handler = logging.StreamHandler()
                handler.setFormatter(JSONFormatter())
                _console_handler_instance = handler
    return _console_handler_instance


def get_logger(name: str) -> logging.Logger:
    """Named logger with console + rotating-file JSON handlers attached.
    Cached — repeated calls with the same name return the same instance."""
    with _loggers_lock:
        if name in _loggers:
            return _loggers[name]

        logger = logging.getLogger(name)
        logger.setLevel(LOG_LEVEL)
        logger.propagate = False
        if not logger.handlers:
            logger.addHandler(_get_console_handler())
            logger.addHandler(_get_file_handler())

        _loggers[name] = logger
        return logger


# ---------------------------------------------------------------------------
# Structured logging helpers
# ---------------------------------------------------------------------------

# Structured logging helpers — threshold imported from constants.


def log_request_start(logger: logging.Logger, method: str, path: str, ip: str, request_id: str) -> None:
    logger.info(
        f"Request received: {method} {path}",
        extra={"request_id": request_id, "http_method": method, "http_path": path, "client_ip": ip},
    )


def log_request_end(
    logger: logging.Logger, method: str, path: str, status_code: int, duration_ms: float, request_id: str
) -> None:
    level = logging.WARNING if duration_ms > SLOW_REQUEST_THRESHOLD_MS else logging.INFO
    logger.log(
        level,
        f"Request completed: {method} {path} -> {status_code} ({duration_ms:.1f}ms)",
        extra={
            "request_id": request_id,
            "http_method": method,
            "http_path": path,
            "status_code": status_code,
            "duration_ms": duration_ms,
        },
    )


def log_agent_step(
    logger: logging.Logger, agent_name: str, status: str, detail: Optional[str], session_id: Optional[str]
) -> None:
    message = f"Agent step: {agent_name} {status}"
    if detail:
        message += f" — {detail}"
    logger.debug(
        message,
        extra={"agent_name": agent_name, "status": status, "session_id": session_id},
    )


def log_cache_hit(logger: logging.Logger, cache_type: str, key_prefix: str) -> None:
    logger.debug(
        f"Cache hit: {cache_type}",
        extra={"cache_type": cache_type, "key_prefix": key_prefix, "cache_result": "hit"},
    )


def log_cache_miss(logger: logging.Logger, cache_type: str, key_prefix: str, critical: bool = False) -> None:
    level = logging.WARNING if critical else logging.DEBUG
    logger.log(
        level,
        f"Cache miss: {cache_type}",
        extra={"cache_type": cache_type, "key_prefix": key_prefix, "cache_result": "miss"},
    )


def log_api_call(logger: logging.Logger, service: str, operation: str, duration_ms: float, success: bool) -> None:
    level = logging.DEBUG if success else logging.ERROR
    status = "succeeded" if success else "failed"
    logger.log(
        level,
        f"API call: {service}.{operation} {status} ({duration_ms:.1f}ms)",
        extra={"service": service, "operation": operation, "duration_ms": duration_ms, "success": success},
    )
