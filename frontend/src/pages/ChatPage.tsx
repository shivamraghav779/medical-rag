import { useCallback, useEffect, useRef, useState } from "react";
import {
  cancelHandoff,
  errorMessage,
  fetchConversation,
  fetchDocuments,
  requestHandoff,
  streamChat,
  submitMessageFeedback,
} from "../api/client";
import { getStoredToken } from "../api/auth";
import { wsUrl } from "../config";
import { useSession } from "../context/SessionContext";
import type {
  AgentStatusEvent,
  ChatEvent,
  ChatMessage,
  DocumentInfo,
  HandoffState,
  RetrievedChunk,
} from "../types/api";
import ChatInput from "../components/chat/ChatInput";
import AttachModal from "../components/chat/AttachModal";
import ChatMessageView from "../components/chat/ChatMessage";
import CitationsPanel from "../components/chat/CitationsPanel";
import ClinicalContextModal from "../components/chat/ClinicalContextModal";
import DisclaimerModal from "../components/chat/DisclaimerModal";
import FeedbackModal from "../components/chat/FeedbackModal";
import ErrorBanner from "../components/ui/ErrorBanner";

const SUGGESTIONS = [
  "What are the first-line treatments for type 2 diabetes?",
  "Check interaction between warfarin and aspirin",
  "Interpret Hb 7.2 g/dL in an adult patient",
  "What are the diagnostic criteria for major depressive disorder?",
];

const NEW_CONV_KEY = "__new__";

function uid() {
  return crypto.randomUUID();
}

function messagesFromConversation(
  msgs: Array<{
    id: string;
    role: string;
    content: string;
    feedback_rating?: string | null;
  }>,
): ChatMessage[] {
  return msgs.map((m) => {
    const role =
      m.role === "event" || m.role === "system"
        ? ("event" as const)
        : m.role === "user"
          ? ("user" as const)
          : ("assistant" as const);
    return {
      id: m.id,
      serverId: m.id,
      role,
      content: m.content,
      feedbackRating:
        m.feedback_rating === "up" || m.feedback_rating === "down"
          ? m.feedback_rating
          : undefined,
    };
  });
}

interface StreamSession {
  cacheKey: string;
  resolvedConversationId: string | null;
  assistantMsgId: string;
}

