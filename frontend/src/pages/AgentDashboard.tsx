import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Headset, RefreshCw, Send, UserRound, X } from "lucide-react";
import {
  fetchAgentStatuses,
  fetchHandoffConversation,
  fetchHandoffQueue,
  registerAgent,
  resolveHandoff,
  takeNextPatient,
} from "../api/client";
import { getStoredToken } from "../api/auth";
import { useAuth } from "../context/AuthContext";
import ErrorBanner from "../components/ui/ErrorBanner";
import EventDivider from "../components/chat/EventDivider";
import { formatDuration, formatReason } from "../utils/format";
import type { ActiveHandoffEntry, AgentStatusItem, QueueEntry } from "../types/api";

const MAX_SLOTS = 5;
const MAX_RECONNECT_ATTEMPTS = 10;
const HEARTBEAT_INTERVAL_MS = 15000;

interface ChatLine {
  id: string;
  role: "user" | "assistant" | "event";
  content: string;
}

interface SessionTab {
  sessionId: string;
  patientLabel: string;
  reason: string | null;
  lastQuery: string | null;
  context: Record<string, unknown> | null;
  draft: string;
}

const ISSUE_TAGS = [
  { value: "medication", label: "Medication" },
  { value: "labs", label: "Labs / results" },
  { value: "guidelines", label: "Guidelines" },
  { value: "symptoms", label: "Symptoms" },
  { value: "billing", label: "Billing / access" },
  { value: "technical", label: "Technical" },
  { value: "other", label: "Other" },
] as const;

const END_REASONS = [
  { value: "issue_resolved", label: "Issue handled — agent closed chat" },
  { value: "patient_ended", label: "Patient asked to end chat" },
  { value: "patient_inactive", label: "Patient inactive / no response" },
  { value: "unresolved", label: "Could not resolve — closed anyway" },
  { value: "transferred", label: "Transferred to another agent" },
] as const;

const ISSUE_STATUSES = [
  { value: "resolved", label: "Issue resolved" },
  { value: "partial", label: "Partially resolved" },
  { value: "not_resolved", label: "Not resolved" },
] as const;

