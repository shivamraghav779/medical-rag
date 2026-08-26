import { emptyAssistant, requestHandoff, streamBotReply, uid } from "./chat";
import type { ChatMessage, HandoffState, WidgetConfig } from "./types";
import { createWidgetDom, type WidgetDom } from "./ui";
import { describeState, HandoffSocket, patientWsUrl } from "./websocket";

export class ClinicalChatWidget {
  private open = false;
  private messages: ChatMessage[] = [];
  private conversationId: string | null = null;
  private streaming = false;
  private state: HandoffState = "BOT_ACTIVE";
  private queuePosition = 0;
  private agentName: string | null = null;
  private socket: HandoffSocket | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private dom: WidgetDom;

  constructor(private readonly config: WidgetConfig) {
    this.dom = createWidgetDom(config);
    this.bind();
    this.render();
  }

  private bind(): void {
    this.dom.button.addEventListener("click", () => {
      this.open = !this.open;
      this.dom.panel.hidden = !this.open;
      this.dom.button.innerHTML = this.open
        ? `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg>`
        : `<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>`;
    });

    this.dom.sendBtn.addEventListener("click", () => void this.send());
    this.dom.input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        void this.send();
      }
    });

    window.addEventListener("message", (event) => {
      const data = event.data as { type?: string; config?: Partial<WidgetConfig> };
      if (data?.type === "clinical-rag-widget-config" && data.config) {
        if (data.config.clinicName) {
          this.config.clinicName = data.config.clinicName;
          this.dom.headerTitle.textContent = data.config.clinicName;
        }
        if (data.config.specialty) this.config.specialty = data.config.specialty;
        if (data.config.primaryColor) this.config.primaryColor = data.config.primaryColor;
      }
    });
  }

  private async send(): Promise<void> {
    const text = this.dom.input.value.trim();
    if (!text || this.streaming) return;
    this.dom.input.value = "";

    if (this.state === "HUMAN_ACTIVE") {
      this.messages.push({ id: uid(), role: "user", content: text });
      if (!this.socket?.ready) this.connectPatientSocket();
      this.socket?.send({ type: "message", content: text });
      this.render();
      return;
    }

    this.messages.push({ id: uid(), role: "user", content: text });
    const assistant = emptyAssistant();
    this.messages.push(assistant);
    this.streaming = true;
    this.render();

    try {
      this.conversationId = await streamBotReply(
        this.config,
        text,
        this.conversationId ?? "",
        (event) => this.onStreamEvent(assistant, event),
      );
    } catch (err) {
      assistant.content = err instanceof Error ? err.message : "Something went wrong.";
    } finally {
      this.streaming = false;
      this.render();
    }
  }

  private onStreamEvent(assistant: ChatMessage, event: Record<string, unknown>): void {
    const type = String(event.type || "");
    if (type === "conversation" && typeof event.conversation_id === "string") {
      this.conversationId = event.conversation_id;
    }
    if (type === "token") {
      assistant.content += String(event.content || "");
    }
    if (type === "agent_status") {
      assistant.agentSteps = assistant.agentSteps || [];
      const agent = String(event.agent || "");
      const status = String(event.status || "");
      const existing = assistant.agentSteps.find((s) => s.agent === agent);
      if (existing) existing.status = status;
      else assistant.agentSteps.push({ agent, status, output: event.output as string | undefined });
    }
    if (type === "citations" && Array.isArray(event.chunks)) {
      assistant.citations = (event.chunks as Array<Record<string, unknown>>).map((c) => ({
        doc_name: String(c.doc_name || "doc"),
        page_number: Number(c.page_number || 0),
        text: c.text ? String(c.text) : undefined,
      }));
    }
    if (type === "faithfulness") {
      // The main app already shows this; the widget silently dropped it
      // (no case for it here) despite the backend streaming it either way
      // (UX_AUDIT.md: widget/main-app faithfulness-badge parity).
      assistant.faithfulness = {
        score: Number(event.score || 0),
        verdict: String(event.verdict || "PASS"),
      };
    }
    if (type === "handoff_initiated") {
      this.state = "QUEUED";
      this.queuePosition = Number(event.queue_position || 0);
      this.messages.push({
        id: uid(),
        role: "event",
        content: "Connecting with a human specialist",
      });
      this.connectPatientSocket();
    }
    if (type === "queue_position") {
      this.queuePosition = Number(event.position || 0);
      this.state = (event.state as HandoffState) || this.state;
    }
    this.render();
  }

  private async handoff(): Promise<void> {
    if (!this.conversationId) return;
    try {
      const result = await requestHandoff(this.config, this.conversationId);
      this.state = (result.state as HandoffState) || "QUEUED";
      this.queuePosition = result.queue_position;
      this.messages.push({
        id: uid(),
        role: "event",
        content: "Connecting with a human specialist",
      });
      // Open socket first so we don't miss agent_connected if routing is instant.
      this.connectPatientSocket();
      this.render();
    } catch (err) {
      this.messages.push({
        id: uid(),
        role: "event",
        content: err instanceof Error ? err.message : "Handoff failed",
      });
      this.render();
    }
  }

  private connectPatientSocket(): void {
    if (!this.conversationId) return;
    if (this.socket?.ready) return;

    // Drop a dead/half-open socket so we can reconnect.
    if (this.socket) {
      this.socket.close();
      this.socket = null;
    }
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }

    const url = patientWsUrl(this.config.apiUrl, this.conversationId, this.config.apiKey);
    this.socket = new HandoffSocket(
      url,
      (data) => this.onSocketMessage(data),
      () => {
        this.socket = null;
        if (this.state === "QUEUED" || this.state === "HUMAN_ACTIVE") {
          this.reconnectTimer = setTimeout(() => this.connectPatientSocket(), 800);
        }
      },
    );
    this.socket.connect();
  }

  private onSocketMessage(data: Record<string, unknown>): void {
    const type = String(data.type || "");
    if (type === "state_resume") {
      // Unlike the React app, the widget has no REST call to reload prior
      // messages on init — without this, a patient who reloads mid-handoff
      // sees an empty thread even though the backend has full history.
      // Only seed once per page load (not on every reconnect within the
      // same session) to avoid duplicating already-rendered messages. (Issue 18)
      this.state = (data.state as HandoffState) || this.state;
      if (typeof data.agent_name === "string") this.agentName = data.agent_name;
      if (typeof data.queue_position === "number") this.queuePosition = data.queue_position;
      if (this.messages.length === 0 && Array.isArray(data.messages)) {
        for (const m of data.messages as Array<Record<string, unknown>>) {
          const role = String(m.role || "");
          const content = String(m.content || "");
          if (!content) continue;
          if (role === "user") {
            this.messages.push({ id: uid(), role: "user", content });
          } else if (role === "assistant") {
            this.messages.push({
              id: uid(),
              role: "agent",
              senderName: this.agentName || "Specialist",
              content,
            });
          }
        }
      }
    }
    if (type === "queue_position") {
      this.queuePosition = Number(data.position || 0);
      this.state = (data.state as HandoffState) || this.state;
    }
    if (type === "agent_connected") {
      this.state = "HUMAN_ACTIVE";
      const name = String(data.agent_name || "a specialist");
      this.agentName = name;
      const already = this.messages.some(
        (m) => m.role === "event" && m.content.toLowerCase().startsWith("connected with"),
      );
      if (!already) {
        this.messages.push({
          id: uid(),
          role: "event",
          content: `Connected with ${name}`,
        });
      }
    }
    if (type === "agent_message") {
      const name = String(data.agent_name || this.agentName || "Specialist");
      this.agentName = name;
      this.messages.push({
        id: uid(),
        role: "agent",
        senderName: name,
        content: String(data.content || ""),
      });
    }
    if (type === "conversation_resolved" || type === "agent_disconnected") {
      this.agentName = null;
      this.state = "BOT_ACTIVE";
      this.messages.push({
        id: uid(),
        role: "event",
        content:
          type === "conversation_resolved"
            ? "Conversation ended — assistant is back"
            : "Specialist disconnected — assistant is back",
      });
      this.socket?.close();
      this.socket = null;
    }
    this.render();
  }

  private render(): void {
    this.dom.statusEl.textContent = describeState(
      this.state,
      this.queuePosition,
      this.agentName,
    );
    this.dom.sendBtn.disabled = this.streaming;
    this.dom.thread.innerHTML = "";

    for (const msg of this.messages) {
      if (msg.role === "event" || msg.role === "system") {
        const wrap = document.createElement("div");
        wrap.className = "cr-event";
        wrap.setAttribute("role", "separator");
        const left = document.createElement("div");
        left.className = "cr-event-line";
        const label = document.createElement("span");
        label.className = "cr-event-label";
        label.textContent = msg.content;
        const right = document.createElement("div");
        right.className = "cr-event-line";
        wrap.append(left, label, right);
        this.dom.thread.appendChild(wrap);
        continue;
      }

      const wrap = document.createElement("div");
      wrap.className = `cr-msg ${msg.role === "user" ? "user" : msg.role}`;
      const bubble = document.createElement("div");
      bubble.className = "cr-bubble";

      if (msg.role === "agent" || msg.role === "assistant") {
        const who = document.createElement("div");
        who.className = "cr-sender";
        who.textContent =
          msg.role === "agent"
            ? msg.senderName || this.agentName || "Specialist"
            : "Assistant";
        bubble.appendChild(who);
      }

      const body = document.createElement("div");
      body.textContent = msg.content || (this.streaming && msg.role === "assistant" ? "…" : "");
      bubble.appendChild(body);
      wrap.appendChild(bubble);

      if (msg.agentSteps?.length) {
        const steps = document.createElement("div");
        steps.className = "cr-steps";
        for (const step of msg.agentSteps) {
          const chip = document.createElement("span");
          chip.className = "cr-chip";
          chip.textContent = `${step.agent}: ${step.status}`;
          steps.appendChild(chip);
        }
        wrap.appendChild(steps);
      }

      if (msg.citations?.length) {
        const cites = document.createElement("div");
        cites.className = "cr-cites";
        for (const c of msg.citations) {
          const chip = document.createElement("details");
          chip.className = "cr-cite";
          const summary = document.createElement("summary");
          summary.textContent = `${c.doc_name} p.${c.page_number}`;
          chip.appendChild(summary);
          if (c.text) {
            const bodyEl = document.createElement("div");
            bodyEl.textContent = c.text.slice(0, 240);
            chip.appendChild(bodyEl);
          }
          cites.appendChild(chip);
        }
        wrap.appendChild(cites);
      }

      if (msg.faithfulness) {
        const badge = document.createElement("span");
        badge.className = `cr-faith cr-faith--${msg.faithfulness.verdict.toLowerCase()}`;
        badge.textContent = `Faithfulness ${Math.round(msg.faithfulness.score * 100)}% · ${msg.faithfulness.verdict}`;
        badge.title = "How closely this answer matches the retrieved documents";
        wrap.appendChild(badge);
      }

      if (msg.role === "assistant" && msg.content && this.state === "BOT_ACTIVE") {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "cr-handoff";
        btn.textContent = "Connect to human";
        btn.addEventListener("click", () => void this.handoff());
        wrap.appendChild(btn);
      }

      this.dom.thread.appendChild(wrap);
    }
    this.dom.thread.scrollTop = this.dom.thread.scrollHeight;
  }
}
