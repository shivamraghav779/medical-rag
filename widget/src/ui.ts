import type { WidgetConfig } from "./types";

export interface WidgetDom {
  host: HTMLElement;
  shadow: ShadowRoot;
  button: HTMLButtonElement;
  panel: HTMLDivElement;
  thread: HTMLDivElement;
  input: HTMLTextAreaElement;
  sendBtn: HTMLButtonElement;
  statusEl: HTMLDivElement;
  headerTitle: HTMLElement;
}

export function createWidgetDom(config: WidgetConfig): WidgetDom {
  const host = document.createElement("div");
  host.id = "clinical-rag-chatbot-host";
  host.style.all = "initial";
  host.style.position = "fixed";
  host.style.zIndex = "2147483000";
  host.style.bottom = "20px";
  host.style[config.position === "bottom-left" ? "left" : "right"] = "20px";

  const shadow = host.attachShadow({ mode: "open" });
  const style = document.createElement("style");
  style.textContent = css(config.primaryColor);
  shadow.appendChild(style);

  const root = document.createElement("div");
  root.className = "cr-root";

  const button = document.createElement("button");
  button.className = "cr-fab";
  button.type = "button";
  button.setAttribute("aria-label", "Open chat");
  button.innerHTML = chatIcon();

  const panel = document.createElement("div");
  panel.className = "cr-panel";
  panel.hidden = true;

  panel.innerHTML = `
    <div class="cr-header">
      <div>
        <div class="cr-title"></div>
        <div class="cr-status"></div>
      </div>
      <button type="button" class="cr-close" aria-label="Close">${xIcon()}</button>
    </div>
    <div class="cr-thread"></div>
    <div class="cr-composer">
      <textarea rows="1" placeholder="Ask a clinical question..."></textarea>
      <button type="button" class="cr-send" aria-label="Send">${sendIcon()}</button>
    </div>
  `;

  root.appendChild(panel);
  root.appendChild(button);
  shadow.appendChild(root);
  document.body.appendChild(host);

  const headerTitle = panel.querySelector(".cr-title") as HTMLElement;
  headerTitle.textContent = config.clinicName;
  const statusEl = panel.querySelector(".cr-status") as HTMLDivElement;
  const thread = panel.querySelector(".cr-thread") as HTMLDivElement;
  const input = panel.querySelector("textarea") as HTMLTextAreaElement;
  const sendBtn = panel.querySelector(".cr-send") as HTMLButtonElement;
  const closeBtn = panel.querySelector(".cr-close") as HTMLButtonElement;

  closeBtn.addEventListener("click", () => {
    button.click();
  });

  return { host, shadow, button, panel, thread, input, sendBtn, statusEl, headerTitle };
}

