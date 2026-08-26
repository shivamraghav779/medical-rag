import type { HandoffState } from "./types";

export type WsHandler = (data: Record<string, unknown>) => void;

export class HandoffSocket {
  private ws: WebSocket | null = null;
  private closed = false;

  constructor(
    private readonly url: string,
    private readonly onMessage: WsHandler,
    private readonly onClose?: () => void,
  ) {}

  connect(): void {
    this.ws = new WebSocket(this.url);
    this.ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(String(ev.data)) as Record<string, unknown>;
        this.onMessage(data);
      } catch {
        // ignore malformed
      }
    };
    this.ws.onclose = () => {
      // Always drop the reference on close, not just on explicit close() —
      // otherwise a caller that checks truthiness instead of readyState
      // could see a stale closed socket and skip reconnecting. (Issue 17)
      this.ws = null;
      if (!this.closed) this.onClose?.();
    };
  }

  get ready(): boolean {
    return !!this.ws && this.ws.readyState === WebSocket.OPEN;
  }

  send(payload: Record<string, unknown>): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(payload));
    }
  }

  close(): void {
    this.closed = true;
    this.ws?.close();
    this.ws = null;
  }
}

export function patientWsUrl(
  apiUrl: string,
  sessionId: string,
  token: string,
): string {
  const base = apiUrl.replace(/^http/, "ws").replace(/\/$/, "");
  return `${base}/ws/chat/${sessionId}?token=${encodeURIComponent(token)}`;
}

export function describeState(
  state: HandoffState,
  position?: number,
  agentName?: string | null,
): string {
  if (state === "QUEUED") {
    return position != null
      ? `Waiting for a specialist · position ${position + 1}`
      : "Waiting for a specialist…";
  }
  if (state === "HUMAN_ACTIVE") {
    return agentName ? `Connected with ${agentName}` : "Connected to a specialist";
  }
  if (state === "RESOLVED") return "Conversation resolved — assistant is back";
  return "Clinical assistant online";
}
