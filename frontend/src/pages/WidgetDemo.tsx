import { Check, Copy } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { getStoredToken } from "../api/auth";

// Minimal line-based highlighter for this one fixed shape (<script ...
// attr="value" ...></script>) — not a general syntax highlighter, so no new
// dependency, just enough to make the embed snippet scannable instead of
// flat white-on-black text (UX_AUDIT.md: Widget demo).
function HighlightedEmbedCode({ code }: { code: string }) {
  const attrPattern = /^(\s*)([\w-]+)(=")([^"]*)(")$/;
  return (
    <>
      {code.split("\n").map((line, i) => {
        const match = line.match(attrPattern);
        if (match) {
          const [, indent, attr, eq, value, quote] = match;
          return (
            <div key={i}>
              {indent}
              <span className="text-sky-400">{attr}</span>
              <span className="text-slate-400">{eq}</span>
              <span className="text-amber-300">{value}</span>
              <span className="text-slate-400">{quote}</span>
            </div>
          );
        }
        return (
          <div key={i} className="text-fuchsia-400">
            {line}
          </div>
        );
      })}
    </>
  );
}

type WidgetCtor = new (config: {
  apiUrl: string;
  apiKey: string;
  specialty?: string;
  clinicName: string;
  primaryColor: string;
  position: "bottom-right" | "bottom-left";
}) => unknown;

declare global {
  interface Window {
    ClinicalRagChatbot?: { ClinicalChatWidget: WidgetCtor };
  }
}

function loadWidgetScript(src: string): Promise<void> {
  const existing = document.querySelector<HTMLScriptElement>(`script[src="${src}"]`);
  if (existing) {
    return existing.dataset.loaded === "1"
      ? Promise.resolve()
      : new Promise((resolve, reject) => {
          existing.addEventListener("load", () => resolve());
          existing.addEventListener("error", () => reject(new Error("Widget script failed")));
        });
  }
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = src;
    script.async = true;
    script.onload = () => {
      script.dataset.loaded = "1";
      resolve();
    };
    script.onerror = () => reject(new Error("Widget script failed"));
    document.body.appendChild(script);
  });
}

