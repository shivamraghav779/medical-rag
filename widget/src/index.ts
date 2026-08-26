import type { WidgetConfig } from "./types";
import { ClinicalChatWidget } from "./widget";

function readConfig(script: HTMLScriptElement): WidgetConfig {
  const rawKey = script.dataset.apiKey || script.getAttribute("data-api-key") || "";
  return {
    apiUrl: (script.dataset.apiUrl || window.location.origin).trim(),
    // Strip BOM / zero-width / ellipsis that break Fetch Authorization headers
    apiKey: rawKey
      .replace(/^\uFEFF/, "")
      .replace(/[\u200B-\u200D\uFEFF]/g, "")
      .replace(/\u2026/g, "")
      .trim(),
    specialty: script.dataset.specialty || undefined,
    clinicName: script.dataset.clinicName || "Clinical Assistant",
    primaryColor: script.dataset.primaryColor || "#7c3aed",
    position: script.dataset.position === "bottom-left" ? "bottom-left" : "bottom-right",
  };
}

function boot(): void {
  const script =
    document.currentScript instanceof HTMLScriptElement
      ? document.currentScript
      : (document.querySelector("script[data-api-url]") as HTMLScriptElement | null);
  if (!script) return;
  const config = readConfig(script);
  // eslint-disable-next-line no-new
  new ClinicalChatWidget(config);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}

export { ClinicalChatWidget };
export type { WidgetConfig };
