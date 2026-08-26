"""Central application constants — models, limits, patterns, Redis keys, scripts."""

from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LOG_DIR = PROJECT_ROOT / "logs"
PROMPTS_FILE = Path(__file__).resolve().parent / "prompts.yml"

# ---------------------------------------------------------------------------
# Application metadata
# ---------------------------------------------------------------------------

APP_TITLE = "Multi-Agent Clinical RAG Platform"
APP_VERSION = "2.0.0"
APP_DESCRIPTION = (
    "Multi-agent clinical RAG pipeline: Emergency Detection → Query Analysis → "
    "Drug Interaction → Dense Retrieval → Sparse Retrieval → RRF Fusion → "
    "Authority-Weighted Reranking → Generation → Faithfulness"
)
APP_ROOT_MESSAGE = "Multi-Agent Clinical RAG Platform"

# ---------------------------------------------------------------------------
# Model identifiers
# ---------------------------------------------------------------------------

EMBED_MODEL = "embed-english-v3.0"
RERANK_MODEL = "rerank-english-v3.0"

# ---------------------------------------------------------------------------
# Retrieval pipeline
# ---------------------------------------------------------------------------

MAX_DENSE_CANDIDATES = 20
FUSED_TOP_K = 20
RRF_K = 60
EXPANDED_QUERY_COUNT = 3

# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

TABLE_TOKEN_LIMIT = 800
NARRATIVE_TOKEN_LIMIT = 300
MIN_CHUNK_TOKENS = 50
OVERLAP_TOKENS = 50
CHARS_PER_PAGE_ESTIMATE = 3000

# ---------------------------------------------------------------------------
# Upload / document metadata
# ---------------------------------------------------------------------------

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
AUTHORITY_LEVEL_MIN = 1
AUTHORITY_LEVEL_MAX = 5

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

MAX_LOG_FILE_BYTES = 50 * 1024 * 1024
LOG_RETENTION_DAYS = 30
SLOW_REQUEST_THRESHOLD_MS = 10_000
EXCEPTION_TRACKER_TTL_SECONDS = 60
EXCEPTION_TRACKER_MAXLEN = 2000

# ---------------------------------------------------------------------------
# Document parsing strategies
# ---------------------------------------------------------------------------

HI_RES_DOC_TYPES = frozenset({
    "clinical_guideline",
    "drug_monograph",
    "lab_reference",
})
FAST_DOC_TYPES = frozenset({"research_paper"})

# ---------------------------------------------------------------------------
# Query classification & routing
# ---------------------------------------------------------------------------

DOC_TYPE_FILTERS: dict[str, list[str] | None] = {
    "drug_information": ["drug_monograph"],
    "drug_interaction": ["drug_monograph"],
    "diagnosis_support": ["diagnostic_criteria", "clinical_guideline"],
    "treatment_query": ["clinical_guideline", "treatment_protocol"],
    "lab_interpretation": ["lab_reference"],
    "emergency_query": None,
    "general_clinical": None,
}

QUERY_TYPE_LEGACY_MAP: dict[str, str] = {
    "general": "general_clinical",
    "emergency": "emergency_query",
    "clinical_guideline": "treatment_query",
}

INTERACTION_QUERY_KEYWORDS = ("interaction", "together", "combine", "combining")

QUERY_TYPES_REQUIRING_DISCLAIMER = frozenset({
    "diagnosis_support",
    "treatment_query",
    "emergency_query",
})

QUERY_TYPES_NO_DISCLAIMER = frozenset({
    "drug_information",
    "general_clinical",
})

# ---------------------------------------------------------------------------
# Authority-weighted reranking
# ---------------------------------------------------------------------------

DOC_TYPE_MULTIPLIERS: dict[str, float] = {
    "drug_monograph": 1.4,
    "treatment_protocol": 1.3,
    "diagnostic_criteria": 1.4,
    "lab_reference": 1.3,
    "research_paper": 1.0,
}

CLINICAL_GUIDELINE_AUTHORITY_HIGH = 4
CLINICAL_GUIDELINE_MULT_HIGH = 1.5
CLINICAL_GUIDELINE_MULT_LOW = 1.3

RECENCY_MULT_CURRENT = 1.1
RECENCY_MULT_MID = 1.0
RECENCY_MULT_OLD = 0.9
RECENCY_CURRENT_MAX_AGE_YEARS = 1
RECENCY_MID_MAX_AGE_YEARS = 5

