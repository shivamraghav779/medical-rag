"""Request-scoped LLM token usage collection via contextvars."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator, Optional


@dataclass
class UsageRecord:
    operation: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class UsageCollector:
    records: list[UsageRecord] = field(default_factory=list)

    def add(
        self,
        operation: str,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
    ) -> None:
        prompt = max(0, int(prompt_tokens or 0))
        completion = max(0, int(completion_tokens or 0))
        total = max(0, int(total_tokens or 0)) or (prompt + completion)
        if prompt == 0 and completion == 0 and total == 0:
            return
        self.records.append(
            UsageRecord(
                operation=operation,
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=total,
            )
        )


_collector: ContextVar[Optional[UsageCollector]] = ContextVar(
    "llm_usage_collector", default=None
)


def get_collector() -> Optional[UsageCollector]:
    return _collector.get()


def note_usage(operation: str, usage: dict[str, int] | None) -> None:
    collector = _collector.get()
    if collector is None or not usage:
        return
    collector.add(
        operation,
        prompt_tokens=usage.get("prompt_tokens", 0),
        completion_tokens=usage.get("completion_tokens", 0),
        total_tokens=usage.get("total_tokens", 0),
    )


@contextmanager
def track_llm_usage() -> Iterator[UsageCollector]:
    """Activate a collector for the current asyncio task / request."""
    collector = UsageCollector()
    token = _collector.set(collector)
    try:
        yield collector
    finally:
        _collector.reset(token)
