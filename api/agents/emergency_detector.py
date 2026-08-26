"""EmergencyDetectorAgent — substring match against configured emergency terms."""

from __future__ import annotations

import time
from typing import Optional

from api.agents.base import BaseAgent
from api.core.config import Settings
from api.core.exceptions import RedisWriteException
from api.core.logger import log_exception
from api.models.schemas import AgentResult
from api.services.redis_service import RedisService

from api.core.constants import (
    EMERGENCY_SPECIALTY,
    EMERGENCY_SPECIALTY_TERMS,
    EMERGENCY_WARNING_MESSAGE,
)
class EmergencyDetectorAgent(BaseAgent):
    name = "Emergency Detector"

    def __init__(
        self,
        redis_service: RedisService,
        settings: Settings,
        session_id: Optional[str] = None,
    ):
        super().__init__(session_id=session_id)
        self._redis = redis_service
        self._terms = [t.lower() for t in settings.emergency_terms]

    async def run(self, query: str = "", specialty: Optional[str] = None, **kwargs) -> AgentResult:
        with self.track_duration() as timing:
            lowered = (query or "").lower()
            terms = list(self._terms)
            if (specialty or "").strip().lower() == EMERGENCY_SPECIALTY:
                for extra in EMERGENCY_SPECIALTY_TERMS:
                    if extra not in terms:
                        terms.append(extra)

            matched = [term for term in terms if term in lowered]
            is_emergency = bool(matched)

            if is_emergency:
                try:
                    await self._redis.log_emergency_query(
                        self.session_id or "unknown",
                        query,
                        time.time(),
                        matched_terms=matched,
                    )
                except RedisWriteException as exc:
                    log_exception(self.logger, exc)

                self.logger.warning(
                    "Emergency indicators detected",
                    extra={
                        "session_id": self.session_id,
                        "matched_terms": matched,
                        "agent_name": self.name,
                    },
                )

        return AgentResult(
            success=True,
            is_emergency=is_emergency,
            matched_terms=matched,
            message=EMERGENCY_WARNING_MESSAGE if is_emergency else None,
            output="emergency" if is_emergency else "clear",
            duration_ms=timing["duration_ms"],
        )
