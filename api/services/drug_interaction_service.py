"""DrugInteractionService — Redis-backed drug graph + LLM extraction helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from api.core.exceptions import RedisReadException
from api.core.logger import conditional_traceable, get_logger, log_exception
from api.models.schemas import DrugInfo, InteractionResult
from api.services.redis_service import RedisService

if TYPE_CHECKING:
    from api.services.llm_service import LLMService

logger = get_logger(__name__)


class DrugInteractionService:
    def __init__(self, redis_service: RedisService):
        self._redis = redis_service

    @conditional_traceable(name="DrugInteractionService.check_interaction", run_type="tool")
    async def check_interaction(self, drug_a: str, drug_b: str) -> Optional[InteractionResult]:
        try:
            raw = await self._redis.get_interaction(drug_a, drug_b)
        except RedisReadException as exc:
            log_exception(logger, exc)
            return None
        if raw is None:
            return None

        monitoring = raw.get("monitoring_parameters") or []
        if not isinstance(monitoring, list):
            monitoring = []

        clinical_rec = str(
            raw.get("clinical_recommendation") or raw.get("recommendation") or ""
        )
        recommendation = str(
            raw.get("recommendation") or raw.get("clinical_recommendation") or ""
        )

        authority = raw.get("source_authority_level")
        try:
            authority = int(authority) if authority is not None else None
        except (TypeError, ValueError):
            authority = None

        return InteractionResult(
            drug_a=raw.get("drug_a", drug_a),
            drug_b=raw.get("drug_b", drug_b),
            severity=str(raw.get("severity", "NONE")),
            description=str(raw.get("description", "")),
            recommendation=recommendation,
            clinical_recommendation=clinical_rec,
            monitoring_parameters=[str(m) for m in monitoring],
            source_doc_name=raw.get("source_doc_name"),
            source_authority_level=authority,
        )

    async def store_interaction(self, drug_a: str, drug_b: str, result: InteractionResult | dict) -> bool:
        if isinstance(result, InteractionResult):
            payload = result.model_dump()
        else:
            payload = result
        return await self._redis.store_interaction(drug_a, drug_b, payload)

    @conditional_traceable(name="DrugInteractionService.extract_drug_names", run_type="tool")
    async def extract_drug_names(self, query: str, llm_service: LLMService) -> list[str]:
        return await llm_service.extract_drug_names(query)

    async def get_drug_info(self, drug_name: str) -> Optional[DrugInfo]:
        raw = await self._redis.get_drug_info(drug_name)
        if raw is None:
            return None
        return DrugInfo(drug_name=drug_name, info=raw)
