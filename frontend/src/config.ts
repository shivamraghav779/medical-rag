/** Runtime URLs for API + widget (empty = same-origin / Vite proxy). */

const trimSlash = (value: string) => value.replace(/\/$/, "");

/** Backend origin, e.g. https://medical-rag-backend-iota.vercel.app */
export const API_BASE_URL = trimSlash(
  (import.meta.env.VITE_API_URL as string | undefined)?.trim() || "",
);

/**
 * Origin that serves /widget/chatbot.js.
 * Defaults to the API origin (FastAPI StaticFiles mount).
 */
export const WIDGET_BASE_URL = trimSlash(
  (import.meta.env.VITE_WIDGET_URL as string | undefined)?.trim() || API_BASE_URL,
);

/** Prefix a path like `/api/chat` with the configured API base. */
export function apiUrl(path: string): string {
  const normalized = path.startsWith("/") ? path : `/${path}`;
  return `${API_BASE_URL}${normalized}`;
}

/** Build a WebSocket URL against the API host (ws/wss). */
export function wsUrl(path: string): string {
  const normalized = path.startsWith("/") ? path : `/${path}`;
  const httpBase = API_BASE_URL || window.location.origin;
  const url = new URL(normalized, httpBase.endsWith("/") ? httpBase : `${httpBase}/`);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

export function widgetScriptUrl(): string {
  const base = WIDGET_BASE_URL || window.location.origin;
  return `${base}/widget/chatbot.js`;
}
