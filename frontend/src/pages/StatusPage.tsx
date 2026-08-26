import { useCallback, useEffect, useState } from "react";
import {
  CheckCircle2,
  ExternalLink,
  RefreshCw,
  XCircle,
} from "lucide-react";
import { fetchHealth, fetchTokenUsage } from "../api/client";
import ErrorBanner from "../components/ui/ErrorBanner";
import type { HealthResponse } from "../types/api";
import type { TokenUsageSummary } from "../types/auth";
import { useAuth } from "../context/AuthContext";

function StatusRow({
  label,
  value,
  ok,
}: {
  label: string;
  value: string;
  ok: boolean;
}) {
  return (
    <div className="flex items-center justify-between rounded-lg bg-oky-purple/5 px-4 py-3">
      <span className="text-sm text-oky-muted">{label}</span>
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium">{value}</span>
        {ok ? (
          <CheckCircle2 className="h-4 w-4 text-green-400" />
        ) : (
          <XCircle className="h-4 w-4 text-red-400" />
        )}
      </div>
    </div>
  );
}

export default function StatusPage() {
  const { user } = useAuth();
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [usage, setUsage] = useState<TokenUsageSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [h, u] = await Promise.all([fetchHealth(), fetchTokenUsage()]);
      setHealth(h);
      setUsage(u);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(load, 30_000);
    return () => clearInterval(interval);
  }, [load]);

  const overallOk = health?.status === "ok";

  return (
    <div className="flex h-full flex-col">
      <header className="flex shrink-0 items-center justify-between border-b border-oky-border/40 px-8 py-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-oky-text">System Status</h1>
          <p className="mt-1 text-sm text-oky-muted">
            Service health, dependencies, and your token usage.
          </p>
        </div>
        <button type="button" onClick={load} className="btn-ghost" title="Refresh" aria-label="Refresh system status">
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} aria-hidden="true" />
        </button>
      </header>

      <div className="flex-1 overflow-auto p-6">
        <div className="mx-auto max-w-2xl space-y-6">
          {error != null && <ErrorBanner error={error} onRetry={load} />}

          {loading && !health ? (
            // Shape-matched skeleton instead of a blank ~2-3s pause before
            // content pops in (UX_AUDIT.md: Status page has no loading state).
            <div className="space-y-6" aria-hidden="true">
              {[112, 96, 128, 128].map((h, i) => (
                <div
                  key={i}
                  className="animate-pulse-soft rounded-2xl bg-oky-purple/5"
                  style={{ height: h }}
                />
              ))}
            </div>
          ) : null}

          {health && (
            <>
              {user && usage && (
                <div className="card p-5">
                  <h2 className="mb-3 font-semibold">Your token usage</h2>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <div className="rounded-lg bg-oky-purple/5 px-4 py-3">
                      <p className="text-xs text-oky-muted">Total tokens</p>
                      <p className="text-2xl font-bold">{usage.total_tokens.toLocaleString()}</p>
                    </div>
                    <div className="rounded-lg bg-oky-purple/5 px-4 py-3">
                      <p className="text-xs text-oky-muted">LLM requests</p>
                      <p className="text-2xl font-bold">{usage.request_count}</p>
                    </div>
                    <div className="rounded-lg bg-oky-purple/5 px-4 py-3">
                      <p className="text-xs text-oky-muted">Prompt tokens</p>
                      <p className="text-lg font-semibold">{usage.prompt_tokens.toLocaleString()}</p>
                    </div>
                    <div className="rounded-lg bg-oky-purple/5 px-4 py-3">
                      <p className="text-xs text-oky-muted">Completion tokens</p>
                      <p className="text-lg font-semibold">{usage.completion_tokens.toLocaleString()}</p>
                    </div>
                  </div>
                </div>
              )}

              <div
                className={`card flex items-center gap-4 p-6 ${
                  overallOk ? "border-green-500/30" : "border-amber-500/30"
                }`}
              >
                <div
                  className={`flex h-12 w-12 items-center justify-center rounded-full ${
                    overallOk ? "bg-green-500/20" : "bg-amber-500/20"
                  }`}
                >
                  {overallOk ? (
                    <CheckCircle2 className="h-6 w-6 text-green-400" />
                  ) : (
                    <XCircle className="h-6 w-6 text-amber-400" />
                  )}
                </div>
                <div>
                  <p className="text-lg font-semibold capitalize">{health.status}</p>
                  <p className="text-sm text-oky-muted">
                    All systems {overallOk ? "operational" : "degraded"}
                  </p>
                </div>
              </div>

              <div className="card space-y-2 p-5">
                <h2 className="mb-3 font-semibold">Infrastructure</h2>
                <StatusRow label="Redis" value={health.redis} ok={health.redis === "ok"} />
                <StatusRow
                  label="Pinecone"
                  value={health.pinecone}
                  ok={health.pinecone === "ok"}
                />
              </div>

              <div className="card space-y-2 p-5">
                <h2 className="mb-3 font-semibold">Models</h2>
                <StatusRow label="LLM" value={health.model} ok />
                <StatusRow label="Embeddings" value={health.embedding_model} ok />
                <StatusRow label="Reranker" value={health.rerank_model} ok />
              </div>

              <a
                href="/docs"
                target="_blank"
                rel="noopener noreferrer"
                className="btn-secondary inline-flex"
              >
                <ExternalLink className="h-4 w-4" />
                Open API docs
              </a>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
