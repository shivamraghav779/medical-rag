export interface WidgetConfig {
  apiUrl: string;
  apiKey: string;
  specialty?: string;
  clinicName: string;
  primaryColor: string;
  position: "bottom-right" | "bottom-left";
}

export type HandoffState =
  | "BOT_ACTIVE"
  | "HANDOFF_REQUESTED"
  | "QUEUED"
  | "HUMAN_ACTIVE"
  | "RESOLVED";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system" | "agent" | "event";
  content: string;
  /** Display name for human-agent bubbles (e.g. "Shivam Raghav"). */
  senderName?: string;
  citations?: Array<{ doc_name: string; page_number: number; text?: string }>;
  agentSteps?: Array<{ agent: string; status: string; output?: string }>;
  faithfulness?: { score: number; verdict: string };
}

export interface StreamEvent {
  type: string;
  [key: string]: unknown;
}
