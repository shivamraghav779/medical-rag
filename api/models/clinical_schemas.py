"""Clinical domain models for the Clinical Decision Support Platform.

These types are the healthcare-specific source of truth. Existing
api.models.schemas keeps the generic RAG shapes; agents and services
that need clinical fields should prefer imports from here.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Document taxonomy
# ---------------------------------------------------------------------------

class DocType(str, Enum):
    CLINICAL_GUIDELINE = "clinical_guideline"
    DRUG_MONOGRAPH = "drug_monograph"
    TREATMENT_PROTOCOL = "treatment_protocol"
    LAB_REFERENCE = "lab_reference"
    DIAGNOSTIC_CRITERIA = "diagnostic_criteria"
    RESEARCH_PAPER = "research_paper"


# ---------------------------------------------------------------------------
# Query classification
# ---------------------------------------------------------------------------

class QueryType(str, Enum):
    DRUG_INFORMATION = "drug_information"
    DRUG_INTERACTION = "drug_interaction"
    DIAGNOSIS_SUPPORT = "diagnosis_support"
    TREATMENT_QUERY = "treatment_query"
    LAB_INTERPRETATION = "lab_interpretation"
    EMERGENCY_QUERY = "emergency_query"
    GENERAL_CLINICAL = "general_clinical"


class QueryClassification(BaseModel):
    """Result of the QueryAnalyzerAgent classification step."""

    query_type: QueryType = QueryType.GENERAL_CLINICAL
    intent: str = "unknown"
    expanded_queries: List[str] = Field(default_factory=list)
    requires_retrieval: bool = True
    doc_filter: Optional[List[str]] = None
    # Pinecone metadata filters derived from query type / session context.
    doc_type_filter: Optional[List[str]] = None
    extracted_drug_names: List[str] = Field(default_factory=list)
    confidence: Optional[float] = None


# ---------------------------------------------------------------------------
# Lab values
# ---------------------------------------------------------------------------

class LabFlag(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL_LOW = "CRITICAL_LOW"
    CRITICAL_HIGH = "CRITICAL_HIGH"


class LabValue(BaseModel):
    """One numeric lab finding extracted from a clinician query."""

    parameter: str
    parameter_normalized: str
    value: float
    unit: str
    flag: Optional[LabFlag] = None
    normal_low: Optional[float] = None
    normal_high: Optional[float] = None
    critical_low: Optional[float] = None
    critical_high: Optional[float] = None
    source: Optional[str] = None  # "cache" | "retrieval" | None


# ---------------------------------------------------------------------------
# Drugs & interactions
# ---------------------------------------------------------------------------

class InteractionSeverity(str, Enum):
    MAJOR = "MAJOR"
    MODERATE = "MODERATE"
    MINOR = "MINOR"
    NONE = "NONE"
    UNKNOWN = "UNKNOWN"


class DrugInfo(BaseModel):
    drug_name: str
    drug_generic_name: Optional[str] = None
    drug_class: Optional[str] = None
    atc_code: Optional[str] = None
    info: Dict[str, Any] = Field(default_factory=dict)


class InteractionResult(BaseModel):
    drug_a: str
    drug_b: str
    severity: InteractionSeverity = InteractionSeverity.UNKNOWN
    description: str = ""
    clinical_recommendation: str = ""
    # Kept for back-compat with earlier InteractionResult.recommendation field.
    recommendation: str = ""
    monitoring_parameters: List[str] = Field(default_factory=list)
    source_doc_name: Optional[str] = None
    source_authority_level: Optional[int] = None


# ---------------------------------------------------------------------------
# Session clinical context
# ---------------------------------------------------------------------------

class ClinicalContext(BaseModel):
    specialty: Optional[str] = None
    patient_age_group: Optional[str] = None  # pediatric | adult | geriatric
    patient_weight_kg: Optional[float] = None
    disclaimer_shown: bool = False
    query_count: int = 0
    last_query_type: Optional[str] = None


# ---------------------------------------------------------------------------
# Document parsing (PyMuPDF)
# ---------------------------------------------------------------------------

class ParsedTable(BaseModel):
    page_number: Optional[int] = None
    text: str
    html: Optional[str] = None


class ParsedDocument(BaseModel):
    """Structured output of DocumentParser for clinical PDFs."""

    full_text: str
    tables: List[ParsedTable] = Field(default_factory=list)
    section_titles: List[str] = Field(default_factory=list)
    page_count: int = 0
    element_count: int = 0
    has_tables: bool = False
    strategy: str = "pymupdf"  # pymupdf (primary)
    parse_method: str = "pymupdf"  # pymupdf
    # Narrative blocks interleaved with table/section markers for chunking.
    structured_blocks: List[Dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

class AnalyticsResponse(BaseModel):
    query_types: Dict[str, int] = Field(default_factory=dict)
    top_drugs: List[Dict[str, Any]] = Field(default_factory=list)
    flagged_emergency: List[Dict[str, Any]] = Field(default_factory=list)
    doc_type_queries: Dict[str, int] = Field(default_factory=dict)
    faithfulness_scores: List[float] = Field(default_factory=list)
    faithfulness_rolling_avg: Optional[float] = None