RESEARCH_PAPER_DOC_TYPE = "research_paper"
CLINICAL_GUIDELINE_DOC_TYPE = "clinical_guideline"
DRUG_MONOGRAPH_DOC_TYPE = "drug_monograph"

# ---------------------------------------------------------------------------
# Drug interactions
# ---------------------------------------------------------------------------

INTERACTION_SEVERITY_LEVELS = frozenset({
    "MAJOR",
    "MODERATE",
    "MINOR",
    "NONE",
    "UNKNOWN",
})

INTERACTION_SEVERITY_MAJOR = "MAJOR"

# ---------------------------------------------------------------------------
# Emergency detection
# ---------------------------------------------------------------------------

EMERGENCY_SPECIALTY_TERMS = ("acute", "unstable", "crash", "code blue")
EMERGENCY_SPECIALTY = "emergency"
EMERGENCY_WARNING_MESSAGE = (
    "Emergency indicators detected. Please contact emergency services immediately."
)

# ---------------------------------------------------------------------------
# Lab reference patterns
# ---------------------------------------------------------------------------

LAB_PARAMETER_ALIASES: dict[str, str] = {
    "hb": "hemoglobin",
    "hgb": "hemoglobin",
    "hemoglobin": "hemoglobin",
    "cr": "creatinine",
    "creat": "creatinine",
    "creatinine": "creatinine",
    "na": "sodium",
    "sodium": "sodium",
    "k": "potassium",
    "potassium": "potassium",
    "wbc": "wbc",
    "rbc": "rbc",
    "platelets": "platelets",
    "plt": "platelets",
}

LAB_VALUE_PATTERN = (
    r"(?P<param>[A-Za-z][A-Za-z0-9]{0,24})"
    r"\s+"
    r"(?P<value>\d+\.?\d*)"
    r"\s*"
    r"(?P<unit>(?:mg|g|mmol|mEq|U|IU|%|dL|L|/µL|/uL|mmHg)"
    r"(?:\s*/\s*(?:dL|L|µL|uL|mL))?)?"
)

LAB_RANGE_PATTERN = (
    r"(?:normal|reference)\s*(?:range|values?)?\s*[:=]?\s*"
    r"(?P<low>\d+\.?\d*)\s*[-–to]+\s*(?P<high>\d+\.?\d*)"
)

LAB_CRITICAL_LOW_PATTERN = r"critical\s*low\s*[:=]?\s*(?P<v>\d+\.?\d*)"
LAB_CRITICAL_HIGH_PATTERN = r"critical\s*high\s*[:=]?\s*(?P<v>\d+\.?\d*)"

LAB_VALUE_RE = re.compile(LAB_VALUE_PATTERN, re.IGNORECASE)
LAB_RANGE_RE = re.compile(LAB_RANGE_PATTERN, re.IGNORECASE)
LAB_CRITICAL_LOW_RE = re.compile(LAB_CRITICAL_LOW_PATTERN, re.IGNORECASE)
LAB_CRITICAL_HIGH_RE = re.compile(LAB_CRITICAL_HIGH_PATTERN, re.IGNORECASE)

# ---------------------------------------------------------------------------
# Citation / text normalization
# ---------------------------------------------------------------------------

CJK_OPEN_BRACKET = "【"
CJK_CLOSE_BRACKET = "】"
ASCII_OPEN_BRACKET = "["
ASCII_CLOSE_BRACKET = "]"

# ---------------------------------------------------------------------------
# Analytics caps
# ---------------------------------------------------------------------------

ANALYTICS_EMERGENCY_MAX = 100
ANALYTICS_FAITHFULNESS_MAX = 1000
ANALYTICS_TOP_DRUGS_DEFAULT_LIMIT = 20

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

CORS_ALLOW_ORIGINS = ["*"]
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_METHODS = ["*"]
CORS_ALLOW_HEADERS = ["*"]

# ---------------------------------------------------------------------------
# Redis key patterns (no hardcoded keys elsewhere)
# ---------------------------------------------------------------------------

