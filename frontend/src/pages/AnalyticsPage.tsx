import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Pill,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import { getStoredToken } from "../api/auth";
import { wsUrl } from "../config";
import {
  fetchAnalytics,
  fetchAgentStatuses,
  fetchHandoffQueue,
  fetchUsers,
  forceResolveHandoff,
  promoteUser,
} from "../api/client";
import { useAuth } from "../context/AuthContext";
import ErrorBanner from "../components/ui/ErrorBanner";
import { formatDuration, formatReason } from "../utils/format";
import type {
  ActiveHandoffEntry,
  AgentStatusItem,
  AnalyticsResponse,
  QueueEntry,
  UserListItem,
} from "../types/api";

const ROLES = ["user", "agent", "admin"] as const;

function BarRow({ label, value, max }: { label: string; value: number; max: number }) {
  const pct = max > 0 ? (value / max) * 100 : 0;
  return (
    <div>
      <div className="mb-1 flex justify-between text-sm">
        <span className="truncate text-oky-muted">{label.replace(/_/g, " ")}</span>
        <span className="font-medium">{value}</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-oky-purple/5">
        <div
          className="h-full rounded-full bg-gradient-to-r from-oky-purple to-oky-purple transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

type Tab = "overview" | "queue" | "users";

export default function AnalyticsPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  // Live Queue needs agent/admin on the backend (AgentUserDep) — showing the
  // tab to a plain patient just so its data calls 403 was the real bug
  // behind the old force-logout (a stray 401); hiding it here matches how
  // the Users tab is already scoped to isAdmin below.
  const isAgentOrAdmin = user?.role === "agent" || user?.role === "admin";
  const [tab, setTab] = useState<Tab>("overview");
  const [users, setUsers] = useState<UserListItem[]>([]);
  const [usersLoading, setUsersLoading] = useState(false);
  const [usersError, setUsersError] = useState<unknown>(null);
  const [promotingId, setPromotingId] = useState<string | null>(null);
  const [data, setData] = useState<AnalyticsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [queueError, setQueueError] = useState<unknown>(null);
  const [queueLen, setQueueLen] = useState(0);
  const [entries, setEntries] = useState<QueueEntry[]>([]);
  const [activeHandoffs, setActiveHandoffs] = useState<ActiveHandoffEntry[]>([]);
  const [agents, setAgents] = useState<AgentStatusItem[]>([]);
  const [activeConversations, setActiveConversations] = useState(0);
  const [avgWait, setAvgWait] = useState(0);
  const wsRef = useRef<WebSocket | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await fetchAnalytics());
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadQueueRest = useCallback(async () => {
    setQueueError(null);
    try {
      const [q, a] = await Promise.all([fetchHandoffQueue(), fetchAgentStatuses()]);
      setQueueLen(q.length);
      setEntries(q.entries);
      setActiveHandoffs(q.active || []);
      setAgents(a.agents);
      setActiveConversations(
        Math.max(a.agents.filter((x) => x.status === "busy").length, (q.active || []).length),
      );
      const waits = q.entries.map((e) => e.wait_seconds);
      setAvgWait(waits.length ? Math.round(waits.reduce((s, n) => s + n, 0) / waits.length) : 0);
    } catch (err) {
      // The tab is now hidden entirely for non-agent/admin users (see
      // isAgentOrAdmin), so this should only fire on a genuine transient
      // failure — surface it instead of silently showing a fake-empty
      // queue, which is what happened before (UX_AUDIT.md follow-up).
      setQueueError(err);
    }
  }, []);

  const loadUsers = useCallback(async () => {
    setUsersLoading(true);
    setUsersError(null);
    try {
      const res = await fetchUsers();
      setUsers(res.users);
    } catch (err) {
      setUsersError(err);
    } finally {
      setUsersLoading(false);
    }
  }, []);

  const handlePromote = useCallback(
    async (userId: string, role: string) => {
      setPromotingId(userId);
      try {
        await promoteUser(userId, role);
        await loadUsers();
      } catch (err) {
        setUsersError(err);
      } finally {
        setPromotingId(null);
      }
    },
    [loadUsers],
  );

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (tab === "users" && isAdmin) void loadUsers();
  }, [tab, isAdmin, loadUsers]);

  useEffect(() => {
    if (tab !== "queue") return;
    void loadQueueRest();
    const token = getStoredToken();
    const ws = new WebSocket(
      `${wsUrl("/ws/admin/queue")}?token=${encodeURIComponent(token || "")}`,
    );
    wsRef.current = ws;
    ws.onmessage = (ev) => {
      try {
        const payload = JSON.parse(ev.data) as Record<string, unknown>;
        if (payload.type !== "queue_snapshot") return;
        setQueueLen(Number(payload.queue_length || 0));
        setEntries((payload.entries as QueueEntry[]) || []);
        setActiveHandoffs((payload.active as ActiveHandoffEntry[]) || []);
        setAgents((payload.agents as AgentStatusItem[]) || []);
        setActiveConversations(Number(payload.active_conversations || 0));
        setAvgWait(Number(payload.average_wait_seconds || 0));
      } catch {
        // ignore
      }
    };
    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, [tab, loadQueueRest]);

  const maxQueryType = Math.max(...Object.values(data?.query_types ?? {}), 1);
  const maxDocType = Math.max(...Object.values(data?.doc_type_queries ?? {}), 1);
  const totalQueries = Object.values(data?.query_types ?? {}).reduce((a, b) => a + b, 0);
  const handoffRate =
    totalQueries > 0 ? Math.round((queueLen / Math.max(totalQueries, 1)) * 1000) / 10 : 0;

  return (
    <div className="flex h-full flex-col">
      <header className="flex shrink-0 items-center justify-between border-b border-oky-border/40 px-8 py-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-oky-text">Analytics</h1>
          <p className="mt-1 text-sm text-oky-muted">
            Query patterns, faithfulness scores, and live handoff queue.
          </p>
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              className={`rounded-lg px-3 py-1.5 text-sm ${
                tab === "overview" ? "bg-oky-purple/10 text-oky-purple" : "text-oky-muted"
              }`}
              onClick={() => setTab("overview")}
            >
              Overview
            </button>
            {isAgentOrAdmin && (
              <button
                type="button"
                className={`rounded-lg px-3 py-1.5 text-sm ${
                  tab === "queue" ? "bg-oky-purple/10 text-oky-purple" : "text-oky-muted"
                }`}
                onClick={() => setTab("queue")}
              >
                <span className="inline-flex items-center gap-1.5">
                  Live Queue
                  <span className="relative flex h-1.5 w-1.5">
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-success-500 opacity-75" />
                    <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-success-500" />
                  </span>
                </span>
              </button>
            )}
            {isAdmin && (
              <button
                type="button"
                className={`rounded-lg px-3 py-1.5 text-sm ${
                  tab === "users" ? "bg-oky-purple/10 text-oky-purple" : "text-oky-muted"
                }`}
                onClick={() => setTab("users")}
              >
                Users
              </button>
            )}
          </div>
        </div>
        <button
          type="button"
          onClick={
            tab === "overview" ? load : tab === "queue" ? () => void loadQueueRest() : () => void loadUsers()
          }
          className="btn-ghost"
          title="Refresh"
          aria-label="Refresh analytics"
        >
          <RefreshCw className={`h-4 w-4 ${loading || usersLoading ? "animate-spin" : ""}`} aria-hidden="true" />
        </button>
      </header>

      <div className="flex-1 overflow-auto p-6">
        <div className="mx-auto max-w-6xl space-y-6">
          {error != null && tab === "overview" && (
            <ErrorBanner error={error} onRetry={load} />
          )}

          {tab === "queue" ? (
            <>
              {queueError != null && <ErrorBanner error={queueError} onRetry={loadQueueRest} />}
              <div className="grid gap-4 sm:grid-cols-4">
                <div className="card p-5">
                  <p className="text-xs uppercase text-oky-muted">Queue length</p>
                  <p className="text-3xl font-bold">{queueLen}</p>
                </div>
                <div className="card p-5">
                  <p className="text-xs uppercase text-oky-muted">Active human chats</p>
                  <p className="text-3xl font-bold">{activeConversations}</p>
                </div>
                <div className="card p-5">
                  <p className="text-xs uppercase text-oky-muted">Avg wait</p>
                  <p className="text-3xl font-bold">{formatDuration(avgWait)}</p>
                </div>
                <div className="card p-5">
                  <p className="text-xs uppercase text-oky-muted">Handoff pressure</p>
                  <p className="text-3xl font-bold">{handoffRate}%</p>
                </div>
              </div>

              <div className="grid gap-6 lg:grid-cols-3">
                <div className="card p-5">
                  <h2 className="mb-4 font-semibold">Waiting patients</h2>
                  {entries.length === 0 ? (
                    <p className="text-sm text-oky-muted">Queue empty.</p>
                  ) : (
                    <div className="space-y-2">
                      {entries.map((e) => (
                        <div key={e.session_id} className="rounded-xl border border-oky-border/50 px-3 py-2 text-sm">
                          <p className="font-medium">{e.patient_username}</p>
                          <p className="text-xs text-oky-muted">
                            #{e.queue_position + 1} · waiting {formatDuration(e.wait_seconds)} ·{" "}
                            {formatReason(e.reason)}
                          </p>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
                <div className="card p-5">
                  <h2 className="mb-4 font-semibold">Live with agent</h2>
                  {activeHandoffs.length === 0 ? (
                    <p className="text-sm text-oky-muted">No active human chats.</p>
                  ) : (
                    <div className="space-y-2">
                      {activeHandoffs.map((e) => (
                        <div
                          key={e.session_id}
                          className="flex items-center justify-between gap-2 rounded-xl border border-oky-border/50 px-3 py-2 text-sm"
                        >
                          <div className="min-w-0">
                            <p className="truncate font-medium">{e.patient_username}</p>
                            <p className="text-xs text-oky-muted">
                              Agent: {e.agent_name || e.agent_id?.slice(0, 8) || "—"}
                              {e.duration_seconds != null ? ` · ${formatDuration(e.duration_seconds)}` : ""}
                              {e.message_count != null ? ` · ${e.message_count} msgs` : ""}
                            </p>
                          </div>
                          {isAdmin && (
                            <button
                              type="button"
                              className="btn-secondary shrink-0 text-xs"
                              onClick={async () => {
                                try {
                                  await forceResolveHandoff(e.session_id);
                                  await loadQueueRest();
                                } catch (err) {
                                  setError(err);
                                }
                              }}
                            >
                              Force resolve
                            </button>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
                <div className="card p-5">
                  <h2 className="mb-4 font-semibold">Online agents</h2>
                  {agents.length === 0 ? (
                    <p className="text-sm text-oky-muted">No agents online.</p>
                  ) : (
                    <div className="space-y-2">
                      {agents.map((a) => (
                        <div key={a.agent_id} className="flex items-center justify-between rounded-xl border border-oky-border/50 px-3 py-2 text-sm">
                          <span>{a.full_name || a.agent_id.slice(0, 8)}</span>
                          <span className="text-xs uppercase text-oky-muted">{a.status}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </>
          ) : tab === "users" ? (
            <div className="card p-5">
              <h2 className="mb-4 font-semibold">Users</h2>
              {usersError != null && <ErrorBanner error={usersError} onRetry={loadUsers} />}
              {usersLoading && users.length === 0 ? (
                <p className="text-sm text-oky-muted">Loading users…</p>
              ) : users.length === 0 ? (
                <p className="text-sm text-oky-muted">No users found.</p>
              ) : (
                <div className="space-y-2">
                  {users.map((u) => (
                    <div
                      key={u.id}
                      className="flex items-center justify-between gap-3 rounded-xl border border-oky-border/50 px-3 py-2 text-sm"
                    >
                      <div className="min-w-0">
                        <p className="truncate font-medium">{u.full_name || u.email}</p>
                        <p className="truncate text-xs text-oky-muted">{u.email}</p>
                      </div>
                      {/* w-auto overrides .input-field's baked-in w-full —
                          without it the select claimed the entire flex row's
                          width, collapsing the name/email column to zero
                          width (UX_AUDIT.md: Users tab rendering bug). */}
                      <select
                        className="input-field w-auto shrink-0 text-xs"
                        value={u.role}
                        disabled={promotingId === u.id}
                        onChange={(e) => void handlePromote(u.id, e.target.value)}
                      >
                        {ROLES.map((r) => (
                          <option key={r} value={r}>
                            {r}
                          </option>
                        ))}
                      </select>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : loading && !data ? (
            <p className="text-sm text-oky-muted">Loading analytics…</p>
          ) : data ? (
            <>
              <div className="grid gap-4 sm:grid-cols-3">
                {(() => {
                  // Color the number itself, not just an icon, so the stat
                  // tile communicates severity at a glance rather than
                  // looking identical whether the average is 95% or 30%
                  // (UX_AUDIT.md: Analytics — stat tiles have no color
                  // coding at all). PASS/WARN/FAIL thresholds mirror the
                  // per-message faithfulness badge.
                  const avg = data.faithfulness_rolling_avg;
                  const avgTone =
                    avg == null ? "muted" : avg >= 0.8 ? "success" : avg >= 0.5 ? "warning" : "danger";
                  const toneText: Record<string, string> = {
                    success: "text-success-700",
                    warning: "text-warning-700",
                    danger: "text-danger-700",
                    muted: "text-oky-text",
                  };
                  const toneIcon: Record<string, string> = {
                    success: "text-success-500",
                    warning: "text-warning-500",
                    danger: "text-danger-500",
                    muted: "text-oky-muted",
                  };
                  const flagCount = data.flagged_emergency.length;
                  const flagTone = flagCount > 0 ? "danger" : "muted";
                  return (
                    <>
                      <div className="card p-5">
                        <div className="mb-2 flex items-center gap-2 text-oky-muted">
                          <ShieldCheck className={`h-4 w-4 ${toneIcon[avgTone]}`} />
                          <span className="text-xs uppercase tracking-wide">Faithfulness avg</span>
                        </div>
                        <p className={`text-3xl font-bold ${toneText[avgTone]}`}>
                          {avg != null ? `${Math.round(avg * 100)}%` : "—"}
                        </p>
                        <p className="mt-1 text-xs text-oky-muted">
                          {data.faithfulness_scores.length} scores tracked
                        </p>
                      </div>
                      <div className="card p-5">
                        <div className="mb-2 flex items-center gap-2 text-oky-muted">
                          <AlertTriangle className={`h-4 w-4 ${toneIcon[flagTone]}`} />
                          <span className="text-xs uppercase tracking-wide">Emergency flags</span>
                        </div>
                        <p className={`text-3xl font-bold ${toneText[flagTone]}`}>{flagCount}</p>
                      </div>
                    </>
                  );
                })()}
                <div className="card p-5">
                  <div className="mb-2 flex items-center gap-2 text-oky-muted">
                    <Pill className="h-4 w-4 text-oky-purple" />
                    <span className="text-xs uppercase tracking-wide">Top drugs tracked</span>
                  </div>
                  <p className="text-3xl font-bold">{data.top_drugs.length}</p>
                </div>
              </div>

              <div className="grid gap-6 lg:grid-cols-2">
                <div className="card p-5">
                  <h2 className="mb-4 font-semibold">Query types</h2>
                  <div className="space-y-3">
                    {Object.entries(data.query_types).length === 0 ? (
                      <p className="text-sm text-oky-muted">No data yet.</p>
                    ) : (
                      Object.entries(data.query_types)
                        .sort(([, a], [, b]) => b - a)
                        .map(([k, v]) => (
                          <BarRow key={k} label={k} value={v} max={maxQueryType} />
                        ))
                    )}
                  </div>
                </div>
                <div className="card p-5">
                  <h2 className="mb-4 font-semibold">Document type usage</h2>
                  <div className="space-y-3">
                    {Object.entries(data.doc_type_queries).length === 0 ? (
                      <p className="text-sm text-oky-muted">No data yet.</p>
                    ) : (
                      Object.entries(data.doc_type_queries)
                        .sort(([, a], [, b]) => b - a)
                        .map(([k, v]) => (
                          <BarRow key={k} label={k} value={v} max={maxDocType} />
                        ))
                    )}
                  </div>
                </div>
              </div>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
