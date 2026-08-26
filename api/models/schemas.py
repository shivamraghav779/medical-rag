from pydantic import BaseModel, Field, field_validator
from typing import Any, Optional, List, Dict


class Chunk(BaseModel):
    chunk_id: str
    doc_name: str
    page_number: int
    char_start: int
    char_end: int
    text: str
    doc_type: Optional[str] = None
    source_org: Optional[str] = None
    authority_level: int = 1
    version: Optional[str] = None
    publication_year: Optional[int] = None


class AgentStep(BaseModel):
    name: str
    status: str
    output: Optional[str] = None
    duration_ms: Optional[float] = None


class RetrievedChunk(BaseModel):
    chunk_id: str
    doc_name: str
    page_number: int
    text: str
    score: float
    rank: int
    authority_level: int = 1
    doc_type: Optional[str] = None
    source_org: Optional[str] = None
    publication_year: Optional[int] = None


class ChatRequest(BaseModel):
    query: str
    conversation_id: Optional[str] = None
    doc_names: Optional[List[str]] = None
    specialty: Optional[str] = None
    patient_age_group: Optional[str] = None
    patient_weight_kg: Optional[float] = None
    enable_web_search: bool = False


class EnhanceQueryRequest(BaseModel):
    query: str
    specialty: Optional[str] = None


class EnhanceQueryResponse(BaseModel):
    enhanced_query: str


class WebSearchResult(BaseModel):
    title: str
    snippet: str
    url: str
    source: str = "pubmed"


class WebSearchRequest(BaseModel):
    query: str
    max_results: int = 5


class WebSearchResponse(BaseModel):
    results: List[WebSearchResult]


class UserRegisterRequest(BaseModel):
    email: str
    password: str
    full_name: str = ""
    # Accepted for forward-compat with an invite-code flow, but the register
    # endpoint always forces role="user" — agent/admin can only be granted via
    # POST /api/admin/promote by an existing admin. See AuthService.register.
    role: str = "user"


class PromoteUserRequest(BaseModel):
    user_id: str
    role: str


class PromoteUserResponse(BaseModel):
    user_id: str
    email: str
    role: str


class UserListItem(BaseModel):
    id: str
    email: str
    full_name: str
    role: str
    is_active: bool


class UserListResponse(BaseModel):
    users: list[UserListItem]


class ForceResolveResponse(BaseModel):
    success: bool
    session_id: str
    agent_id: Optional[str] = None