function CapacityBar({ count, max = MAX_SLOTS }: { count: number; max?: number }) {
  const pct = Math.min(100, (count / max) * 100);
  const color =
    count >= max ? "bg-red-500" : count >= 3 ? "bg-amber-400" : "bg-emerald-500";
  const badge =
    count >= max
      ? "bg-red-50 text-red-700"
      : count >= 3
        ? "bg-amber-50 text-amber-800"
        : "bg-emerald-50 text-emerald-800";
  return (
    <div className="mt-2">
      <div className="mb-1 flex items-center justify-between text-[11px]">
        <span className={`rounded-full px-2 py-0.5 font-semibold ${badge}`}>
          {count} / {max} active chats{count >= max ? " · FULL" : ""}
        </span>
      </div>
      {/* Thickened from h-1.5 — a capacity bar an agent needs to notice at
          a glance was previously a near-invisible hairline (UX_AUDIT.md). */}
      <div className="h-2.5 overflow-hidden rounded-full bg-oky-purple/10">
        <div className={`h-full ${color} transition-all`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export default function AgentDashboard() {
  const { user } = useAuth();
  const [queue, setQueue] = useState<QueueEntry[]>([]);
  const [activeList, setActiveList] = useState<ActiveHandoffEntry[]>([]);
  const [agents, setAgents] = useState<AgentStatusItem[]>([]);
  const [tabs, setTabs] = useState<SessionTab[]>([]);
  const [messagesBySession, setMessagesBySession] = useState<Record<string, ChatLine[]>>({});
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [myActiveCount, setMyActiveCount] = useState(0);
  const [resolveOpen, setResolveOpen] = useState(false);
  const [resolveTag, setResolveTag] = useState("other");
  const [resolveEndReason, setResolveEndReason] = useState("issue_resolved");
  const [resolveIssueStatus, setResolveIssueStatus] = useState("resolved");
  const [resolveComments, setResolveComments] = useState("");
  const [resolving, setResolving] = useState(false);
  const selectedIdRef = useRef<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const tabsRef = useRef<SessionTab[]>([]);
  const loadConversationRef = useRef<(sessionId: string, patientHint?: string) => Promise<void>>(
    async () => undefined,
  );
  const loadQueueRef = useRef<() => Promise<ActiveHandoffEntry[]>>(async () => []);
  const upsertTabRef = useRef<(tab: SessionTab, lines?: ChatLine[]) => void>(() => undefined);
  const closeTabRef = useRef<(sessionId: string) => void>(() => undefined);
  const [socketLive, setSocketLive] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [permanentlyDisconnected, setPermanentlyDisconnected] = useState(false);
  const manualReconnectRef = useRef<() => void>(() => undefined);
  // Gates the three panels behind a single loading sequence so they don't
  // flash contradictory empty states while register → queue → conversations
  // are still loading in order (Issue 10).
  const [dashboardReady, setDashboardReady] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);

  const selectedTab = useMemo(
    () => tabs.find((t) => t.sessionId === selectedId) || null,
    [tabs, selectedId],
  );
  const selectedMessages = selectedId ? messagesBySession[selectedId] || [] : [];
  const atCapacity = myActiveCount >= MAX_SLOTS;

  const upsertTab = useCallback((tab: SessionTab, lines?: ChatLine[]) => {
    setTabs((prev) => {
      const idx = prev.findIndex((t) => t.sessionId === tab.sessionId);
      if (idx >= 0) {
        const next = [...prev];
        next[idx] = { ...next[idx], ...tab };
        return next;
      }
      if (prev.length >= MAX_SLOTS) return prev;
      return [...prev, tab];
    });
    if (lines) {
      setMessagesBySession((prev) => ({ ...prev, [tab.sessionId]: lines }));
    }
    setSelectedId(tab.sessionId);
    selectedIdRef.current = tab.sessionId;
  }, []);

  const closeTab = useCallback((sessionId: string) => {
    setTabs((prev) => prev.filter((t) => t.sessionId !== sessionId));
    setMessagesBySession((prev) => {
      const next = { ...prev };
      delete next[sessionId];
      return next;
    });
    setSelectedId((cur) => {
      if (cur !== sessionId) return cur;
      return null;
    });
    if (selectedIdRef.current === sessionId) selectedIdRef.current = null;
  }, []);

  const loadConversation = useCallback(
    async (sessionId: string, patientHint?: string) => {
      try {
        const conv = await fetchHandoffConversation(sessionId);
        const patientName = patientHint || "patient";
        const lines: ChatLine[] = conv.messages.map((m) => {
          if (m.role === "event" || m.role === "system") {
            return { id: m.id, role: "event" as const, content: m.content };
          }
          return {
            id: m.id,
            role: (m.role === "user" ? "user" : "assistant") as "user" | "assistant",
            content: m.content,
          };
        });
        const last = [...conv.messages].reverse().find((m) => m.role === "user");
        upsertTab(
          {
            sessionId,
            patientLabel: patientName,
            reason: conv.handoff_reason ?? null,
            lastQuery: last?.content ?? null,
            context: (conv.clinical_context as Record<string, unknown>) || null,
            draft: "",
          },
          lines,
        );
        setError(null);
      } catch (err) {
        // Still open a chat tab so the agent can message — history may load later.
        upsertTab({
          sessionId,
          patientLabel: patientHint || "patient",
          reason: null,
          lastQuery: null,
          context: null,
          draft: "",
        });
        setError(err);
      }
    },
    [upsertTab],
  );

  const loadQueue = useCallback(async () => {
    try {
      const [q, a] = await Promise.all([fetchHandoffQueue(), fetchAgentStatuses()]);
      setQueue(q.entries);
      setActiveList(q.active || []);
      setAgents(a.agents);
      const myId = user?.id;
      if (myId) {
        const mine = a.agents.find((x) => x.agent_id === myId);
        const myActive = (q.active || []).filter((x) => x.agent_id === myId);
        setMyActiveCount(mine?.active_count ?? myActive.length);

        // Recover tabs if Redis says we own sessions but UI tabs were wiped
        // (Strict Mode remount / WS flicker) while the patient stayed assigned.
        const openIds = new Set(tabsRef.current.map((t) => t.sessionId));
        for (const entry of myActive.slice(0, MAX_SLOTS)) {
          if (!openIds.has(entry.session_id)) {
            void loadConversationRef.current(entry.session_id, entry.patient_username);
          }
        }
      }
      setLastUpdated(Date.now());
      return q.active || [];
    } catch (err) {
      setError(err);
      return [] as ActiveHandoffEntry[];
    }
  }, [user?.id]);

  // Keep refs current so the WebSocket effect can stay mounted on user.id only.
  tabsRef.current = tabs;
  loadConversationRef.current = loadConversation;
  loadQueueRef.current = loadQueue;
  upsertTabRef.current = upsertTab;
  closeTabRef.current = closeTab;

  useEffect(() => {
    if (!user || (user.role !== "agent" && user.role !== "admin")) return;
    const agentId = user.id;
    let stopped = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: number | undefined;
    let heartbeatTimer: number | undefined;
    let poll: number | undefined;
    let reconnectAttempts = 0;

    const handleMessage = (ev: MessageEvent) => {
      try {
        const data = JSON.parse(String(ev.data)) as Record<string, unknown>;
        if (typeof data.active_count === "number") {
          setMyActiveCount(Number(data.active_count));
        }
        if (data.type === "patient_assigned" && typeof data.session_id === "string") {
          const sid = data.session_id;
          const patientName = String(
            (data.patient as { full_name?: string; username?: string } | undefined)?.full_name
              || (data.patient as { username?: string } | undefined)?.username
              || "patient",
          );
          const seeded = Array.isArray(data.messages)
            ? (data.messages as Array<{ id?: string; role?: string; content?: string }>).map(
                (m, i) => {
                  const role = String(m.role || "assistant");
                  if (role === "event" || role === "system") {
                    return {
                      id: String(m.id || `${sid}-e-${i}`),
                      role: "event" as const,
                      content: String(m.content || ""),
                    };
                  }
                  return {
                    id: String(m.id || `${sid}-${i}`),
                    role: (role === "user" ? "user" : "assistant") as "user" | "assistant",
                    content: String(m.content || ""),
                  };
                },
              )
            : [];
          upsertTabRef.current(
            {
              sessionId: sid,
              patientLabel: patientName,
              reason: String(data.reason || "") || null,
              lastQuery: String(data.last_query || "") || null,
              context: (data.clinical_context as Record<string, unknown>) || null,
              draft: "",
            },
            seeded,
          );
          void loadQueueRef.current();
        }
        if (data.type === "patient_message" && typeof data.session_id === "string") {
          const sid = data.session_id;
          setMessagesBySession((prev) => ({
            ...prev,
            [sid]: [
              ...(prev[sid] || []),
              { id: crypto.randomUUID(), role: "user", content: String(data.content || "") },
            ],
          }));
        }
        if (data.type === "patient_disconnected" && typeof data.session_id === "string") {
          const sid = data.session_id;
          setMessagesBySession((prev) => ({
            ...prev,
            [sid]: [
              ...(prev[sid] || []),
              {
                id: crypto.randomUUID(),
                role: "event",
                content: "Patient disconnected — returned to queue",
              },
            ],
          }));
          window.setTimeout(() => closeTabRef.current(sid), 800);
          void loadQueueRef.current();
        }
        if (data.type === "conversation_resolved" && typeof data.session_id === "string") {
          const sid = data.session_id;
          setMessagesBySession((prev) => ({
            ...prev,
            [sid]: [
              ...(prev[sid] || []),
              { id: crypto.randomUUID(), role: "event", content: "Conversation ended" },
            ],
          }));
          window.setTimeout(() => closeTabRef.current(sid), 800);
          void loadQueueRef.current();
        }
        if (data.type === "error") {
          setError(new Error(String(data.message || "Agent error")));
        }
      } catch {
        // ignore
      }
    };

    const clearHeartbeat = () => {
      if (heartbeatTimer) {
        window.clearInterval(heartbeatTimer);
        heartbeatTimer = undefined;
      }
    };

    const connect = () => {
      if (stopped) return;
      setReconnecting(reconnectAttempts > 0);
      const token = getStoredToken();
      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(
        `${proto}://${window.location.host}/ws/agent/${agentId}?token=${encodeURIComponent(token || "")}`,
      );
      socket = ws;
      wsRef.current = ws;
      setSocketLive(false);

      ws.onopen = () => {
        if (stopped) {
          ws.close();
          return;
        }
        wsRef.current = ws;
        setSocketLive(true);
        setReconnecting(false);
        setPermanentlyDisconnected(false);
        reconnectAttempts = 0;
        // Heartbeat (Issue 24): keep the backend's grace timer fresh and
        // opportunistically re-register if Redis was cleared/restarted.
        clearHeartbeat();
        heartbeatTimer = window.setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "ping" }));
          }
        }, HEARTBEAT_INTERVAL_MS);
      };

      ws.onmessage = handleMessage;

      ws.onclose = () => {
        if (wsRef.current === ws) wsRef.current = null;
        setSocketLive(false);
        clearHeartbeat();
        if (stopped) return;
        reconnectAttempts += 1;
        if (reconnectAttempts > MAX_RECONNECT_ATTEMPTS) {
          setReconnecting(false);
          setPermanentlyDisconnected(true);
          return;
        }
        setReconnecting(true);
        reconnectTimer = window.setTimeout(connect, 700);
      };
    };

    manualReconnectRef.current = () => {
      reconnectAttempts = 0;
      setPermanentlyDisconnected(false);
      connect();
    };

    const boot = async () => {
      await registerAgent().catch(() => undefined);
      if (stopped) return;
      const active = await loadQueueRef.current();
      if (stopped) return;
      const mine = active.filter((a) => a.agent_id === agentId).slice(0, MAX_SLOTS);
      for (const entry of mine) {
        await loadConversationRef.current(entry.session_id, entry.patient_username);
      }
      if (!stopped) setDashboardReady(true);
    };
    void boot();
    connect();

    poll = window.setInterval(() => {
      void registerAgent().catch(() => undefined);
      void loadQueueRef.current();
    }, 10000);

    return () => {
      stopped = true;
      if (poll) clearInterval(poll);
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      clearHeartbeat();
      const ws = socket;
      if (wsRef.current === ws) wsRef.current = null;
      setSocketLive(false);
      try {
        ws?.close();
      } catch {
        // ignore
      }
    };
  }, [user?.id]);

  if (!user || (user.role !== "agent" && user.role !== "admin")) {
    return (
      <div className="flex h-full items-center justify-center p-8 text-sm text-oky-muted">
        Agent or admin role required.
      </div>
    );
  }

  const send = () => {
    if (!selectedTab) return;
    const text = selectedTab.draft.trim();
    if (!text) return;
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      setError(new Error("Reconnecting agent socket — try send again in a moment."));
      return;
    }
    const sid = selectedTab.sessionId;
    ws.send(JSON.stringify({ type: "message", session_id: sid, content: text }));
    setMessagesBySession((prev) => ({
      ...prev,
      [sid]: [
        ...(prev[sid] || []),
        { id: crypto.randomUUID(), role: "assistant", content: text },
      ],
    }));
    setTabs((prev) =>
      prev.map((t) => (t.sessionId === sid ? { ...t, draft: "" } : t)),
    );
  };

  const submitResolve = async () => {
    if (!selectedId) return;
    setResolving(true);
    try {
      await resolveHandoff(selectedId, {
        tag: resolveTag,
        end_reason: resolveEndReason,
        issue_status: resolveIssueStatus,
        comments: resolveComments.trim() || undefined,
      });
      setResolveOpen(false);
      setResolveComments("");
      const sid = selectedId;
      setMessagesBySession((prev) => ({
        ...prev,
        [sid]: [
          ...(prev[sid] || []),
          { id: crypto.randomUUID(), role: "event", content: "Conversation ended" },
        ],
      }));
      window.setTimeout(() => closeTab(sid), 600);
      await loadQueue();
    } catch (err) {
      setError(err);
    } finally {
      setResolving(false);
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex shrink-0 items-center justify-between border-b border-oky-border/40 px-6 py-4">
        <div>
          <h1 className="text-xl font-bold text-oky-text">Agent dashboard</h1>
          <p className="text-sm text-oky-muted">
            Up to {MAX_SLOTS} isolated chats — each tab has its own messages and input.
          </p>
          <CapacityBar count={myActiveCount} />
          {permanentlyDisconnected ? (
            <p className="mt-1 flex items-center gap-2 text-[11px] text-red-700">
              Agent channel disconnected — you will not receive new chats.
              <button
                type="button"
                className="underline"
                onClick={() => manualReconnectRef.current()}
              >
                Reconnect
              </button>
            </p>
          ) : (
            <p className={`mt-1 text-[11px] ${socketLive ? "text-emerald-700" : "text-amber-700"}`}>
              {socketLive
                ? "Agent channel connected"
                : reconnecting
                  ? "Agent channel reconnecting…"
                  : "Agent channel connecting…"}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          {lastUpdated != null && (
            <span className="text-[11px] text-oky-muted">
              Updated {Math.max(0, Math.round((Date.now() - lastUpdated) / 1000))}s ago
            </span>
          )}
          <button type="button" className="btn-ghost" onClick={() => void loadQueue()} title="Refresh" aria-label="Refresh dashboard">
            <RefreshCw className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
      </header>

      {error != null && (
        <div className="px-6 pt-4">
          <ErrorBanner
            error={error}
            onRetry={() => {
              setError(null);
              void loadQueue();
            }}
            onDismiss={() => setError(null)}
          />
        </div>
      )}

      <div className="relative grid min-h-0 flex-1 grid-cols-1 gap-3 p-4 lg:grid-cols-12">
        {!dashboardReady && (
          // Single loading sequence (register → queue → conversations) means
          // the three panels below can briefly show contradictory-looking
          // empty states while data is still arriving — cover them with one
          // clear loading skeleton instead. (Issue 10)
          <div className="absolute inset-0 z-10 flex items-center justify-center rounded-2xl bg-white/70 backdrop-blur-sm">
            <p className="text-sm text-oky-muted">Loading dashboard…</p>
          </div>
        )}
        <aside className="glass-card flex min-h-0 flex-col overflow-hidden lg:col-span-3">
          <div className="border-b border-oky-border/40 px-4 py-3">
            <p className="text-sm font-semibold">Waiting · {queue.length}</p>
            <button
              type="button"
              className="btn-primary mt-3 w-full disabled:cursor-not-allowed disabled:opacity-50"
              disabled={atCapacity || queue.length === 0}
              onClick={async () => {
                try {
                  setError(null);
                  const res = await takeNextPatient();
                  if (res.assigned && res.conversation_id) {
                    await loadConversation(res.conversation_id);
                  } else if (res.message) {
                    setError(new Error(res.message));
                  }
                  await loadQueue();
                } catch (err) {
                  setError(err);
                }
              }}
            >
              {atCapacity
                ? `At capacity (${myActiveCount}/${MAX_SLOTS})`
                : queue.length === 0
                  ? "No patients waiting"
                  : "Take next patient"}
            </button>
          </div>
          <div className="max-h-[28%] overflow-auto border-b border-oky-border/40 p-2">
            {queue.length === 0 ? (
              <p className="p-3 text-sm text-oky-muted">No patients waiting.</p>
            ) : (
              queue.map((entry, idx) => (
                <div
                  key={entry.session_id}
                  className={`mb-2 rounded-xl border px-3 py-2 text-sm ${
                    idx === 0
                      ? "border-warning-500/40 bg-warning-50"
                      : "border-oky-border/50 bg-white/70"
                  }`}
                >
                  <p className="font-medium">{entry.patient_username}</p>
                  <p className="text-xs text-oky-muted">
                    waiting {formatDuration(entry.wait_seconds)} · #{entry.queue_position + 1}
                  </p>
                </div>
              ))
            )}
          </div>
          <div className="border-b border-oky-border/40 px-4 py-2">
            <p className="text-sm font-semibold">Agents online</p>
          </div>
          <div className="flex-1 overflow-auto p-2">
            {agents.length === 0 ? (
              <p className="p-3 text-sm text-oky-muted">No agents online.</p>
            ) : (
              agents.map((a) => (
                <div
                  key={a.agent_id}
                  className="mb-2 rounded-xl border border-oky-border/50 bg-white/70 px-3 py-2 text-sm"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium">{a.full_name || a.agent_id.slice(0, 8)}</span>
                    <span className="text-[10px] uppercase text-oky-muted">{a.status}</span>
                  </div>
                  <CapacityBar count={a.active_count ?? 0} max={a.max_active ?? MAX_SLOTS} />
                </div>
              ))
            )}
            {activeList.length > 0 && (
              <p className="mt-3 px-1 text-[11px] text-oky-muted">
                Live handoffs system-wide: {activeList.length}
              </p>
            )}
          </div>
        </aside>

        <section className="glass-card flex min-h-0 flex-col overflow-hidden lg:col-span-6">
          <div className="flex items-center gap-1 overflow-x-auto border-b border-oky-border/40 px-2 pt-2">
            {tabs.length === 0 ? (
              <p className="px-3 py-2 text-sm text-oky-muted">No active chats — take a patient.</p>
            ) : (
              tabs.map((tab, idx) => (
                <button
                  key={tab.sessionId}
                  type="button"
                  onClick={() => {
                    setSelectedId(tab.sessionId);
                    selectedIdRef.current = tab.sessionId;
                  }}
                  className={`mb-0 shrink-0 rounded-t-xl border border-b-0 px-3 py-2 text-left text-xs ${
                    selectedId === tab.sessionId
                      ? "border-oky-purple/40 bg-white text-oky-text"
                      : "border-transparent bg-oky-purple/5 text-oky-muted"
                  }`}
                >
                  <p className="font-semibold">{tab.patientLabel}</p>
                  <p className="text-[10px] opacity-70">
                    Slot {idx + 1} of {MAX_SLOTS}
                  </p>
                </button>
              ))
            )}
            <div className="ml-auto px-2 pb-2">
              {selectedId && (
                <button
                  type="button"
                  className="btn-secondary text-xs"
                  onClick={() => {
                    setResolveTag("other");
                    setResolveEndReason("issue_resolved");
                    setResolveIssueStatus("resolved");
                    setResolveComments("");
                    setResolveOpen(true);
                  }}
                >
                  Resolve…
                </button>
              )}
            </div>
          </div>

          <div className="flex items-center gap-2 border-b border-oky-border/40 px-4 py-2 text-sm font-semibold">
            <Headset className="h-4 w-4 text-oky-purple" />
            {selectedTab
              ? `Chatting · ${selectedTab.patientLabel}`
              : "Select a tab to chat"}
          </div>

          <div className="flex-1 overflow-auto px-3 py-2">
            {/* Keyed by selectedId so switching tabs remounts this panel —
                that's what makes the fade actually replay per switch
                instead of only playing once ever (UX_AUDIT.md:
                micro-interactions — tab switches should fade). */}
            <div key={selectedId} className="mx-auto flex w-full max-w-3xl animate-fade-in flex-col gap-1.5">
              {selectedMessages.map((m) =>
                m.role === "event" ? (
                  <EventDivider key={m.id} label={m.content} compact />
                ) : (
                  <div
                    key={m.id}
                    className={`flex ${m.role === "user" ? "justify-start" : "justify-end"}`}
                  >
                    <div
                      className={`max-w-[min(100%,28rem)] rounded-2xl px-3.5 py-2 text-sm leading-snug ${
                        m.role === "user"
                          ? "bg-oky-purple/10 text-oky-text"
                          : "bg-oky-purple text-white shadow-sm"
                      }`}
                    >
                      <p
                        className={`mb-0.5 text-[10px] font-semibold uppercase tracking-wide ${
                          m.role === "user" ? "text-oky-muted" : "text-white/70"
                        }`}
                      >
                        {m.role === "user" ? "Patient" : "You"}
                      </p>
                      <p className="whitespace-pre-wrap">{m.content}</p>
                    </div>
                  </div>
                ),
              )}
            </div>
          </div>

          {/* Per-tab isolated input — only the selected session's draft */}
          <div className="flex gap-2 border-t border-oky-border/40 p-3">
            <input
              value={selectedTab?.draft ?? ""}
              onChange={(e) => {
                const v = e.target.value;
                if (!selectedId) return;
                setTabs((prev) =>
                  prev.map((t) => (t.sessionId === selectedId ? { ...t, draft: v } : t)),
                );
              }}
              onKeyDown={(e) => e.key === "Enter" && send()}
              disabled={!selectedTab}
              placeholder={
                !selectedTab
                  ? "Select a chat tab…"
                  : !socketLive
                    ? "Reconnecting… wait a second"
                    : "Message this patient only…"
              }
              className="input-field flex-1"
            />
            <button type="button" className="btn-primary" disabled={!selectedTab || !socketLive} onClick={send}>
              <Send className="h-4 w-4" />
            </button>
          </div>
        </section>

        <aside className="glass-card overflow-auto p-4 lg:col-span-3">
          <p className="mb-3 flex items-center gap-2 text-sm font-semibold">
            <UserRound className="h-4 w-4" /> Patient context
          </p>
          <div className="space-y-3 text-sm">
            <div>
              <p className="text-xs uppercase text-oky-muted">Patient</p>
              <p>{selectedTab?.patientLabel || "—"}</p>
            </div>
            <div>
              <p className="text-xs uppercase text-oky-muted">Handoff reason</p>
              <p>{formatReason(selectedTab?.reason)}</p>
            </div>
            <div>
              <p className="text-xs uppercase text-oky-muted">Last query</p>
              <p className="whitespace-pre-wrap">{selectedTab?.lastQuery || "—"}</p>
            </div>
            <div>
              <p className="text-xs uppercase text-oky-muted">Clinical context</p>
              {(() => {
                const ctx = selectedTab?.context;
                const entries = ctx ? Object.entries(ctx) : [];
                // Flat key/value context (the common case — specialty,
                // disclaimer_shown, query_count, …) renders as a readable
                // list instead of a raw JSON dump an agent has to parse
                // (UX_AUDIT.md: Agent dashboard). Anything nested falls
                // back to JSON so no data is ever hidden.
                const isFlat = entries.every(([, v]) => v === null || typeof v !== "object");
                if (!ctx || entries.length === 0) return <p className="mt-1 text-oky-muted">—</p>;
                if (isFlat) {
                  return (
                    <dl className="mt-1 space-y-1 rounded-lg bg-oky-purple/5 p-2 text-xs">
                      {entries.map(([k, v]) => (
                        <div key={k} className="flex justify-between gap-2">
                          <dt className="text-oky-muted">{k.replace(/_/g, " ")}</dt>
                          <dd className="font-medium text-oky-text">{String(v)}</dd>
                        </div>
                      ))}
                    </dl>
                  );
                }
                return (
                  <pre className="mt-1 overflow-auto rounded-lg bg-oky-purple/5 p-2 text-xs">
                    {JSON.stringify(ctx, null, 2)}
                  </pre>
                );
              })()}
            </div>
          </div>
        </aside>
      </div>

      {resolveOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-lg rounded-2xl bg-white p-5 shadow-xl">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-lg font-semibold">Resolve handoff</h2>
              <button type="button" className="btn-ghost" onClick={() => setResolveOpen(false)}>
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="space-y-4 text-sm">
              <label className="block">
                <span className="mb-1 block font-medium">Issue tag</span>
                <select
                  className="input-field w-full"
                  value={resolveTag}
                  onChange={(e) => setResolveTag(e.target.value)}
                >
                  {ISSUE_TAGS.map((t) => (
                    <option key={t.value} value={t.value}>
                      {t.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block">
                <span className="mb-1 block font-medium">How did the chat end?</span>
                <select
                  className="input-field w-full"
                  value={resolveEndReason}
                  onChange={(e) => setResolveEndReason(e.target.value)}
                >
                  {END_REASONS.map((t) => (
                    <option key={t.value} value={t.value}>
                      {t.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block">
                <span className="mb-1 block font-medium">Was the issue resolved?</span>
                <select
                  className="input-field w-full"
                  value={resolveIssueStatus}
                  onChange={(e) => setResolveIssueStatus(e.target.value)}
                >
                  {ISSUE_STATUSES.map((t) => (
                    <option key={t.value} value={t.value}>
                      {t.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block">
                <span className="mb-1 block font-medium">Comments</span>
                <textarea
                  className="input-field min-h-[88px] w-full"
                  value={resolveComments}
                  onChange={(e) => setResolveComments(e.target.value)}
                  placeholder="What was discussed, follow-ups…"
                />
              </label>
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button type="button" className="btn-ghost" onClick={() => setResolveOpen(false)}>
                Cancel
              </button>
              <button
                type="button"
                className="btn-primary"
                disabled={resolving}
                onClick={() => void submitResolve()}
              >
                {resolving ? "Saving…" : "Confirm resolve"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
