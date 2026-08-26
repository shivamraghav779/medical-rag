import {
  ApiRequestError,
  dispatchAuthExpired,
  parseApiError,
  toApiError,
} from "./errors";
import { authHeaders, clearStoredToken } from "./auth";
import type {
  AnalyticsResponse,
  ChatEvent,
  ClinicalContext,
  DocumentInfo,
  FaqListResponse,
  HealthResponse,
  QueueStateResponse,
  AgentStatusItem,
  UploadResponse,
  UserListItem,
} from "../types/api";
import type {
  AuthResponse,
  ConversationDetail,
  ConversationSummary,
  TokenUsageSummary,
  User,
} from "../types/auth";

export { ApiRequestError, errorMessage, toApiError } from "./errors";

async function apiFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const headers = {
    ...authHeaders(),
    ...(init.headers as Record<string, string> | undefined),
  };

  let res: Response;
  try {
    res = await fetch(url, { ...init, headers });
  } catch (err) {
    throw toApiError(err);
  }

  if (res.status === 401 && !url.includes("/api/auth/login") && !url.includes("/api/auth/register")) {
    clearStoredToken();
    dispatchAuthExpired();
    throw await parseApiError(res);
  }

  return res;
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await apiFetch(url, init);
  if (!res.ok) throw await parseApiError(res);
  return res.json() as Promise<T>;
}

