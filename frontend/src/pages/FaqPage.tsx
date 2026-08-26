import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, HelpCircle, MessageSquare, RefreshCw } from "lucide-react";
import { fetchFaqs } from "../api/client";
import ErrorBanner from "../components/ui/ErrorBanner";
import { useSession } from "../context/SessionContext";
import type { FaqItem } from "../types/api";

function formatRelative(ts: number): string {
  const diffSec = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (diffSec < 60) return "just now";
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
  if (diffSec < 86400 * 7) return `${Math.floor(diffSec / 86400)}d ago`;
  return new Date(ts * 1000).toLocaleDateString();
}

export default function FaqPage() {
  const navigate = useNavigate();
  const { askFaq } = useSession();
  const [items, setItems] = useState<FaqItem[]>([]);
  const [total, setTotal] = useState(0);
  const [sort, setSort] = useState<"count" | "recent">("count");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchFaqs({ limit: 50, sort });
      setItems(data.items);
      setTotal(data.total);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, [sort]);

  useEffect(() => {
    load();
  }, [load]);

  const onAsk = (item: FaqItem) => {
    askFaq(item.canonical_question);
    navigate("/chat");
  };

  return (
    <div className="flex h-full flex-col">
      <header className="flex shrink-0 items-center justify-between border-b border-oky-border/40 px-8 py-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-oky-text">FAQ</h1>
          <p className="mt-1 text-sm text-oky-muted">
            Frequently asked clinical questions, clustered by meaning.
            {total > 0 ? ` ${total} tracked.` : ""}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex rounded-xl border border-oky-border/60 bg-white/80 p-0.5 text-sm">
            <button
              type="button"
              onClick={() => setSort("count")}
              className={`rounded-lg px-3 py-1.5 transition ${
                sort === "count"
                  ? "bg-oky-purple/10 font-medium text-oky-purple"
                  : "text-oky-muted hover:text-oky-text"
              }`}
            >
              Top
            </button>
            <button
              type="button"
              onClick={() => setSort("recent")}
              className={`rounded-lg px-3 py-1.5 transition ${
                sort === "recent"
                  ? "bg-oky-purple/10 font-medium text-oky-purple"
                  : "text-oky-muted hover:text-oky-text"
              }`}
            >
              Recent
            </button>
          </div>
          <button type="button" onClick={load} className="btn-ghost" title="Refresh" aria-label="Refresh FAQ list">
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} aria-hidden="true" />
          </button>
        </div>
      </header>

      <div className="flex-1 overflow-auto p-6">
        <div className="mx-auto max-w-3xl space-y-3">
          {error != null && <ErrorBanner error={error} onRetry={load} />}

          {loading && items.length === 0 ? (
            <p className="text-sm text-oky-muted">Loading FAQs…</p>
          ) : items.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
              <HelpCircle className="h-10 w-10 text-oky-muted/50" />
              <p className="text-sm text-oky-muted">
                No FAQ clusters yet. Ask clinical questions in chat — paraphrases
                merge automatically when similarity ≥ 0.80.
              </p>
            </div>
          ) : (
            items.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => onAsk(item)}
                className="group flex w-full items-start gap-4 rounded-2xl border border-oky-border/50 bg-white/70 px-5 py-4 text-left transition hover:border-oky-purple/30 hover:bg-white hover:shadow-sm"
              >
                <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-oky-purple/10 text-oky-purple transition group-hover:bg-oky-purple/15">
                  <MessageSquare className="h-4 w-4" />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="text-[15px] font-medium leading-snug text-oky-text">
                    {item.canonical_question}
                  </p>
                  <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-oky-muted">
                    <span className="font-medium text-oky-purple">
                      asked {item.ask_count}×
                    </span>
                    {item.query_type ? (
                      <span className="rounded-md bg-oky-purple/5 px-1.5 py-0.5">
                        {item.query_type.replace(/_/g, " ")}
                      </span>
                    ) : null}
                    <span>last {formatRelative(item.last_asked_at)}</span>
                  </div>
                </div>
                <ArrowRight className="mt-1 h-4 w-4 shrink-0 text-oky-purple opacity-0 transition group-hover:translate-x-0.5 group-hover:opacity-100" />
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