DOC_META = "doc:{doc_id}"
DOC_INDEX = "docs:index"
CHUNK_TEXT = "chunk:{chunk_id}"
CHUNK_META = "chunk:meta:{chunk_id}"
CHUNKS_INDEX = "chunks:index"
SESSION_HISTORY = "session:{session_id}:history"
SESSION_CONTEXT = "session:{session_id}:context"
EMB_CACHE = "emb:{text_hash}"
ANSWER_CACHE = "answer:{query_hash}"
BM25_INDEX = "bm25:index"
BM25_BUILT_AT = "bm25:last_built"
DRUG_INFO = "drug:{drug_name}"
DRUG_INDEX = "drug:index"
INTERACTION = "interaction:{drug_a}:{drug_b}"
LAB_RANGE = "lab:range:{parameter}"
LAB_RANGE_INDEX = "lab:parameters:index"
ANALYTICS_QUERY_TYPES = "analytics:query_types"
ANALYTICS_EMERGENCY = "analytics:flagged_emergency"
ANALYTICS_TOP_DRUGS = "analytics:top_drugs"
ANALYTICS_DOC_TYPE_QUERIES = "analytics:doc_type_queries"
ANALYTICS_FAITHFULNESS = "analytics:faithfulness_scores"
RATE_LIMIT = "ratelimit:{ip}"
LOCK_QUEUE_ROUTING = "lock:queue_routing"

# ---------------------------------------------------------------------------
# Redis Lua scripts
# ---------------------------------------------------------------------------

RATE_LIMIT_LUA_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local max_requests = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, window)
local count = redis.call('ZCARD', key)

if count > max_requests then
    redis.call('ZREM', key, member)
    return 0
else
    return 1
end
"""

# Release-lock script only deletes the lock if the token still matches —
# prevents releasing a lock that expired and was re-acquired by someone else.
LOCK_RELEASE_LUA_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
else
    return 0
end
"""

QUEUE_ROUTING_LOCK_TTL_SECONDS = 5
QUEUE_ROUTING_LOCK_WAIT_SECONDS = 1.0
QUEUE_ROUTING_LOCK_POLL_INTERVAL_SECONDS = 0.05

# Messages replayed to a patient on WebSocket (re)connect (Issue 18 state_resume)
PATIENT_HISTORY_RESUME_LIMIT = 20

# Delay after agent registration before auto-route starts pulling from the
# queue, so the agent's WebSocket has time to fully establish (Issue 14).
AGENT_REGISTER_ROUTE_DELAY_SECONDS = 1.0

# ---------------------------------------------------------------------------
# Session / specialty context
# ---------------------------------------------------------------------------

SPECIALTY_PEDIATRICS = "pediatrics"
PATIENT_AGE_GERIATRIC = "geriatric"

# ---------------------------------------------------------------------------
# Emergency detection (default terms — override via Settings.emergency_terms)
# ---------------------------------------------------------------------------

EMERGENCY_TERMS: tuple[str, ...] = (
    "cardiac arrest",
    "chest pain",
    "stroke",
    "anaphylaxis",
    "overdose",
    "seizure",
    "unconscious",
    "not breathing",
    "hemorrhage",
    "severe allergic reaction",
    "myocardial infarction",
    "pulmonary embolism",
)

CLINICAL_DISCLAIMER = (
    "This information is for qualified healthcare professionals only and is intended "
    "as a clinical reference tool. It does not replace clinical judgment, patient history, "
    "or direct physician assessment. Always verify against current local guidelines before "
    "clinical application."
)

# ---------------------------------------------------------------------------
# Evaluation scripts
# ---------------------------------------------------------------------------

EVAL_API_URL = "http://127.0.0.1:8000/api/chat"

# ---------------------------------------------------------------------------
# Faithfulness fallback
# ---------------------------------------------------------------------------

FAITHFULNESS_JUDGE_FAILURE_VIOLATION = "Faithfulness judge call failed."

FAITHFULNESS_CHECK_FAILED_MESSAGE = (
    "Faithfulness check failed: this answer may contain claims "
    "not supported by the source documents."
)

# ---------------------------------------------------------------------------
# Grounded answer fallback
# ---------------------------------------------------------------------------

GROUNDED_ANSWER_NOT_FOUND = "I cannot find this in the provided documents."

# ---------------------------------------------------------------------------
# FAQ clustering
# ---------------------------------------------------------------------------

FAQ_SIMILARITY_THRESHOLD = 0.80
FAQ_MIN_QUESTION_CHARS = 12
FAQ_LIST_DEFAULT_LIMIT = 50
FAQ_LIST_MAX_LIMIT = 100
FAQ_STATUS_ACTIVE = "active"