export async function fetchHealth(): Promise<HealthResponse> {
  const res = await fetch("/health");
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function login(email: string, password: string): Promise<AuthResponse> {
  const res = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function register(
  email: string,
  password: string,
  fullName: string,
): Promise<AuthResponse> {
  const res = await fetch("/api/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, full_name: fullName }),
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function fetchMe(): Promise<User> {
  return requestJson<User>("/api/auth/me");
}

export async function fetchTokenUsage(): Promise<TokenUsageSummary> {
  return requestJson<TokenUsageSummary>("/api/auth/usage");
}

export async function fetchConversations(): Promise<ConversationSummary[]> {
  return requestJson<ConversationSummary[]>("/api/conversations");
}

export async function createConversation(title = "New conversation"): Promise<ConversationSummary> {
  return requestJson<ConversationSummary>("/api/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
}

export async function fetchConversation(id: string): Promise<ConversationDetail> {
  return requestJson<ConversationDetail>(`/api/conversations/${id}`);
}

export async function deleteConversation(id: string): Promise<void> {
  const res = await apiFetch(`/api/conversations/${id}`, { method: "DELETE" });
  if (!res.ok) throw await parseApiError(res);
}

export async function submitMessageFeedback(
  conversationId: string,
  messageId: string,
  payload: {
    rating: "up" | "down";
    comment?: string;
    correct_answer?: string;
  },
): Promise<{
  success: boolean;
  message_id: string;
  rating: string;
  comment?: string | null;
  correct_answer?: string | null;
}> {
  return requestJson(`/api/conversations/${conversationId}/messages/${messageId}/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function fetchDocuments(): Promise<DocumentInfo[]> {
  return requestJson<DocumentInfo[]>("/api/documents");
}

export async function deleteDocument(docId: string): Promise<void> {
  const res = await apiFetch(`/api/documents/${docId}`, { method: "DELETE" });
  if (!res.ok) throw await parseApiError(res);
}

export async function fetchAnalytics(): Promise<AnalyticsResponse> {
  return requestJson<AnalyticsResponse>("/api/analytics");
}

export async function fetchFaqs(opts?: {
  limit?: number;
  offset?: number;
  sort?: "count" | "recent";
  query_type?: string;
}): Promise<FaqListResponse> {
  const params = new URLSearchParams();
  if (opts?.limit != null) params.set("limit", String(opts.limit));
  if (opts?.offset != null) params.set("offset", String(opts.offset));
  if (opts?.sort) params.set("sort", opts.sort);
  if (opts?.query_type) params.set("query_type", opts.query_type);
  const qs = params.toString();
  return requestJson<FaqListResponse>(`/api/faq${qs ? `?${qs}` : ""}`);
}

export async function requestHandoff(
  conversationId: string,
  reason = "patient_request",
): Promise<{ conversation_id: string; state: string; queue_position: number; reason: string }> {
  return requestJson("/api/handoff/request", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ conversation_id: conversationId, reason }),
  });
}

export async function cancelHandoff(
  conversationId: string,
): Promise<{ conversation_id: string; state: string }> {
  return requestJson(`/api/handoff/cancel/${conversationId}`, {
    method: "POST",
  });
}

export async function fetchHandoffQueue(): Promise<QueueStateResponse> {
  return requestJson<QueueStateResponse>("/api/handoff/queue");
}

export async function resolveHandoff(
  sessionId: string,
  payload: {
    tag: string;
    end_reason: string;
    issue_status: string;
    comments?: string;
  },
): Promise<{
  conversation_id: string;
  state: string;
  agent_id?: string | null;
  resolution?: Record<string, unknown> | null;
}> {
  return requestJson(`/api/handoff/resolve/${sessionId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function takeNextPatient(): Promise<{
  conversation_id?: string | null;
  assigned: boolean;
  message: string;
}> {
  return requestJson("/api/handoff/take-next", { method: "POST" });
}

export async function registerAgent(): Promise<{ agent_id: string; status: string; online: boolean }> {
  return requestJson("/api/agents/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
}

export async function fetchAgentStatuses(): Promise<{ agents: AgentStatusItem[] }> {
  return requestJson("/api/agents/status");
}

export async function fetchHandoffConversation(sessionId: string): Promise<ConversationDetail> {
  return requestJson<ConversationDetail>(`/api/handoff/conversation/${sessionId}`);
}

export async function fetchUsers(): Promise<{ users: UserListItem[] }> {
  return requestJson("/api/admin/users");
}

export async function promoteUser(
  userId: string,
  role: string,
): Promise<{ user_id: string; email: string; role: string }> {
  return requestJson("/api/admin/promote", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: userId, role }),
  });
}

export async function forceResolveHandoff(
  sessionId: string,
): Promise<{ success: boolean; session_id: string; agent_id?: string | null }> {
  return requestJson(`/api/handoff/force-resolve/${sessionId}`, { method: "DELETE" });
}

export async function enhanceQuery(query: string, specialty?: string): Promise<string> {
  const body = await requestJson<{ enhanced_query: string }>("/api/chat/tools/enhance-query", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, specialty }),
  });
  return body.enhanced_query;
}

export interface UploadMetadata {
  doc_type?: string;
  source_org?: string;
  authority_level?: number;
  version?: string;
  publication_year?: number;
  guideline_version?: string;
  issuing_body?: string;
  disease_area?: string;
  drug_generic_name?: string;
  drug_class?: string;
  atc_code?: string;
  condition_name?: string;
  criteria_system?: string;
}

export async function uploadDocument(
  file: File,
  metadata: UploadMetadata,
): Promise<UploadResponse> {
  const form = new FormData();
  form.append("file", file);
  Object.entries(metadata).forEach(([key, value]) => {
    if (value !== undefined && value !== "") {
      form.append(key, String(value));
    }
  });

  return requestJson<UploadResponse>("/api/upload", { method: "POST", body: form });
}

export interface StreamChatOptions {
  query: string;
  conversationId?: string;
  context?: ClinicalContext;
  enableWebSearch?: boolean;
  onEvent: (event: ChatEvent) => void;
  signal?: AbortSignal;
}

export async function streamChat({
  query,
  conversationId,
  context,
  enableWebSearch,
  onEvent,
  signal,
}: StreamChatOptions): Promise<string> {
  const body: Record<string, unknown> = { query };
  if (conversationId) body.conversation_id = conversationId;
  if (context?.specialty) body.specialty = context.specialty;
  if (context?.patient_age_group) body.patient_age_group = context.patient_age_group;
  if (context?.patient_weight_kg) body.patient_weight_kg = context.patient_weight_kg;
  if (context?.doc_names?.length) body.doc_names = context.doc_names;
  if (enableWebSearch) body.enable_web_search = true;

  let res: Response;
  try {
    res = await apiFetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (err) {
    throw toApiError(err);
  }

  if (!res.ok) throw await parseApiError(res);

  let resolvedConversationId = conversationId ?? "";
  const reader = res.body?.getReader();
  if (!reader) {
    throw new ApiRequestError("No response stream from chat service.", {
      code: "STREAM_ERROR",
      status: 502,
      isRetryable: true,
    });
  }

  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed.startsWith("data:")) continue;
        const payload = trimmed.slice(5).trim();
        if (!payload) continue;
        try {
          const event = JSON.parse(payload) as ChatEvent;
          if (event.type === "conversation") {
            resolvedConversationId = event.conversation_id;
          }
          onEvent(event);
        } catch {
          // skip malformed SSE lines
        }
      }
    }
  } catch (err) {
    if ((err as Error).name === "AbortError") throw err;
    throw toApiError(err);
  }

  return resolvedConversationId;
}
