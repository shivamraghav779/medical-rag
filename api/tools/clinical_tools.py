"""LLM-callable clinical tools — guidelines, drug monographs/interactions,
lab reference ranges, treatment protocols, and diagnostic criteria.

Each function is a thin wrapper over RetrievalService / DrugInteractionService
/ LLMService / RedisService, with the calling service instances passed in as
parameters (dependency-injection-via-parameters) rather than reached for as
module globals. Docstrings are written for an LLM tool-calling schema: a
one-line description an LLM would see, followed by Args/Returns.

No new business logic and no new prompt content — every code path here is a
direct port of what DrugInteractionAgent / LabReferenceAgent / the retrieval
pipeline already do. Nothing in this module is called by the live Orchestrator
pipeline; these are standalone, individually invocable tools.
"""

from __future__ import annotations

from typing import Optional

from api.core.config import settings
from api.core.constants import (
    DOC_TYPE_FILTERS,
    DRUG_MONOGRAPH_DOC_TYPE,
    LAB_CRITICAL_HIGH_RE,
    LAB_CRITICAL_LOW_RE,
    LAB_PARAMETER_ALIASES,
    LAB_RANGE_RE,
)
from api.core.exceptions import GroqException, PineconeQueryException
from api.core.logger import get_logger, log_exception
from api.models.schemas import InteractionResult, RetrievedChunk
from api.services.drug_interaction_service import DrugInteractionService
from api.services.llm_service import LLMService
from api.services.redis_service import RedisService
from api.services.retrieval_service import RetrievalService

logger = get_logger(__name__)


def _normalize_lab_param(raw: str) -> str:
    """Same normalization LabReferenceAgent uses (kept in sync — pure lookup,
    no state)."""
    key = raw.strip().lower()
    return LAB_PARAMETER_ALIASES.get(key, key)


def _parse_range_from_text(text: str) -> Optional[dict]:
    """Same range-parsing regex logic LabReferenceAgent uses (kept in sync —
    pure parsing, no state)."""
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


def _serialize_chunks(chunks: list[RetrievedChunk]) -> list[dict]:
    return [chunk.model_dump() for chunk in chunks]


async def search_clinical_guidelines(
    retrieval_service: RetrievalService,
    query: str,
    top_k: int = 5,
    doc_filter: Optional[list[str]] = None,
) -> list[dict]:
    """Search clinical practice guidelines for evidence-backed recommendations.

    Use this when the question asks what a clinical guideline recommends —
    e.g. screening intervals, first-line management, diagnostic thresholds.

    Args:
        query: The clinical question or topic to search guidelines for.
        top_k: Maximum number of guideline passages to return.
        doc_filter: Optional list of specific document names to restrict to.

    Returns:
        A list of guideline chunks, each with doc_name, page_number, text,
        score, authority_level, and publication_year.
    """
    doc_type_filter = DOC_TYPE_FILTERS.get("treatment_query")
    chunks = await retrieval_service.hybrid_retrieve(query, top_k, doc_filter=doc_filter)
    filtered = [c for c in chunks if not doc_type_filter or c.doc_type in doc_type_filter]
    return _serialize_chunks(filtered or chunks)


async def search_drug_monograph(
    retrieval_service: RetrievalService,
    drug_name: str,
    top_k: int = 5,
) -> list[dict]:
    """Search drug monographs for dosing, indications, contraindications, and
    adverse-effect information about a specific medication.

    Args:
        drug_name: The medication name to search a monograph for.
        top_k: Maximum number of monograph passages to return.

    Returns:
        A list of drug-monograph chunks, each with doc_name, page_number,
        text, score, and authority_level.
    """
    try:
        chunks = await retrieval_service.dense_search(
            drug_name,
            top_k,
            doc_filter=None,
            doc_type_filter=DOC_TYPE_FILTERS.get("drug_information"),
        )
    except PineconeQueryException as exc:
        log_exception(logger, exc)
        return []

    monographs = [c for c in chunks if c.doc_type == DRUG_MONOGRAPH_DOC_TYPE] or chunks
    return _serialize_chunks(monographs[:top_k])


