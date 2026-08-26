import type { ClinicalContext } from "./api";

export interface User {
  id: string;
  email: string;
  full_name: string;
  role?: "user" | "agent" | "admin";
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  user: User;
}

export interface TokenUsageSummary {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  request_count: number;
}

export interface ConversationSummary {
  id: string;
  title: string;
  summary?: string;
  message_count: number;
  created_at: number;
  updated_at: number;
}

export interface ConversationDetail {
  id: string;
  title: string;
  summary?: string;
  summary_updated_at?: number;
  clinical_context?: ClinicalContext;
  messages: Array<{
    id: string;
    role: string;
    content: string;
    created_at: number;
    metadata?: Record<string, unknown>;
    feedback_rating?: string | null;
    feedback_comment?: string | null;
    feedback_correct_answer?: string | null;
  }>;
  created_at: number;
  updated_at: number;
  handoff_state?: string;
  handoff_reason?: string | null;
  assigned_agent_id?: string | null;
}
