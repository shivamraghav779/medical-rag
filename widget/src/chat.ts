import type { ChatMessage, StreamEvent, WidgetConfig } from "./types";

/** HTTP headers must be ISO-8859-1; strip anything else (smart quotes, ellipsis, etc.). */
function sanitizeHeaderValue(value: string): string {
  return (value || "")
    .replace(/[\u2018\u2019]/g, "'")
    .replace(/[\u201C\u201D]/g, '"')
    .replace(/\u2026/g, "...")
    .replace(/[^\x09\x20-\x7E]/g, "")
    .trim();
}

function authHeaders(config: WidgetConfig): Record<string, string> {
  const raw = sanitizeHeaderValue(config.apiKey);
  // Accept either a bare JWT or "Bearer <jwt>"
  const token = raw.replace(/^Bearer\s+/i, "").trim();
  if (!token) {
    throw new Error("Missing data-api-key. Use a full JWT from login (ASCII only).");
  }
  if (/[^\x20-\x7E]/.test(token)) {
    throw new Error("data-api-key contains invalid characters. Paste the raw JWT token.");
  }
  return {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
  };
}

export async function ensureConversation(
  config: WidgetConfig,
  conversationId: string | null,
): Promise<string> {
  if (conversationId) return conversationId;
  const res = await fetch(`${config.apiUrl.replace(/\/$/, "")}/api/conversations`, {
    method: "POST",
    headers: authHeaders(config),
    body: JSON.stringify({}),
  });
  if (!res.ok) throw new Error("Failed to create conversation");
  const data = (await res.json()) as { id: string };
  return data.id;
}

export async function streamBotReply(
  config: WidgetConfig,
  query: string,
  conversationId: string,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<string> {
  const body: Record<string, unknown> = { query };
  if (conversationId) body.conversation_id = conversationId;
  if (config.specialty) body.specialty = config.specialty;

  const res = await fetch(`${config.apiUrl.replace(/\/$/, "")}/api/chat`, {
    method: "POST",
    headers: authHeaders(config),
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) throw new Error("Chat request failed");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let resolvedId = conversationId;

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
        const event = JSON.parse(payload) as StreamEvent;
        if (event.type === "conversation" && typeof event.conversation_id === "string") {
          resolvedId = event.conversation_id;
        }
        onEvent(event);
      } catch {
        // skip
      }
    }
  }
  return resolvedId;
}

export async function requestHandoff(
  config: WidgetConfig,
  conversationId: string,
): Promise<{ queue_position: number; state: string; reason: string }> {
  const res = await fetch(`${config.apiUrl.replace(/\/$/, "")}/api/handoff/request`, {
    method: "POST",
    headers: authHeaders(config),
    body: JSON.stringify({ conversation_id: conversationId, reason: "patient_request" }),
  });
  if (!res.ok) throw new Error("Handoff request failed");
  return res.json();
}

export function uid(): string {
  return crypto.randomUUID();
}

export function emptyAssistant(): ChatMessage {
  return { id: uid(), role: "assistant", content: "", agentSteps: [] };
}