export default function WidgetDemo() {
  const [clinicName, setClinicName] = useState("Riverside Family Clinic");
  const [specialty, setSpecialty] = useState("primary_care");
  const [primaryColor, setPrimaryColor] = useState("#0f766e");
  const [widgetError, setWidgetError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const token = getStoredToken() || "";
  const apiUrl = window.location.origin;

  const embedCode = useMemo(
    () =>
      `<script
  src="${apiUrl}/widget/chatbot.js"
  data-api-url="${apiUrl}"
  data-api-key="${token || "PASTE_YOUR_JWT_HERE"}"
  data-clinic-name="${clinicName}"
  data-specialty="${specialty}"
  data-primary-color="${primaryColor}"
  data-position="bottom-right"
></script>`,
    [apiUrl, token, clinicName, specialty, primaryColor],
  );

  const pushConfig = () => {
    window.postMessage(
      {
        type: "clinical-rag-widget-config",
        config: { clinicName, specialty, primaryColor },
      },
      "*",
    );
  };

  useEffect(() => {
    let cancelled = false;

    const mount = async () => {
      setWidgetError(null);
      if (!token) {
        setWidgetError("Log in to mount the live widget (needs your JWT).");
        return;
      }
      try {
        await loadWidgetScript(`${apiUrl}/widget/chatbot.js`);
        if (cancelled) return;
        document.getElementById("clinical-rag-chatbot-host")?.remove();
        const Ctor = window.ClinicalRagChatbot?.ClinicalChatWidget;
        if (!Ctor) {
          setWidgetError("Widget bundle loaded but ClinicalChatWidget is missing.");
          return;
        }
        new Ctor({
          apiUrl,
          apiKey: token,
          clinicName,
          specialty,
          primaryColor,
          position: "bottom-right",
        });
      } catch (err) {
        if (!cancelled) {
          setWidgetError(err instanceof Error ? err.message : "Failed to mount widget");
        }
      }
    };

    void mount();
    return () => {
      cancelled = true;
      document.getElementById("clinical-rag-chatbot-host")?.remove();
    };
    // Mount once with session token; live config updates go via postMessage.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiUrl, token]);

  useEffect(() => {
    pushConfig();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clinicName, specialty, primaryColor]);

  return (
    <div className="flex h-full flex-col overflow-auto">
      <header className="border-b border-oky-border/40 px-8 py-6">
        <h1 className="text-2xl font-bold text-oky-text">Embeddable widget demo</h1>
        <p className="mt-1 text-sm text-oky-muted">
          Fake clinic page with a live chatbot bubble (bottom-right). Same script hospitals embed.
        </p>
        {widgetError ? (
          <p className="mt-2 text-sm text-red-600">{widgetError}</p>
        ) : (
          <p className="mt-2 text-sm text-teal-700">
            Live widget mounted — open the chat bubble at the bottom-right of the screen.
          </p>
        )}
      </header>

      <div className="mx-auto grid w-full max-w-6xl gap-6 p-6 lg:grid-cols-2">
        <div
          className="relative min-h-[480px] overflow-hidden rounded-3xl border border-oky-border/50 shadow-sm"
          style={{
            background:
              "linear-gradient(160deg, #ecfeff 0%, #f0fdfa 40%, #fff 100%)",
          }}
        >
          <div className="flex items-center justify-between border-b border-teal-900/10 px-6 py-4">
            <div className="text-lg font-semibold text-teal-900">{clinicName}</div>
            <nav className="flex gap-4 text-sm text-teal-800/70">
              <span>Care</span>
              <span>Providers</span>
              <span>Contact</span>
            </nav>
          </div>
          <div className="space-y-3 px-6 py-8 text-teal-950">
            <h2 className="text-3xl font-semibold tracking-tight">Your health, closer to home.</h2>
            <p className="max-w-md text-sm text-teal-900/70">
              Ask our clinical assistant about guidelines, medications, and lab values.
              Specialists are one handoff away when you need a human.
            </p>
            <div className="rounded-2xl bg-white/70 p-4 text-sm text-teal-900/80 shadow-sm">
              Open hours · Mon–Fri 8am–6pm · Same-day telehealth available
            </div>
          </div>
        </div>

        <div className="space-y-4">
          <div className="card space-y-3 p-5">
            <h3 className="font-semibold">Live configuration</h3>
            <label className="block text-sm">
              Clinic name
              <input
                className="input-field mt-1 w-full"
                value={clinicName}
                onChange={(e) => setClinicName(e.target.value)}
                onBlur={pushConfig}
              />
            </label>
            <label className="block text-sm">
              Specialty
              <input
                className="input-field mt-1 w-full"
                value={specialty}
                onChange={(e) => setSpecialty(e.target.value)}
                onBlur={pushConfig}
              />
            </label>
            <label className="block text-sm">
              Primary color
              <input
                type="color"
                className="mt-1 h-10 w-full cursor-pointer rounded-lg border border-oky-border/60"
                value={primaryColor}
                onChange={(e) => {
                  setPrimaryColor(e.target.value);
                  window.postMessage(
                    {
                      type: "clinical-rag-widget-config",
                      config: { clinicName, specialty, primaryColor: e.target.value },
                    },
                    "*",
                  );
                }}
              />
            </label>
          </div>

          <div className="card p-5">
            <div className="mb-2 flex items-center justify-between">
              <h3 className="font-semibold">Embed code</h3>
              <button
                type="button"
                onClick={() => {
                  navigator.clipboard.writeText(embedCode).then(() => {
                    setCopied(true);
                    window.setTimeout(() => setCopied(false), 1500);
                  });
                }}
                className="inline-flex items-center gap-1.5 rounded-lg border border-oky-border/60 bg-white px-2.5 py-1 text-xs font-medium text-oky-text-secondary transition hover:bg-oky-purple/5"
              >
                {copied ? (
                  <>
                    <Check className="h-3.5 w-3.5 text-success-500" /> Copied
                  </>
                ) : (
                  <>
                    <Copy className="h-3.5 w-3.5" /> Copy
                  </>
                )}
              </button>
            </div>
            <pre className="overflow-auto rounded-xl bg-slate-950 p-4 font-mono text-xs leading-relaxed">
              <HighlightedEmbedCode code={embedCode} />
            </pre>
            <p className="mt-3 text-xs text-oky-muted">
              This page already injects the widget with your session JWT. Copy the snippet above
              for other sites. Token length: {token.length || 0}.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