function css(primary: string): string {
  return `
    :host { all: initial; }
    * { box-sizing: border-box; font-family: Inter, system-ui, sans-serif; }
    .cr-root { position: relative; }
    .cr-fab {
      width: 56px; height: 56px; border-radius: 50%; border: none; cursor: pointer;
      background: ${primary}; color: #fff; box-shadow: 0 8px 24px rgba(0,0,0,.25);
      display: grid; place-items: center;
    }
    .cr-panel {
      position: absolute; bottom: 72px; right: 0; width: 380px; height: 560px;
      background: #fff; border-radius: 18px; overflow: hidden;
      box-shadow: 0 16px 48px rgba(15,23,42,.28);
      display: flex; flex-direction: column;
      transform-origin: bottom right;
      animation: cr-slide .2s ease-out;
    }
    @keyframes cr-slide { from { opacity: 0; transform: translateY(12px) scale(.98);} to { opacity:1; transform:none;} }
    .cr-header {
      background: ${primary}; color: #fff; padding: 14px 16px;
      display: flex; justify-content: space-between; align-items: flex-start;
    }
    .cr-title { font-weight: 700; font-size: 15px; }
    .cr-status { font-size: 12px; opacity: .9; margin-top: 4px; min-height: 16px; }
    .cr-close { background: transparent; border: none; color: #fff; cursor: pointer; }
    .cr-thread { flex: 1; overflow: auto; padding: 14px; background: #f8fafc; }
    .cr-msg { margin-bottom: 12px; max-width: 92%; }
    .cr-msg.user { margin-left: auto; }
    .cr-bubble {
      padding: 10px 12px; border-radius: 14px; font-size: 13.5px; line-height: 1.45;
      white-space: pre-wrap; word-break: break-word;
    }
    .cr-sender {
      font-size: 10px; font-weight: 700; letter-spacing: .04em; text-transform: uppercase;
      opacity: .7; margin-bottom: 4px;
    }
    .cr-msg.user .cr-bubble { background: ${primary}; color: #fff; border-bottom-right-radius: 4px; }
    .cr-msg.assistant .cr-bubble, .cr-msg.agent .cr-bubble, .cr-msg.system .cr-bubble {
      background: #fff; color: #0f172a; border: 1px solid #e2e8f0; border-bottom-left-radius: 4px;
    }
    /* Human-agent messages get their own tinted background + accent border,
       not just the bot bubble with a thin colored edge — a patient should
       tell "this is a person" apart from "this is the bot" at a glance
       (UX_AUDIT.md: widget message bubbles). Deliberately not ${primary}
       (the clinic's own brand color, already used for the patient's own
       messages) and not green (reserved for success/positive elsewhere) —
       a dedicated violet accent, same role as the main app's "agent" token. */
    .cr-msg.agent .cr-bubble { background: #f5f3ff; border-color: #c4b5fd; }
    .cr-msg.agent .cr-sender { color: #6d28d9; opacity: 1; }
    .cr-event {
      display: flex; align-items: center; gap: 10px;
      max-width: 100%; margin: 14px 0;
    }
    .cr-event-line { flex: 1; height: 1px; background: #cbd5e1; }
    .cr-event-label {
      flex-shrink: 0; font-size: 10px; font-weight: 600; letter-spacing: .04em;
      text-transform: uppercase; color: #64748b; text-align: center;
    }
    .cr-steps { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
    /* Neutral slate, not violet — that accent is reserved for "a human
       agent is here" (see .cr-msg.agent above); these chips are the bot's
       own retrieval pipeline and shouldn't borrow the human signal. */
    .cr-chip {
      font-size: 11px; padding: 2px 8px; border-radius: 999px; background: #f1f5f9; color: #475569;
    }
    .cr-faith {
      display: inline-block; margin-top: 6px; font-size: 11px; font-weight: 600;
      padding: 2px 8px; border-radius: 999px;
    }
    .cr-faith--pass { background: #f0fdf4; color: #15803d; }
    .cr-faith--warn { background: #fffbeb; color: #b45309; }
    .cr-faith--fail { background: #fef2f2; color: #b91c1c; }
    .cr-cites { margin-top: 6px; display: flex; flex-wrap: wrap; gap: 4px; }
    .cr-cite {
      font-size: 11px; border: 1px solid #cbd5e1; border-radius: 8px; padding: 2px 6px;
      background: #fff; color: #334155; cursor: pointer;
    }
    .cr-handoff {
      margin-top: 6px; border: none; background: transparent; color: ${primary};
      font-size: 12px; cursor: pointer; text-decoration: underline; padding: 0;
    }
    .cr-composer {
      display: flex; gap: 8px; padding: 10px; border-top: 1px solid #e2e8f0; background: #fff;
    }
    .cr-composer textarea {
      flex: 1; resize: none; border: 1px solid #cbd5e1; border-radius: 12px;
      padding: 10px 12px; font-size: 13px; max-height: 90px; outline: none;
    }
    .cr-send {
      width: 40px; height: 40px; border: none; border-radius: 12px; background: ${primary};
      color: #fff; cursor: pointer; display: grid; place-items: center;
    }
    .cr-send:disabled { opacity: .5; cursor: default; }
  `;
}

function chatIcon() {
  return `<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>`;
}
function xIcon() {
  return `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg>`;
}
function sendIcon() {
  return `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/></svg>`;
}
