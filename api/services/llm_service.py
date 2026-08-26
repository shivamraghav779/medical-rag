"""LLMService — all Groq chat / JSON / streaming calls."""

from __future__ import annotations

import json
import time
from typing import Any, AsyncGenerator, Optional

from groq import AsyncGroq

from api.core.config import Settings
from api.core.constants import (
    ASCII_CLOSE_BRACKET,
    ASCII_OPEN_BRACKET,
    CJK_CLOSE_BRACKET,
    CJK_OPEN_BRACKET,
    DOC_TYPE_FILTERS,
    EXPANDED_QUERY_COUNT,
    FAITHFULNESS_JUDGE_FAILURE_VIOLATION,
    GROUNDED_ANSWER_NOT_FOUND,
    INTERACTION_SEVERITY_LEVELS,
    PATIENT_AGE_GERIATRIC,
    QUERY_TYPE_LEGACY_MAP,
    SPECIALTY_PEDIATRICS,
)
from api.core.exceptions import AgentOutputParseException, GroqException, GroqStreamException
from api.core.logger import conditional_traceable, get_logger, log_api_call, log_exception
from api.core.prompts import get_prompt
from api.models.schemas import FaithfulnessResult, QueryAnalysis, RetrievedChunk
from api.services.llm_usage import note_usage

logger = get_logger(__name__)