export default function ChatPage() {
  const {
    conversationId,
    setConversationId,
    clinicalContext,
    setClinicalContext,
    refreshConversations,
    pendingFaqQuery,
    clearPendingFaqQuery,
  } = useSession();

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [contextOpen, setContextOpen] = useState(false);
  const [disclaimer, setDisclaimer] = useState<string | null>(null);
  const [activeCitations, setActiveCitations] = useState<RetrievedChunk[] | null>(null);
  const [documents, setDocuments] = useState<DocumentInfo[]>([]);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [attachOpen, setAttachOpen] = useState(false);
  const [webSearchEnabled, setWebSearchEnabled] = useState(false);
  const [attachedDocNames, setAttachedDocNames] = useState<string[]>([]);
  const [pageError, setPageError] = useState<unknown>(null);
  const [feedbackTarget, setFeedbackTarget] = useState<{
    conversationId: string;
    messageId: string;
    clientId: string;
  } | null>(null);
  const [handoffState, setHandoffState] = useState<HandoffState>("BOT_ACTIVE");
  const [queuePosition, setQueuePosition] = useState<number | null>(null);
  const [agentLabel, setAgentLabel] = useState<string | null>(null);
  const [notFoundStreak, setNotFoundStreak] = useState(0);
  const handoffWsRef = useRef<WebSocket | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const viewingConversationIdRef = useRef<string | null>(conversationId);
  const messagesCacheRef = useRef<Map<string, ChatMessage[]>>(new Map());
  const streamSessionRef = useRef<StreamSession | null>(null);
  const connectHandoffSocketRef = useRef<(hint?: { conversation_id?: string }) => void>(
    () => undefined,
  );

  // Restores handoff state + reconnects the /ws/chat socket when a
  // conversation is (re)loaded mid-handoff — e.g. a page reload while
  // QUEUED or HUMAN_ACTIVE. Without this, handoffState silently reset to
  // BOT_ACTIVE on reload and the next message went to the bot instead of
  // the assigned agent. (Issue 18 — frontend side; the widget already
  // handles this via state_resume.)
  const resumeHandoffFromConversation = useCallback(
    (sid: string, state: string | undefined) => {
      if (state === "QUEUED" || state === "HUMAN_ACTIVE") {
        setHandoffState(state as HandoffState);
        connectHandoffSocketRef.current({ conversation_id: sid });
      } else {
        setHandoffState("BOT_ACTIVE");
        setAgentLabel(null);
        if (handoffWsRef.current) {
          try {
            handoffWsRef.current.close();
          } catch {
            // ignore
          }
          handoffWsRef.current = null;
        }
      }
    },
    [],
  );

  const cacheKeyFor = useCallback(
    (id: string | null) => id ?? NEW_CONV_KEY,
    [],
  );

  const getCached = useCallback(
    (key: string) => messagesCacheRef.current.get(key) ?? [],
    [],
  );

  const setCached = useCallback((key: string, msgs: ChatMessage[]) => {
    messagesCacheRef.current.set(key, msgs);
  }, []);

  const isViewingStream = useCallback(
    (viewingId: string | null, session: StreamSession | null = streamSessionRef.current) => {
      if (!session) return false;
      if (viewingId === null) return session.cacheKey === NEW_CONV_KEY;
      return (
        session.resolvedConversationId === viewingId ||
        session.cacheKey === viewingId
      );
    },
    [],
  );

  const syncDisplay = useCallback(
    (viewingId: string | null) => {
      if (viewingId === null) {
        const session = streamSessionRef.current;
        if (session?.cacheKey === NEW_CONV_KEY) {
          setMessages(getCached(NEW_CONV_KEY));
          setIsStreaming(true);
        } else {
          setMessages([]);
          setIsStreaming(false);
        }
        return;
      }

      setMessages(getCached(viewingId));
      setIsStreaming(isViewingStream(viewingId));
    },
    [getCached, isViewingStream],
  );

  const updateStreamMessages = useCallback(
    (updater: (prev: ChatMessage[]) => ChatMessage[]) => {
      const session = streamSessionRef.current;
      if (!session) return;

      const key = session.cacheKey;
      const next = updater(getCached(key));
      setCached(key, next);

      if (isViewingStream(viewingConversationIdRef.current, session)) {
        setMessages(next);
      }
    },
    [getCached, isViewingStream, setCached],
  );

  useEffect(() => {
    fetchDocuments()
      .then(setDocuments)
      .catch(() => setDocuments([]));
  }, []);

  useEffect(() => {
    setAttachedDocNames(clinicalContext.doc_names ?? []);
  }, [clinicalContext.doc_names]);

  useEffect(() => {
    viewingConversationIdRef.current = conversationId;
    setActiveCitations(null);

    if (!conversationId) {
      syncDisplay(null);
      return;
    }

    const cached = getCached(conversationId);
    if (cached.length > 0) {
      syncDisplay(conversationId);
      if (!isViewingStream(conversationId)) {
        let cancelled = false;
        fetchConversation(conversationId)
          .then((conv) => {
            if (cancelled || viewingConversationIdRef.current !== conversationId) return;
            const loaded = messagesFromConversation(conv.messages);
            setCached(conversationId, loaded);
            setMessages(loaded);
            if (conv.clinical_context) {
              setClinicalContext(conv.clinical_context);
            }
            resumeHandoffFromConversation(conversationId, conv.handoff_state);
          })
          .catch((err) => {
            if (!cancelled && viewingConversationIdRef.current === conversationId) {
              setPageError(err);
            }
          });
        return () => {
          cancelled = true;
        };
      }
      return;
    }

    if (isViewingStream(conversationId)) {
      syncDisplay(conversationId);
      return;
    }

    let cancelled = false;
    setLoadingHistory(true);
    fetchConversation(conversationId)
      .then((conv) => {
        if (cancelled || viewingConversationIdRef.current !== conversationId) return;
        const loaded = messagesFromConversation(conv.messages);
        setCached(conversationId, loaded);
        setMessages(loaded);
        if (conv.clinical_context) {
          setClinicalContext(conv.clinical_context);
        }
        resumeHandoffFromConversation(conversationId, conv.handoff_state);
        setPageError(null);
      })
      .catch((err) => {
        if (!cancelled && viewingConversationIdRef.current === conversationId) {
          setPageError(err);
          setMessages([]);
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingHistory(false);
      });

    return () => {
      cancelled = true;
    };
  }, [
    conversationId,
    getCached,
    isViewingStream,
    resumeHandoffFromConversation,
    setCached,
    setClinicalContext,
    syncDisplay,
  ]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isStreaming]);

  const handleEvent = useCallback(
    (assistantId: string, event: ChatEvent) => {
      switch (event.type) {
        case "conversation": {
          const id = event.conversation_id;
          const session = streamSessionRef.current;
          if (session) {
            const pending = getCached(session.cacheKey);
            setCached(id, pending);
            if (session.cacheKey === NEW_CONV_KEY) {
              messagesCacheRef.current.delete(NEW_CONV_KEY);
            }
            session.resolvedConversationId = id;
            session.cacheKey = id;
          }

          const viewing = viewingConversationIdRef.current;
          if (viewing === null || viewing === id) {
            setConversationId(id);
          }
          refreshConversations();
          break;
        }
        case "token":
          updateStreamMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? { ...m, content: m.content + event.content }
                : m,
            ),
          );
          break;
        case "agent_status":
          updateStreamMessages((prev) =>
            prev.map((m) => {
              if (m.id !== assistantId) return m;
              const steps = [...(m.agentSteps ?? [])];
              const idx = steps.findIndex((s) => s.agent === event.agent);
              const step: AgentStatusEvent = {
                agent: event.agent,
                status: event.status as "running" | "complete",
                output: event.output,
              };
              if (idx >= 0) steps[idx] = step;
              else steps.push(step);
              return { ...m, agentSteps: steps };
            }),
          );
          break;
        case "citations":
          updateStreamMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId ? { ...m, citations: event.chunks } : m,
            ),
          );
          break;
        case "web_sources":
          updateStreamMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId ? { ...m, webSources: event.results } : m,
            ),
          );
          break;
        case "faithfulness":
          updateStreamMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? {
                    ...m,
                    faithfulness: {
                      score: event.score,
                      verdict: event.verdict,
                      violations: event.violations,
                    },
                  }
                : m,
            ),
          );
          break;
        case "drug_interaction":
          updateStreamMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId ? { ...m, drugInteraction: event } : m,
            ),
          );
          break;
        case "emergency_warning":
          updateStreamMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? {
                    ...m,
                    emergency: {
                      message: event.message,
                      matched_terms: event.matched_terms,
                    },
                  }
                : m,
            ),
          );
          break;
        case "message_saved": {
          const session = streamSessionRef.current;
          if (!session) break;
          updateStreamMessages((prev) => {
            const next = [...prev];
            // Last user + assistant pair in this stream
            for (let i = next.length - 1; i >= 0; i--) {
              if (next[i].id === assistantId && next[i].role === "assistant") {
                next[i] = {
                  ...next[i],
                  serverId: event.assistant_message_id,
                };
                // Previous user message
                for (let j = i - 1; j >= 0; j--) {
                  if (next[j].role === "user") {
                    next[j] = { ...next[j], serverId: event.user_message_id };
                    break;
                  }
                }
                break;
              }
            }
            return next;
          });
          break;
        }
        case "clinical_disclaimer":
          if (isViewingStream(viewingConversationIdRef.current)) {
            setDisclaimer(event.message);
          }
          break;
        case "handoff_initiated":
          setHandoffState((event.state as HandoffState) || "QUEUED");
          setQueuePosition(event.queue_position);
          connectHandoffSocketRef.current({
            conversation_id:
              streamSessionRef.current?.resolvedConversationId ||
              conversationId ||
              undefined,
          });
          updateStreamMessages((prev) => [
            ...prev,
            {
              id: uid(),
              role: "event",
              content: "Connecting with a human specialist",
            },
          ]);
          break;
        case "queue_position":
          setQueuePosition(event.position);
          if (event.state) setHandoffState(event.state as HandoffState);
          break;
        case "error":
          updateStreamMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId ? { ...m, error: event.message } : m,
            ),
          );
          break;
        default:
          break;
      }
    },
    [
      conversationId,
      getCached,
      isViewingStream,
      refreshConversations,
      setCached,
      setConversationId,
      updateStreamMessages,
    ],
  );

  const connectHandoffSocket = useCallback(
    (hint?: { conversation_id?: string }) => {
      const sid = hint?.conversation_id || conversationId;
      if (!sid) return;
      if (handoffWsRef.current && handoffWsRef.current.readyState === WebSocket.OPEN) return;
      if (handoffWsRef.current) {
        try {
          handoffWsRef.current.close();
        } catch {
          // ignore
        }
        handoffWsRef.current = null;
      }
      const token = getStoredToken();
      const ws = new WebSocket(
        `${wsUrl(`/ws/chat/${sid}`)}?token=${encodeURIComponent(token || "")}`,
      );
      handoffWsRef.current = ws;
      ws.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data) as Record<string, unknown>;
          if (data.type === "state_resume") {
            // First message on every (re)connect — authoritative state so a
            // reload mid-QUEUED shows position immediately instead of
            // waiting on the backend's ~30s position-loop tick. (Issue 18)
            if (data.state) setHandoffState(data.state as HandoffState);
            if (typeof data.queue_position === "number") setQueuePosition(data.queue_position);
            if (typeof data.agent_name === "string" && data.agent_name) {
              setAgentLabel(data.agent_name);
            }
          }
          if (data.type === "queue_position") {
            setQueuePosition(Number(data.position || 0));
            if (data.state) setHandoffState(data.state as HandoffState);
          }
          if (data.type === "agent_connected") {
            setHandoffState("HUMAN_ACTIVE");
            const name = String(data.agent_name || "a specialist");
            setAgentLabel(name);
            const key = cacheKeyFor(sid);
            const cached = getCached(key);
            const already = cached.some(
              (m) => m.role === "event" && m.content.toLowerCase().startsWith("connected with"),
            );
            if (!already) {
              const next = [
                ...cached,
                {
                  id: uid(),
                  role: "event" as const,
                  content: `Connected with ${name}`,
                },
              ];
              setCached(key, next);
              setMessages(next);
            }
          }
          if (data.type === "agent_message") {
            const name = String(data.agent_name || "Specialist");
            setAgentLabel((prev) => prev || name);
            const key = cacheKeyFor(sid);
            const next = [
              ...getCached(key),
              {
                id: uid(),
                role: "assistant" as const,
                senderLabel: name,
                content: String(data.content || ""),
              },
            ];
            setCached(key, next);
            setMessages(next);
          }
          if (data.type === "conversation_resolved" || data.type === "agent_disconnected") {
            setHandoffState("BOT_ACTIVE");
            setAgentLabel(null);
            const key = cacheKeyFor(sid);
            const next = [
              ...getCached(key),
              {
                id: uid(),
                role: "event" as const,
                content:
                  data.type === "conversation_resolved"
                    ? "Conversation ended — assistant is back"
                    : "Specialist disconnected — assistant is back",
              },
            ];
            setCached(key, next);
            setMessages(next);
            ws.close();
            handoffWsRef.current = null;
          }
        } catch {
          // ignore
        }
      };
      ws.onclose = () => {
        if (handoffWsRef.current === ws) handoffWsRef.current = null;
      };
    },
    [cacheKeyFor, conversationId, getCached, setCached],
  );
  connectHandoffSocketRef.current = connectHandoffSocket;

  const triggerHandoff = useCallback(async () => {
    if (!conversationId) {
      setPageError(new Error("Start a conversation before requesting a human."));
      return;
    }
    try {
      const result = await requestHandoff(conversationId);
      setHandoffState((result.state as HandoffState) || "QUEUED");
      setQueuePosition(result.queue_position);
      const key = cacheKeyFor(conversationId);
      const next = [
        ...getCached(key),
        {
          id: uid(),
          role: "event" as const,
          content: "Connecting with a human specialist",
        },
      ];
      setCached(key, next);
      setMessages(next);
      connectHandoffSocket({ conversation_id: conversationId });
    } catch (err) {
      setPageError(err);
    }
  }, [cacheKeyFor, connectHandoffSocket, conversationId, getCached, setCached]);

  const sendMessage = async (query: string) => {
    if (handoffState === "HUMAN_ACTIVE" && handoffWsRef.current) {
      const key = cacheKeyFor(conversationId);
      const userMsg: ChatMessage = { id: uid(), role: "user", content: query };
      const next = [...getCached(key), userMsg];
      setCached(key, next);
      setMessages(next);
      handoffWsRef.current.send(JSON.stringify({ type: "message", content: query }));
      return;
    }

    const key = cacheKeyFor(conversationId);
    const userMsg: ChatMessage = { id: uid(), role: "user", content: query };
    const assistantMsg: ChatMessage = {
      id: uid(),
      role: "assistant",
      content: "",
      agentSteps: [],
    };

    const nextMessages = [...getCached(key), userMsg, assistantMsg];
    setCached(key, nextMessages);
    setMessages(nextMessages);
    setIsStreaming(true);

    streamSessionRef.current = {
      cacheKey: key,
      resolvedConversationId: conversationId,
      assistantMsgId: assistantMsg.id,
    };

    abortRef.current = new AbortController();

    try {
      await streamChat({
        query,
        conversationId: conversationId ?? undefined,
        context: {
          ...clinicalContext,
          doc_names: attachedDocNames.length ? attachedDocNames : clinicalContext.doc_names,
        },
        enableWebSearch: webSearchEnabled,
        signal: abortRef.current.signal,
        onEvent: (event) => handleEvent(assistantMsg.id, event),
      });
      setWebSearchEnabled(false);
      refreshConversations();
      const latest = getCached(streamSessionRef.current?.cacheKey ?? key);
      const lastAssistant = [...latest].reverse().find((m) => m.role === "assistant");
      if (
        lastAssistant?.content?.trim() ===
        "I cannot find this in the provided documents."
      ) {
        setNotFoundStreak((n) => n + 1);
      } else if (lastAssistant?.content?.trim()) {
        setNotFoundStreak(0);
      }
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        updateStreamMessages((prev) =>
          prev.map((m) =>
            m.id === assistantMsg.id
              ? { ...m, error: errorMessage(err) }
              : m,
          ),
        );
      }
    } finally {
      streamSessionRef.current = null;
      abortRef.current = null;
      syncDisplay(viewingConversationIdRef.current);
    }
  };

  const sendMessageRef = useRef(sendMessage);
  sendMessageRef.current = sendMessage;

  useEffect(() => {
    if (!pendingFaqQuery || isStreaming || loadingHistory) return;
    const q = pendingFaqQuery;
    clearPendingFaqQuery();
    messagesCacheRef.current.delete(NEW_CONV_KEY);
    setMessages([]);
    void sendMessageRef.current(q);
  }, [pendingFaqQuery, isStreaming, loadingHistory, clearPendingFaqQuery]);

  const stopStreaming = () => {
    abortRef.current?.abort();
    streamSessionRef.current = null;
    abortRef.current = null;
    syncDisplay(viewingConversationIdRef.current);
  };

  const resolveFeedbackIds = useCallback(
    (msg: ChatMessage) => {
      const convId =
        conversationId ??
        streamSessionRef.current?.resolvedConversationId ??
        null;
      const messageId = msg.serverId ?? msg.id;
      if (!convId) return null;
      return { conversationId: convId, messageId, clientId: msg.id };
    },
    [conversationId],
  );

  const handleFeedbackUp = useCallback(
    async (msg: ChatMessage) => {
      const ids = resolveFeedbackIds(msg);
      if (!ids) {
        setPageError(new Error("Save the conversation before sending feedback."));
        return;
      }
      try {
        await submitMessageFeedback(ids.conversationId, ids.messageId, {
          rating: "up",
        });
        const key = cacheKeyFor(ids.conversationId);
        const next = getCached(key).map((m) =>
          m.id === ids.clientId || m.serverId === ids.messageId
            ? { ...m, feedbackRating: "up" as const }
            : m,
        );
        setCached(key, next);
        if (viewingConversationIdRef.current === ids.conversationId) {
          setMessages(next);
        }
      } catch (err) {
        setPageError(err);
      }
    },
    [cacheKeyFor, getCached, resolveFeedbackIds, setCached],
  );

  const handleFeedbackDown = useCallback(
    (msg: ChatMessage) => {
      const ids = resolveFeedbackIds(msg);
      if (!ids) {
        setPageError(new Error("Save the conversation before sending feedback."));
        return;
      }
      setFeedbackTarget(ids);
    },
    [resolveFeedbackIds],
  );

  const submitDownFeedback = useCallback(
    async ({ comment, correctAnswer }: { comment: string; correctAnswer: string }) => {
      if (!feedbackTarget) return;
      await submitMessageFeedback(
        feedbackTarget.conversationId,
        feedbackTarget.messageId,
        {
          rating: "down",
          comment: comment || undefined,
          correct_answer: correctAnswer || undefined,
        },
      );
      const key = cacheKeyFor(feedbackTarget.conversationId);
      const next = getCached(key).map((m) =>
        m.id === feedbackTarget.clientId || m.serverId === feedbackTarget.messageId
          ? { ...m, feedbackRating: "down" as const }
          : m,
      );
      setCached(key, next);
      if (viewingConversationIdRef.current === feedbackTarget.conversationId) {
        setMessages(next);
      }
      setFeedbackTarget(null);
    },
    [cacheKeyFor, feedbackTarget, getCached, setCached],
  );

  const showEmptyHero =
    messages.length === 0 && !loadingHistory && !isStreaming;

  const chatInput = (
    <ChatInput
      onSend={sendMessage}
      onStop={stopStreaming}
      disabled={isStreaming}
      isStreaming={isStreaming}
      onOpenContext={() => setContextOpen(true)}
      onOpenAttach={() => setAttachOpen(true)}
      suggestions={SUGGESTIONS}
      showSuggestions={showEmptyHero}
      className="px-4 pb-6 sm:px-6"
      webSearchEnabled={webSearchEnabled}
      onToggleWebSearch={() => setWebSearchEnabled((v) => !v)}
      attachedDocNames={attachedDocNames}
      onRemoveAttached={(name) => {
        const next = attachedDocNames.filter((n) => n !== name);
        setAttachedDocNames(next);
        setClinicalContext({ ...clinicalContext, doc_names: next.length ? next : undefined });
      }}
      specialty={clinicalContext.specialty}
      accent={handoffState === "HUMAN_ACTIVE" ? "agent" : "default"}
    />
  );

  // While QUEUED, the input area itself becomes the waiting indicator
  // (position + cancel) rather than a normal, still-typeable input sitting
  // under a separate banner — a purposeful state change, not greyed-out
  // text (UX_AUDIT.md: Chat handoff states).
  const queuedIndicator = (
    <div className="px-4 pb-6 sm:px-6">
      <div className="mx-auto flex w-full max-w-5xl items-center justify-between gap-3 rounded-4xl border border-warning-500/30 bg-warning-50 px-5 py-4 shadow-input">
        <div className="flex items-center gap-3">
          <span className="relative flex h-2.5 w-2.5 shrink-0">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-warning-500 opacity-60" />
            <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-warning-500" />
          </span>
          <div>
            <p className="text-sm font-medium text-warning-700">
              Waiting for a specialist
              {queuePosition != null ? ` · position ${queuePosition + 1}` : ""}
            </p>
            <p className="text-xs text-warning-700/70">
              Hang tight — you'll be connected as soon as one is free.
            </p>
          </div>
        </div>
        <button
          type="button"
          className="shrink-0 rounded-xl border border-warning-500/30 bg-white px-3 py-1.5 text-xs font-medium text-warning-700 transition hover:bg-warning-50"
          onClick={() => {
            // Previously this only reset local UI state — the backend
            // queue entry (and DB state) survived, so a "cancelled" patient
            // could still be routed to an agent later with no one there.
            // Reset the UI regardless of the API outcome (a network hiccup
            // shouldn't trap someone in the waiting view), but surface a
            // failure rather than silently leaving server state stale.
            if (conversationId) {
              cancelHandoff(conversationId).catch((err) => setPageError(err));
            }
            setHandoffState("BOT_ACTIVE");
            handoffWsRef.current?.close();
            handoffWsRef.current = null;
          }}
        >
          Cancel
        </button>
      </div>
    </div>
  );

  return (
    <div className="flex h-full flex-col">
      {pageError != null && (
        <div className="shrink-0 px-6 pt-4">
          <ErrorBanner
            error={pageError}
            onRetry={() => {
              setPageError(null);
              if (conversationId) {
                setLoadingHistory(true);
                fetchConversation(conversationId)
                  .then((conv) => {
                    setMessages(messagesFromConversation(conv.messages));
                    if (conv.clinical_context) setClinicalContext(conv.clinical_context);
                  })
                  .catch(setPageError)
                  .finally(() => setLoadingHistory(false));
              }
            }}
          />
        </div>
      )}
      {showEmptyHero ? (
        <div className="flex min-h-0 flex-1 flex-col items-center justify-center px-4 pb-6 sm:px-6">
          <div className="mb-8 w-full max-w-5xl text-center">
            <h1 className="text-3xl font-bold tracking-tight text-oky-text">
              AI Chatbot
            </h1>
            <p className="mt-2 text-sm text-oky-muted">
              Ask anything about your clinical data, documents, and guidelines.
            </p>
          </div>
          {chatInput}
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col">
          {handoffState === "HUMAN_ACTIVE" && (
            <div className="mx-4 mt-3 flex items-center gap-2 rounded-xl border border-agent-500/25 bg-agent-50 px-4 py-2 text-sm text-agent-700 sm:mx-6">
              <span className="h-2 w-2 rounded-full bg-agent-500" />
              Connected to {agentLabel || "a specialist"}
            </div>
          )}
          <div className="flex min-h-0 flex-1">
            <div className="flex min-w-0 flex-1 flex-col">
              <div className="flex-1 overflow-y-auto">
                {loadingHistory && messages.length === 0 ? (
                  <div className="flex h-full items-center justify-center text-sm text-oky-muted">
                    Loading conversation…
                  </div>
                ) : (
                  <div className="mx-auto w-full max-w-5xl pb-4 pt-2">
                    {messages.map((msg, i) => (
                      <ChatMessageView
                        key={msg.id}
                        message={msg}
                        isStreaming={
                          isStreaming &&
                          i === messages.length - 1 &&
                          msg.role === "assistant"
                        }
                        onShowCitations={
                          msg.citations?.length
                            ? () => setActiveCitations(msg.citations!)
                            : undefined
                        }
                        onFeedbackUp={
                          msg.role === "assistant"
                            ? () => handleFeedbackUp(msg)
                            : undefined
                        }
                        onFeedbackDown={
                          msg.role === "assistant"
                            ? () => handleFeedbackDown(msg)
                            : undefined
                        }
                        onRequestHandoff={
                          msg.role === "assistant" && handoffState === "BOT_ACTIVE"
                            ? () => void triggerHandoff()
                            : undefined
                        }
                        showNotFoundPrompt={
                          msg.role === "assistant" &&
                          i === messages.length - 1 &&
                          notFoundStreak >= 2 &&
                          handoffState === "BOT_ACTIVE"
                        }
                      />
                    ))}
                    <div ref={bottomRef} />
                  </div>
                )}
              </div>

              {handoffState === "QUEUED" ? queuedIndicator : chatInput}
            </div>

            {activeCitations && (
              <CitationsPanel
                chunks={activeCitations}
                onClose={() => setActiveCitations(null)}
              />
            )}
          </div>
        </div>
      )}

      <ClinicalContextModal
        open={contextOpen}
        onClose={() => setContextOpen(false)}
        context={clinicalContext}
        onSave={(ctx) => {
          setClinicalContext(ctx);
          setAttachedDocNames(ctx.doc_names ?? []);
        }}
        documents={documents}
      />

      <AttachModal
        open={attachOpen}
        onClose={() => setAttachOpen(false)}
        documents={documents}
        attachedDocNames={attachedDocNames}
        onAttach={(docNames) => {
          setAttachedDocNames(docNames);
          setClinicalContext({ ...clinicalContext, doc_names: docNames.length ? docNames : undefined });
        }}
        onDocumentsChange={() => {
          fetchDocuments()
            .then(setDocuments)
            .catch(() => setDocuments([]));
        }}
      />

      <DisclaimerModal
        open={!!disclaimer}
        message={disclaimer ?? ""}
        onAccept={() => setDisclaimer(null)}
      />

      <FeedbackModal
        open={feedbackTarget != null}
        onClose={() => setFeedbackTarget(null)}
        onSubmit={submitDownFeedback}
      />
    </div>
  );
}