async def check_drug_interaction(
    drug_service: DrugInteractionService,
    llm_service: LLMService,
    retrieval_service: RetrievalService,
    drug_a: str,
    drug_b: str,
    dense_top_k: Optional[int] = None,
) -> dict:
    """Check for a clinically significant interaction between two drugs.

    Checks the Redis interaction cache first; on a miss, retrieves drug
    monograph passages for both drugs and has the LLM extract severity,
    description, and monitoring recommendations from those sources, then
    caches the result. Same logic as the pipeline's drug-interaction path,
    exposed here as a standalone two-drug-name tool.

    Args:
        drug_a: First drug name.
        drug_b: Second drug name.
        dense_top_k: Override for how many monograph candidates to retrieve
            (defaults to the configured dense_top_k).

    Returns:
        A dict with severity, description, recommendation,
        monitoring_parameters, source_doc_name, and source_authority_level.
    """
    cached = await drug_service.check_interaction(drug_a, drug_b)
    if cached is not None:
        return cached.model_dump()

    top_k = dense_top_k or settings.dense_top_k
    search_query = f"{drug_a} {drug_b} drug interaction"
    try:
        chunks = await retrieval_service.dense_search(
            search_query,
            top_k,
            doc_filter=None,
            doc_type_filter=DOC_TYPE_FILTERS.get("drug_interaction"),
        )
        monographs = [c for c in chunks if c.doc_type == DRUG_MONOGRAPH_DOC_TYPE] or chunks
    except PineconeQueryException as exc:
        log_exception(logger, exc)
        return {"severity": "UNKNOWN", "description": "Retrieval failed", "recommendation": ""}

    try:
        extracted = await llm_service.extract_interaction_from_chunks(drug_a, drug_b, monographs[:5])
    except GroqException as exc:
        log_exception(logger, exc)
        return {"severity": "UNKNOWN", "description": "Extraction failed", "recommendation": ""}

    if not extracted.get("source_authority_level") and monographs:
        extracted["source_authority_level"] = monographs[0].authority_level
    if not extracted.get("source_doc_name") and monographs:
        extracted["source_doc_name"] = monographs[0].doc_name

    result = InteractionResult(
        drug_a=drug_a,
        drug_b=drug_b,
        severity=str(extracted.get("severity", "UNKNOWN")),
        description=str(extracted.get("description", "")),
        recommendation=str(extracted.get("recommendation") or extracted.get("clinical_recommendation") or ""),
        clinical_recommendation=str(
            extracted.get("clinical_recommendation") or extracted.get("recommendation") or ""
        ),
        monitoring_parameters=list(extracted.get("monitoring_parameters") or []),
        source_doc_name=extracted.get("source_doc_name"),
        source_authority_level=extracted.get("source_authority_level"),
    )
    await drug_service.store_interaction(drug_a, drug_b, result)
    return result.model_dump()


async def get_lab_normal_range(
    redis_service: RedisService,
    retrieval_service: RetrievalService,
    parameter: str,
    dense_top_k: Optional[int] = None,
) -> Optional[dict]:
    """Look up the normal (and critical) reference range for a lab parameter.

    Checks the Redis range cache first; on a miss, retrieves lab-reference
    passages and parses a range out of the matching text, then caches it.

    Args:
        parameter: The lab parameter name (e.g. "potassium", "hemoglobin").
        dense_top_k: Override for how many reference candidates to retrieve
            (defaults to the configured dense_top_k).

    Returns:
        A dict with normal_low/normal_high and, when available,
        critical_low/critical_high — or None if no range could be found.
    """
    from api.core.exceptions import RedisReadException, RedisWriteException

    normalized = _normalize_lab_param(parameter)

    try:
        cached = await redis_service.get_lab_range(normalized)
    except RedisReadException as exc:
        log_exception(logger, exc)
        cached = None
    if cached is not None:
        return cached

    top_k = dense_top_k or settings.dense_top_k
    search_query = f"{normalized} normal reference range lab values"
    try:
        chunks = await retrieval_service.dense_search(
            search_query, top_k, doc_filter=None, doc_type_filter=["lab_reference"]
        )
    except PineconeQueryException as exc:
        log_exception(logger, exc)
        return None

    for chunk in chunks:
        parsed = _parse_range_from_text(chunk.text)
        if parsed:
            try:
                await redis_service.store_lab_range(normalized, parsed)
            except RedisWriteException as exc:
                log_exception(logger, exc)
            return parsed

    return None


async def get_treatment_protocol(
    retrieval_service: RetrievalService,
    query: str,
    top_k: int = 5,
) -> list[dict]:
    """Search treatment protocols and clinical guidelines for management/
    treatment recommendations for a condition.

    Args:
        query: The condition or treatment question to search protocols for.
        top_k: Maximum number of protocol passages to return.

    Returns:
        A list of protocol/guideline chunks, each with doc_name, page_number,
        text, score, and authority_level.
    """
    doc_type_filter = DOC_TYPE_FILTERS.get("treatment_query")
    try:
        chunks = await retrieval_service.dense_search(
            query, top_k, doc_filter=None, doc_type_filter=doc_type_filter
        )
    except PineconeQueryException as exc:
        log_exception(logger, exc)
        return []
    return _serialize_chunks(chunks[:top_k])


async def search_diagnostic_criteria(
    retrieval_service: RetrievalService,
    query: str,
    top_k: int = 5,
) -> list[dict]:
    """Search diagnostic criteria and clinical guidelines for how a condition
    is diagnosed (thresholds, required findings, diagnostic workups).

    Args:
        query: The condition or diagnostic question to search criteria for.
        top_k: Maximum number of criteria passages to return.

    Returns:
        A list of diagnostic-criteria/guideline chunks, each with doc_name,
        page_number, text, score, and authority_level.
    """
    doc_type_filter = DOC_TYPE_FILTERS.get("diagnosis_support")
    try:
        chunks = await retrieval_service.dense_search(
            query, top_k, doc_filter=None, doc_type_filter=doc_type_filter
        )
    except PineconeQueryException as exc:
        log_exception(logger, exc)
        return []
    return _serialize_chunks(chunks[:top_k])