class LLMService:
    def __init__(self, groq_client: AsyncGroq, settings: Settings):
        self._client = groq_client
        self._settings = settings

    @staticmethod
    def _format_history(conversation_history: list[dict]) -> list[dict]:
        return [
            {"role": entry["role"], "content": entry["content"]}
            for entry in conversation_history
            if entry.get("role") in ("user", "assistant") and entry.get("content")
        ]

    @staticmethod
    def _history_with_summary(
        conversation_history: list[dict],
        conversation_summary: Optional[str] = None,
    ) -> list[dict]:
        messages: list[dict] = []
        if conversation_summary:
            messages.append({
                "role": "system",
                "content": get_prompt("conversation_summary", "context_prefix").format(
                    summary=conversation_summary
                ),
            })
        messages.extend(LLMService._format_history(conversation_history))
        return messages

    @staticmethod
    def _coerce_usage(raw: Any) -> dict[str, int]:
        if raw is None:
            return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        if isinstance(raw, dict):
            prompt = int(raw.get("prompt_tokens", 0) or 0)
            completion = int(raw.get("completion_tokens", 0) or 0)
            total = int(raw.get("total_tokens", 0) or 0) or (prompt + completion)
            return {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": total,
            }
        prompt = int(getattr(raw, "prompt_tokens", 0) or 0)
        completion = int(getattr(raw, "completion_tokens", 0) or 0)
        total = int(getattr(raw, "total_tokens", 0) or 0) or (prompt + completion)
        return {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
        }

    @classmethod
    def _extract_usage(cls, response: Any) -> dict[str, int]:
        if response is None:
            return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        usage = getattr(response, "usage", None)
        if usage is None and isinstance(response, dict):
            usage = response.get("usage")
        if usage is None:
            x_groq = getattr(response, "x_groq", None)
            if x_groq is None and isinstance(response, dict):
                x_groq = response.get("x_groq")
            if isinstance(x_groq, dict):
                usage = x_groq.get("usage")
            elif x_groq is not None:
                usage = getattr(x_groq, "usage", None)
        if usage is None and (
            hasattr(response, "prompt_tokens")
            or (isinstance(response, dict) and "prompt_tokens" in response)
        ):
            usage = response
        return cls._coerce_usage(usage)

    def _record_usage(self, operation: str, response_or_usage: Any) -> dict[str, int]:
        usage = self._extract_usage(response_or_usage)
        note_usage(operation, usage)
        return usage

    async def _create_completion(self, *, operation: str, **kwargs: Any):
        response = await self._client.chat.completions.create(**kwargs)
        self._record_usage(operation, response)
        return response

    async def _create_stream(self, **kwargs: Any):
        """Open a chat stream, requesting usage on the final chunk when supported."""
        kwargs = dict(kwargs)
        kwargs["stream"] = True
        try:
            return await self._client.chat.completions.create(
                **kwargs,
                stream_options={"include_usage": True},
            )
        except Exception as first_exc:
            # Older Groq deployments rejected stream_options — retry without.
            message = str(first_exc).lower()
            if "stream_options" not in message and "include_usage" not in message:
                raise
            log_exception(logger, first_exc)
            logger.warning("Groq rejected stream_options; streaming without usage metadata")
            return await self._client.chat.completions.create(**kwargs)

    async def _iter_content_stream(
        self,
        stream: Any,
        *,
        operation: str,
        normalize_brackets: bool = False,
    ) -> AsyncGenerator[str, None]:
        last_usage_carrier: Any = None
        try:
            async for chunk in stream:
                usage_payload = self._extract_usage(chunk)
                if (
                    usage_payload["prompt_tokens"]
                    or usage_payload["completion_tokens"]
                    or usage_payload["total_tokens"]
                ):
                    last_usage_carrier = chunk

                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                content = getattr(delta, "content", None) if delta is not None else None
                if not content:
                    continue
                if normalize_brackets:
                    content = content.replace(CJK_OPEN_BRACKET, ASCII_OPEN_BRACKET).replace(
                        CJK_CLOSE_BRACKET, ASCII_CLOSE_BRACKET
                    )
                yield content
        finally:
            if last_usage_carrier is not None:
                self._record_usage(operation, last_usage_carrier)

    @staticmethod
    def _format_chunks_block(chunks: list[RetrievedChunk]) -> str:
        parts = []
        for chunk in chunks:
            parts.append(
                f"[{chunk.rank}] (doc: {chunk.doc_name}, page {chunk.page_number})\n{chunk.text}"
            )
        return "\n\n".join(parts)

    @staticmethod
    def _format_clinical_context(
        session_context: Optional[dict],
        lab_context: Optional[str] = None,
    ) -> str:
        if not session_context and not lab_context:
            return ""
        specialty = (session_context or {}).get("specialty")
        age_group = (session_context or {}).get("patient_age_group")
        weight = (session_context or {}).get("patient_weight_kg")
        bits = []
        if specialty:
            bits.append(get_prompt("clinical_context", "specialty_prefix").format(specialty=specialty))
            if str(specialty).lower() == SPECIALTY_PEDIATRICS:
                bits.append(get_prompt("clinical_context", "pediatric_dosing"))
            if str(specialty).lower() == "emergency":
                bits.append(get_prompt("clinical_context", "emergency_priority"))
        if age_group:
            bits.append(get_prompt("clinical_context", "age_group_prefix").format(age_group=age_group))
            if str(age_group).lower() == PATIENT_AGE_GERIATRIC:
                bits.append(get_prompt("clinical_context", "geriatric_dosing"))
        if weight is not None:
            bits.append(get_prompt("clinical_context", "weight_prefix").format(weight_kg=weight))
        if lab_context:
            bits.append(lab_context)
        if not bits:
            return ""
        header = get_prompt("clinical_context", "header").strip()
        return f"\n{header}\n- " + "\n- ".join(bits) + "\n"

    @staticmethod
    def _format_web_context(web_context: Optional[str] = None) -> str:
        if not web_context:
            return ""
        header = get_prompt("web_context", "header").strip()
        return f"\n{header}\n{web_context}\n"

    @conditional_traceable(name="LLMService.enhance_query", run_type="llm")
    async def enhance_query(self, query: str, specialty: Optional[str] = None) -> str:
        user_content = f"Draft query:\n{query.strip()}"
        if specialty:
            user_content += f"\n\nClinical specialty context: {specialty}"

        started = time.perf_counter()
        try:
            response = await self._create_completion(
                operation="enhance_query",
                model=self._settings.groq_model,
                messages=[
                    {"role": "system", "content": get_prompt("enhance_query", "system")},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.3,
            )
            enhanced = (response.choices[0].message.content or "").strip()
            log_api_call(logger, "groq", "enhance_query", (time.perf_counter() - started) * 1000, True)
            return enhanced or query.strip()
        except Exception as exc:
            log_api_call(logger, "groq", "enhance_query", (time.perf_counter() - started) * 1000, False)
            log_exception(logger, exc)
            return query.strip()

    @conditional_traceable(name="LLMService.analyze_query", run_type="llm")
    async def analyze_query(
        self,
        query: str,
        conversation_history: list[dict],
        conversation_summary: Optional[str] = None,
    ) -> QueryAnalysis:
        messages = [{"role": "system", "content": get_prompt("query_analysis", "system")}]
        messages.extend(self._history_with_summary(conversation_history, conversation_summary))
        messages.append({"role": "user", "content": f"Analyze this query: {query}"})

        started = time.perf_counter()
        try:
            response = await self._create_completion(
                operation="analyze_query",
                model=self._settings.groq_model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0,
            )
            raw = response.choices[0].message.content
            parsed = json.loads(raw)
        except Exception as exc:
            log_api_call(logger, "groq", "analyze_query", (time.perf_counter() - started) * 1000, False)
            log_exception(logger, exc)
            logger.warning("analyze_query failed, falling back to safe defaults")
            return QueryAnalysis(
                intent="unknown",
                expanded_queries=[query, query, query],
                requires_retrieval=True,
                doc_filter=None,
                query_type="general_clinical",
                doc_type_filter=None,
            )

        log_api_call(logger, "groq", "analyze_query", (time.perf_counter() - started) * 1000, True)

        expanded_queries = parsed.get("expanded_queries") or []
        if not isinstance(expanded_queries, list):
            expanded_queries = [query]
        expanded_queries = [str(q) for q in expanded_queries][:EXPANDED_QUERY_COUNT]
        while len(expanded_queries) < EXPANDED_QUERY_COUNT:
            expanded_queries.append(query)

        doc_filter = parsed.get("doc_filter")
        if doc_filter is not None and not isinstance(doc_filter, list):
            doc_filter = None

        query_type = str(parsed.get("query_type") or "general_clinical")
        if query_type not in DOC_TYPE_FILTERS:
            query_type = QUERY_TYPE_LEGACY_MAP.get(query_type, "general_clinical")

        drugs = parsed.get("extracted_drug_names") or []
        if not isinstance(drugs, list):
            drugs = []

        return QueryAnalysis(
            intent=str(parsed.get("intent", "unknown")),
            expanded_queries=expanded_queries,
            requires_retrieval=bool(parsed.get("requires_retrieval", True)),
            doc_filter=doc_filter,
            query_type=query_type,
            doc_type_filter=DOC_TYPE_FILTERS.get(query_type),
            extracted_drug_names=[str(d).strip() for d in drugs if str(d).strip()],
        )

    @conditional_traceable(name="LLMService.stream_answer", run_type="llm")
    async def stream_answer(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        conversation_history: list[dict],
        session_context: Optional[dict] = None,
        lab_context: Optional[str] = None,
        conversation_summary: Optional[str] = None,
        web_context: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        system_prompt = get_prompt("generation", "system").format(
            chunks_block=self._format_chunks_block(chunks),
            clinical_context=self._format_clinical_context(session_context, lab_context),
            web_context=self._format_web_context(web_context),
        )
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(self._history_with_summary(conversation_history, conversation_summary))
        messages.append({"role": "user", "content": query})

        try:
            stream = await self._create_stream(
                model=self._settings.groq_model,
                messages=messages,
                temperature=0,
            )
        except Exception as exc:
            log_exception(logger, exc)
            raise GroqStreamException("Failed to open Groq answer stream") from exc

        try:
            async for token in self._iter_content_stream(
                stream,
                operation="stream_answer",
                normalize_brackets=True,
            ):
                yield token
        except Exception as exc:
            log_exception(logger, exc)
            raise GroqStreamException("Groq answer stream interrupted") from exc

    async def stream_conversational(
        self,
        query: str,
        conversation_history: list[dict],
        conversation_summary: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        messages = [{"role": "system", "content": get_prompt("conversational", "system")}]
        messages.extend(self._history_with_summary(conversation_history, conversation_summary))
        messages.append({"role": "user", "content": query})

        try:
            stream = await self._create_stream(
                model=self._settings.groq_model,
                messages=messages,
                temperature=0.7,
            )
        except Exception as exc:
            log_exception(logger, exc)
            raise GroqStreamException("Failed to open Groq conversational stream") from exc

        try:
            async for token in self._iter_content_stream(
                stream,
                operation="stream_conversational",
            ):
                yield token
        except Exception as exc:
            log_exception(logger, exc)
            raise GroqStreamException("Groq conversational stream interrupted") from exc

    @conditional_traceable(name="LLMService.judge_faithfulness", run_type="llm")
    async def judge_faithfulness(
        self,
        query: str,
        answer: str,
        chunks: list[RetrievedChunk],
    ) -> FaithfulnessResult:
        # Grounded refusal is definitionally faithful — never FAIL it.
        if (answer or "").strip() == GROUNDED_ANSWER_NOT_FOUND:
            return FaithfulnessResult(score=1.0, verdict="PASS", violations=[])

        chunks_block = self._format_chunks_block(chunks)
        user_content = (
            f"QUERY:\n{query}\n\nANSWER:\n{answer}\n\nSOURCE CHUNKS:\n{chunks_block}"
        )

        started = time.perf_counter()
        try:
            response = await self._create_completion(
                operation="judge_faithfulness",
                model=self._settings.groq_model,
                messages=[
                    {"role": "system", "content": get_prompt("faithfulness", "system")},
                    {"role": "user", "content": user_content},
                ],
                response_format={"type": "json_object"},
                temperature=0,
            )
            parsed = json.loads(response.choices[0].message.content)
            score = float(parsed.get("score", 0.0))
            score = max(0.0, min(1.0, score))
            violations = parsed.get("violations") or []
            if not isinstance(violations, list):
                violations = [str(violations)]
            violations = [str(v) for v in violations]
        except Exception as exc:
            log_api_call(logger, "groq", "faithfulness", (time.perf_counter() - started) * 1000, False)
            log_exception(logger, exc)
            score = 0.0
            violations = [FAITHFULNESS_JUDGE_FAILURE_VIOLATION]
        else:
            log_api_call(logger, "groq", "faithfulness", (time.perf_counter() - started) * 1000, True)

        if score >= self._settings.faithfulness_pass_threshold:
            verdict = "PASS"
        elif score >= self._settings.faithfulness_warn_threshold:
            verdict = "WARN"
        else:
            verdict = "FAIL"

        if verdict == "WARN":
            logger.warning(
                f"Faithfulness WARN score={score}",
                extra={"faithfulness_score": score, "verdict": verdict},
            )

        return FaithfulnessResult(score=score, verdict=verdict, violations=violations)

    async def extract_drug_names(self, query: str) -> list[str]:
        started = time.perf_counter()
        try:
            response = await self._create_completion(
                operation="extract_drug_names",
                model=self._settings.groq_model,
                messages=[
                    {"role": "system", "content": get_prompt("drug_extraction", "system")},
                    {"role": "user", "content": query},
                ],
                response_format={"type": "json_object"},
                temperature=0,
            )
            parsed = json.loads(response.choices[0].message.content)
            drugs = parsed.get("drugs") or []
            if not isinstance(drugs, list):
                raise AgentOutputParseException("drugs field was not a list")
            log_api_call(logger, "groq", "extract_drugs", (time.perf_counter() - started) * 1000, True)
            return [str(d).strip() for d in drugs if str(d).strip()]
        except AgentOutputParseException:
            raise
        except Exception as exc:
            log_api_call(logger, "groq", "extract_drugs", (time.perf_counter() - started) * 1000, False)
            log_exception(logger, exc)
            raise GroqException("Failed to extract drug names") from exc

    async def extract_interaction_from_chunks(
        self,
        drug_a: str,
        drug_b: str,
        chunks: list[RetrievedChunk],
    ) -> dict:
        user_content = (
            f"Drug A: {drug_a}\nDrug B: {drug_b}\n\n"
            f"SOURCE CHUNKS:\n{self._format_chunks_block(chunks)}"
        )
        started = time.perf_counter()
        try:
            response = await self._create_completion(
                operation="extract_interaction",
                model=self._settings.groq_model,
                messages=[
                    {"role": "system", "content": get_prompt("interaction_extraction", "system")},
                    {"role": "user", "content": user_content},
                ],
                response_format={"type": "json_object"},
                temperature=0,
            )
            parsed = json.loads(response.choices[0].message.content)
            log_api_call(logger, "groq", "extract_interaction", (time.perf_counter() - started) * 1000, True)
            severity = str(parsed.get("severity", "UNKNOWN")).upper()
            if severity not in INTERACTION_SEVERITY_LEVELS:
                severity = "UNKNOWN"
            recommendation = str(
                parsed.get("clinical_recommendation")
                or parsed.get("recommendation")
                or ""
            )
            monitoring = parsed.get("monitoring_parameters") or []
            if not isinstance(monitoring, list):
                monitoring = [str(monitoring)]
            source_doc = parsed.get("source_doc_name") or (
                chunks[0].doc_name if chunks else None
            )
            source_auth = chunks[0].authority_level if chunks else None
            return {
                "drug_a": drug_a,
                "drug_b": drug_b,
                "severity": severity,
                "description": str(parsed.get("description", "")),
                "recommendation": recommendation,
                "clinical_recommendation": recommendation,
                "monitoring_parameters": [str(m) for m in monitoring],
                "source_doc_name": source_doc,
                "source_authority_level": source_auth,
            }
        except Exception as exc:
            log_api_call(logger, "groq", "extract_interaction", (time.perf_counter() - started) * 1000, False)
            log_exception(logger, exc)
            raise GroqException("Failed to extract drug interaction from chunks") from exc

    async def summarize_conversation(
        self,
        older_messages: list[dict],
        existing_summary: Optional[str] = None,
    ) -> tuple[str, dict[str, int]]:
        """Summarize older conversation turns for permanent storage."""
        lines = []
        if existing_summary:
            lines.append(f"Existing summary:\n{existing_summary}\n")
        lines.append("Messages to incorporate:")
        for msg in older_messages:
            lines.append(f"{msg['role'].upper()}: {msg['content']}")
        user_content = "\n".join(lines)

        started = time.perf_counter()
        response = await self._create_completion(
            operation="summarize_conversation",
            model=self._settings.groq_model,
            messages=[
                {"role": "system", "content": get_prompt("conversation_summary", "system")},
                {"role": "user", "content": user_content},
            ],
            temperature=0,
        )
        summary = (response.choices[0].message.content or "").strip()
        usage = self._extract_usage(response)
        log_api_call(logger, "groq", "summarize_conversation", (time.perf_counter() - started) * 1000, True)
        return summary, usage
