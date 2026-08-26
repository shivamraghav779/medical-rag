export const DOC_TYPES = [
  { value: "clinical_guideline", label: "Clinical Guideline" },
  { value: "drug_monograph", label: "Drug Monograph" },
  { value: "treatment_protocol", label: "Treatment Protocol" },
  { value: "lab_reference", label: "Lab Reference" },
  { value: "diagnostic_criteria", label: "Diagnostic Criteria" },
  { value: "research_paper", label: "Research Paper" },
] as const;

export type DocType = (typeof DOC_TYPES)[number]["value"];

export interface DocumentInfo {
  doc_id: string;
  doc_name: string;
  chunk_count: number;
  doc_type?: string;
  source_org?: string;
  authority_level?: number;
  version?: string;
  publication_year?: number;
  upload_timestamp?: number;
  parse_method?: string;
}

export interface UploadResponse {
  doc_id: string;
  doc_name: string;
  chunk_count: number;
  success: boolean;
  message: string;
  parse_method?: string;
}

export interface RetrievedChunk {
  chunk_id: string;
  doc_name: string;
  page_number: number;
  text: string;
  score: number;
  rank: number;
  authority_level?: number;
  doc_type?: string;
  source_org?: string;
  publication_year?: number;
}

export interface DrugInteractionEvent {
  drug_a: string;
  drug_b: string;
  severity: string;
  description?: string;
  clinical_recommendation?: string;
  monitoring_parameters?: string[];
  source_doc_name?: string;
  source_authority_level?: number;
}

export interface FaithfulnessEvent {
  score: number;
  verdict: string;
  violations: string[];
}

export interface AgentStatusEvent {
  agent: string;
  status: "running" | "complete";
  output?: string;
}

export interface WebSearchResult {
  title: string;
  snippet: string;
  url: string;
  source: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "event";
  content: string;
  /** Override bubble label (e.g. human agent full name during handoff). */
  senderLabel?: string;
  citations?: RetrievedChunk[];
  webSources?: WebSearchResult[];
  faithfulness?: FaithfulnessEvent;
  drugInteraction?: DrugInteractionEvent;
  emergency?: { message: string; matched_terms: string[] };
  agentSteps?: AgentStatusEvent[];
  error?: string;
  /** Persisted DB id once the stream is saved (may differ from client id briefly). */
  serverId?: string;
  feedbackRating?: "up" | "down";
}

export type ChatEvent =
  | { type: "agent_status"; agent: string; status: string; output?: string }
  | { type: "emergency_warning"; message: string; matched_terms: string[] }
  | { type: "drug_interaction" } & DrugInteractionEvent
  | { type: "token"; content: string }
  | { type: "citations"; chunks: RetrievedChunk[] }
  | { type: "web_sources"; results: WebSearchResult[] }
  | { type: "faithfulness"; score: number; verdict: string; violations: string[] }
  | { type: "clinical_disclaimer"; message: string }
  | { type: "error"; message: string }
  | { type: "done" }
  | { type: "conversation"; conversation_id: string }
  | {
      type: "message_saved";
      conversation_id: string;
      user_message_id: string;
      assistant_message_id: string;
    }
  | {
      type: "handoff_initiated";
      reason: string;
      queue_position: number;
      state?: string;
      details?: Record<string, unknown>;
    }
  | { type: "queue_position"; position: number; state?: string };

export interface ClinicalContext {
  specialty?: string;
  patient_age_group?: "pediatric" | "adult" | "geriatric";
  patient_weight_kg?: number;
  doc_names?: string[];
}

export interface AnalyticsResponse {
  query_types: Record<string, number>;
  top_drugs: { drug: string; count: number }[];
  flagged_emergency: {
    session_id?: string;
    query?: string;
    matched_terms?: string[];
    timestamp?: number;
  }[];
  doc_type_queries: Record<string, number>;
  faithfulness_scores: number[];
  faithfulness_rolling_avg?: number;
}

export interface HealthResponse {
  status: "ok" | "degraded";
  redis: string;
  pinecone: string;
  model: string;
  embedding_model: string;
  rerank_model: string;
}

export interface FaqItem {
  id: string;
  canonical_question: string;
  ask_count: number;
  query_type?: string | null;
  last_asked_at: number;
  created_at: number;
}

export interface FaqListResponse {
  items: FaqItem[];
  total: number;
}

export interface QueueEntry {
  session_id: string;
  patient_username: string;
  wait_seconds: number;
  queue_position: number;
  reason?: string | null;
  last_query?: string | null;
  requested_at?: number | null;
}

export interface ActiveHandoffEntry {
  session_id: string;
  patient_username: string;
  agent_id?: string | null;
  agent_name?: string | null;
  reason?: string | null;
  last_query?: string | null;
  duration_seconds?: number | null;
  message_count?: number | null;
}

export interface UserListItem {
  id: string;
  email: string;
  full_name: string;
  role: string;
  is_active: boolean;
}

export interface QueueStateResponse {
  length: number;
  entries: QueueEntry[];
  active?: ActiveHandoffEntry[];
}

export interface AgentStatusItem {
  agent_id: string;
  status: string;
  last_freed_at?: number | null;
  current_session_id?: string | null;
  full_name?: string | null;
  active_count?: number;
  max_active?: number;
  active_sessions?: string[];
}

export type HandoffState =
  | "BOT_ACTIVE"
  | "HANDOFF_REQUESTED"
  | "QUEUED"
  | "HUMAN_ACTIVE"
  | "RESOLVED";

export interface ApiError {
  error: {
    code: string;
    message: string;
    is_retryable: boolean;
    request_id?: string;
    timestamp?: string;
  };
}
