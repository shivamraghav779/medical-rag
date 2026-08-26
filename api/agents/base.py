"""BaseAgent — shared logging, SSE status events, and duration tracking."""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import contextmanager
from time import perf_counter
from typing import Any, Generator, Optional

from api.core.logger import get_logger, log_agent_step
from api.models.schemas import AgentResult


class BaseAgent(ABC):
    name: str = "BaseAgent"

    def __init__(self, session_id: Optional[str] = None):
        self.session_id = session_id
        self.logger = get_logger(self.__class__.__module__)

    @abstractmethod
    async def run(self, **kwargs: Any) -> AgentResult:
        ...

    def emit_status(self, status: str, output: Optional[str] = None) -> dict:
        """SSE agent_status event dict."""
        log_agent_step(self.logger, self.name, status, output, self.session_id)
        return {
            "type": "agent_status",
            "agent": self.name,
            "status": status,
            "output": output,
        }

    @contextmanager
    def track_duration(self) -> Generator[dict, None, None]:
        """Context manager that records elapsed ms into the yielded dict."""
        box: dict = {"duration_ms": 0.0}
        started = perf_counter()
        try:
            yield box
        finally:
            box["duration_ms"] = (perf_counter() - started) * 1000
            self.logger.debug(
                f"{self.name} completed in {box['duration_ms']:.1f}ms",
                extra={"agent_name": self.name, "session_id": self.session_id, "duration_ms": box["duration_ms"]},
            )