class UserLoginRequest(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    id: str
    email: str
    full_name: str
    role: str = "user"


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class TokenUsageSummary(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    request_count: int


class MessageResponse(BaseModel):
    id: str
    role: str
    content: str
    created_at: float
    metadata: Optional[Dict[str, Any]] = None
    feedback_rating: Optional[str] = None
    feedback_comment: Optional[str] = None
    feedback_correct_answer: Optional[str] = None


class MessageFeedbackRequest(BaseModel):
    rating: str = Field(..., description='"up" or "down"')
    comment: Optional[str] = None
    correct_answer: Optional[str] = None


class MessageFeedbackResponse(BaseModel):
    success: bool
    message_id: str
    rating: str
    comment: Optional[str] = None
    correct_answer: Optional[str] = None


class ConversationSummary(BaseModel):
    id: str
    title: str
    summary: Optional[str] = None
    message_count: int = 0
    created_at: float
    updated_at: float


class ConversationDetail(BaseModel):
    id: str
    title: str
    summary: Optional[str] = None
    summary_updated_at: Optional[float] = None
    clinical_context: Optional[Dict[str, Any]] = None
    messages: List[MessageResponse] = Field(default_factory=list)
    created_at: float
    updated_at: float
    handoff_state: str = "BOT_ACTIVE"
    handoff_reason: Optional[str] = None
    assigned_agent_id: Optional[str] = None


class ConversationCreateRequest(BaseModel):
    title: str = "New conversation"


class ConversationUpdateRequest(BaseModel):
    title: Optional[str] = None


class UploadResponse(BaseModel):
    doc_id: str
    doc_name: str
    chunk_count: int
    success: bool
    message: str
    parse_method: Optional[str] = None


class DocumentInfo(BaseModel):
    doc_id: str
    doc_name: str
    chunk_count: int
    doc_type: Optional[str] = None
    source_org: Optional[str] = None
    authority_level: Optional[int] = None
    version: Optional[str] = None
    publication_year: Optional[int] = None
    upload_timestamp: Optional[float] = None
    parse_method: Optional[str] = None

    @field_validator("version", mode="before")
    @classmethod
    def _coerce_version(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        return str(value)


class FaithfulnessResult(BaseModel):
    score: float
    verdict: str
    violations: List[str]


class ConversationMessage(BaseModel):
    role: str
    content: str
    timestamp: float


class SessionInfo(BaseModel):
    session_id: str
    history: List[ConversationMessage]


class QueryAnalysis(BaseModel):
    intent: str
    expanded_queries: List[str]
    requires_retrieval: bool = True
    doc_filter: Optional[List[str]] = None
    query_type: Optional[str] = None
    doc_type_filter: Optional[List[str]] = None
    extracted_drug_names: List[str] = Field(default_factory=list)


class AgentResult(BaseModel):
    """Uniform agent return envelope used by BaseAgent subclasses."""

    success: bool = True
    output: Optional[str] = None
    data: Any = None
    duration_ms: Optional[float] = None
    is_emergency: bool = False
    matched_terms: List[str] = Field(default_factory=list)
    not_applicable: bool = False
    severity: Optional[str] = None
    message: Optional[str] = None


class DrugInfo(BaseModel):
    drug_name: str
    info: Dict[str, Any] = Field(default_factory=dict)


class InteractionResult(BaseModel):
    drug_a: str
    drug_b: str
    severity: str
    description: str = ""
    recommendation: str = ""
    clinical_recommendation: str = ""
    monitoring_parameters: List[str] = Field(default_factory=list)
    source_doc_name: Optional[str] = None
    source_authority_level: Optional[int] = None


class SessionContext(BaseModel):
    specialty: Optional[str] = None
    patient_age_group: Optional[str] = None
    patient_weight_kg: Optional[float] = None
    disclaimer_shown: bool = False
    query_count: int = 0
    last_query_type: Optional[str] = None


class FaqItem(BaseModel):
    id: str
    canonical_question: str
    ask_count: int
    query_type: Optional[str] = None
    last_asked_at: float
    created_at: float
    matched: bool = False
    similarity: Optional[float] = None


class FaqListResponse(BaseModel):
    items: List[FaqItem] = Field(default_factory=list)
    total: int = 0


class FaqObserveResponse(BaseModel):
    faq_id: str
    matched: bool
    similarity: Optional[float] = None
    ask_count: int
    canonical_question: str


# ---------------------------------------------------------------------------
# Human handoff
# ---------------------------------------------------------------------------

class HandoffRequestBody(BaseModel):
    conversation_id: str
    reason: Optional[str] = "patient_request"


class HandoffRequestResponse(BaseModel):
    conversation_id: str
    state: str
    queue_position: int
    reason: str


class CancelHandoffResponse(BaseModel):
    conversation_id: str
    state: str


class QueueEntry(BaseModel):
    session_id: str
    patient_username: str
    wait_seconds: int
    queue_position: int
    reason: Optional[str] = None
    last_query: Optional[str] = None
    requested_at: Optional[float] = None


class ActiveHandoffEntry(BaseModel):
    session_id: str
    patient_username: str
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None
    reason: Optional[str] = None
    last_query: Optional[str] = None
    duration_seconds: Optional[int] = None
    message_count: Optional[int] = None


class QueueStateResponse(BaseModel):
    length: int
    entries: List[QueueEntry] = Field(default_factory=list)
    active: List[ActiveHandoffEntry] = Field(default_factory=list)


class ResolveHandoffRequest(BaseModel):
    """Agent disposition when closing a human handoff."""

    # What the conversation was about
    tag: str = Field(
        ...,
        description="Issue category tag",
        examples=["medication", "labs", "guidelines", "symptoms", "technical", "other"],
    )
    # Why the chat ended
    end_reason: str = Field(
        ...,
        description="How the chat ended",
        examples=["issue_resolved", "patient_ended", "patient_inactive", "unresolved", "transferred"],
    )
    # Whether the clinical/support issue itself was resolved
    issue_status: str = Field(
        ...,
        description="Issue outcome",
        examples=["resolved", "not_resolved", "partial"],
    )
    comments: Optional[str] = None


class ResolveHandoffResponse(BaseModel):
    conversation_id: str
    state: str
    agent_id: Optional[str] = None
    resolution: Optional[dict] = None


class AgentRegisterRequest(BaseModel):
    agent_id: Optional[str] = None


class AgentRegisterResponse(BaseModel):
    agent_id: str
    status: str
    online: bool = True


class AgentStatusItem(BaseModel):
    agent_id: str
    status: str
    last_freed_at: Optional[float] = None
    current_session_id: Optional[str] = None
    full_name: Optional[str] = None
    active_count: int = 0
    max_active: int = 5
    active_sessions: List[str] = Field(default_factory=list)


class AgentStatusListResponse(BaseModel):
    agents: List[AgentStatusItem] = Field(default_factory=list)


class TakeNextPatientResponse(BaseModel):
    conversation_id: Optional[str] = None
    assigned: bool = False
    message: str = ""
