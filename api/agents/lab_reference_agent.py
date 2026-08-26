"""LabReferenceAgent — extract lab values, look up ranges, flag severity."""

from __future__ import annotations

from typing import Optional

from api.agents.base import BaseAgent
from api.core.config import Settings
from api.core.constants import (
    LAB_CRITICAL_HIGH_RE,
    LAB_CRITICAL_LOW_RE,
    LAB_PARAMETER_ALIASES,
    LAB_RANGE_RE,
    LAB_VALUE_RE,
)
from api.core.exceptions import PineconeQueryException, RedisReadException, RedisWriteException
from api.core.logger import log_exception
from api.models.clinical_schemas import LabFlag, LabValue
from api.models.schemas import AgentResult
from api.services.redis_service import RedisService
from api.services.retrieval_service import RetrievalService


class LabReferenceAgent(BaseAgent):
    name = "Lab Reference"

    def __init__(
        self,
        redis_service: RedisService,
        retrieval_service: RetrievalService,
        settings: Settings,
        session_id: Optional[str] = None,
    ):
        super().__init__(session_id=session_id)
        self._redis = redis_service
        self._retrieval = retrieval_service
        self._settings = settings

    @staticmethod
    def _normalize_param(raw: str) -> str:
        key = raw.strip().lower()
        return LAB_PARAMETER_ALIASES.get(key, key)

    @staticmethod
    def _flag_value(value: float, range_dict: Optional[dict]) -> Optional[LabFlag]:
        if not range_dict:
            return None
        try:
            critical_low = range_dict.get("critical_low")
            critical_high = range_dict.get("critical_high")
            normal_low = range_dict.get("normal_low")
            normal_high = range_dict.get("normal_high")

            if critical_low is not None and value <= float(critical_low):
                return LabFlag.CRITICAL_LOW
            if critical_high is not None and value >= float(critical_high):
                return LabFlag.CRITICAL_HIGH
            if normal_low is not None and value < float(normal_low):
                return LabFlag.LOW
            if normal_high is not None and value > float(normal_high):
                return LabFlag.HIGH
            if normal_low is not None or normal_high is not None:
                return LabFlag.NORMAL
        except (TypeError, ValueError):
            return None
        return None

    @staticmethod
    def _parse_range_from_text(text: str) -> Optional[dict]:
        if not text:
            return None
        match = LAB_RANGE_RE.search(text)
        if not match:
            return None
        range_dict: dict = {
            "normal_low": float(match.group("low")),
            "normal_high": float(match.group("high")),
        }
        cl = LAB_CRITICAL_LOW_RE.search(text)
        ch = LAB_CRITICAL_HIGH_RE.search(text)
        if cl:
            range_dict["critical_low"] = float(cl.group("v"))
        if ch:
            range_dict["critical_high"] = float(ch.group("v"))
        return range_dict

    def _extract_lab_values(self, query: str) -> list[tuple[str, str, float, str]]:
        """Return list of (raw_param, normalized, value, unit)."""
        found: list[tuple[str, str, float, str]] = []
        seen: set[tuple[str, float, str]] = set()
        for match in LAB_VALUE_RE.finditer(query or ""):
            raw = match.group("param")
            normalized = self._normalize_param(raw)
            # Skip common non-lab words that accidentally match the pattern.
            if normalized in {"the", "a", "an", "of", "to", "for", "with", "and", "or", "is", "was"}:
                continue
            value = float(match.group("value"))
            unit = (match.group("unit") or "").strip().replace(" ", "")
            key = (normalized, value, unit.lower())
            if key in seen:
                continue
            seen.add(key)
            found.append((raw, normalized, value, unit))
        return found

    async def _resolve_range(self, parameter: str) -> tuple[Optional[dict], Optional[str]]:
        """Return (range_dict, source) where source is cache|retrieval|None."""
        try:
            cached = await self._redis.get_lab_range(parameter)
        except RedisReadException as exc:
            log_exception(self.logger, exc)
            cached = None

        if cached is not None:
            return cached, "cache"

        search_query = f"{parameter} normal reference range lab values"
        try:
            chunks = await self._retrieval.dense_search(
                search_query,
                self._settings.dense_top_k,
                doc_filter=None,
                doc_type_filter=["lab_reference"],
            )
        except PineconeQueryException as exc:
            log_exception(self.logger, exc)
            return None, None

        for chunk in chunks:
            parsed = self._parse_range_from_text(chunk.text)
            if parsed:
                try:
                    await self._redis.store_lab_range(parameter, parsed)
                except RedisWriteException as exc:
                    log_exception(self.logger, exc)
                return parsed, "retrieval"

        return None, "retrieval" if chunks else None

    @staticmethod
    def _format_lab_context(values: list[LabValue]) -> str:
        if not values:
            return ""
        lines = ["Lab values detected and interpreted:"]
        for lv in values:
            flag = lv.flag.value if lv.flag else "UNKNOWN"
            range_part = ""
            if lv.normal_low is not None or lv.normal_high is not None:
                low = lv.normal_low if lv.normal_low is not None else "?"
                high = lv.normal_high if lv.normal_high is not None else "?"
                range_part = f" (normal {low}-{high}"
                if lv.unit:
                    range_part += f" {lv.unit}"
                range_part += ")"
            unit = f" {lv.unit}" if lv.unit else ""
            lines.append(
                f"- {lv.parameter_normalized}: {lv.value}{unit} → {flag}{range_part}"
            )
        return "\n".join(lines)

    async def run(self, query: str = "", **kwargs) -> AgentResult:
        with self.track_duration() as timing:
            extracted = self._extract_lab_values(query)
            if not extracted:
                return AgentResult(
                    success=True,
                    not_applicable=True,
                    data=[],
                    output="no lab values detected",
                    message=None,
                    duration_ms=timing["duration_ms"],
                )

            lab_values: list[LabValue] = []
            for raw, normalized, value, unit in extracted:
                range_dict, source = await self._resolve_range(normalized)
                flag = self._flag_value(value, range_dict)
                lab_values.append(
                    LabValue(
                        parameter=raw,
                        parameter_normalized=normalized,
                        value=value,
                        unit=unit,
                        flag=flag,
                        normal_low=(
                            float(range_dict["normal_low"])
                            if range_dict and range_dict.get("normal_low") is not None
                            else None
                        ),
                        normal_high=(
                            float(range_dict["normal_high"])
                            if range_dict and range_dict.get("normal_high") is not None
                            else None
                        ),
                        critical_low=(
                            float(range_dict["critical_low"])
                            if range_dict and range_dict.get("critical_low") is not None
                            else None
                        ),
                        critical_high=(
                            float(range_dict["critical_high"])
                            if range_dict and range_dict.get("critical_high") is not None
                            else None
                        ),
                        source=source,
                    )
                )

            lab_context = self._format_lab_context(lab_values)

        return AgentResult(
            success=True,
            data=lab_values,
            output=lab_context or f"{len(lab_values)} lab value(s) interpreted",
            message=lab_context,
            duration_ms=timing["duration_ms"],
        )
