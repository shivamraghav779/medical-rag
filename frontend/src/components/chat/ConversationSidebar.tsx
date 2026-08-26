import { useCallback, useEffect, useMemo, useState } from "react";
import { MessageSquare, Trash2 } from "lucide-react";
import {
  deleteConversation,
  fetchConversations,
} from "../../api/client";
import type { ConversationSummary } from "../../types/auth";
import ErrorBanner from "../ui/ErrorBanner";

interface Props {
  activeId: string | null;
  onSelect: (id: string | null) => void;
  refreshKey?: number;
  searchQuery?: string;
}

function formatRelativeTime(ts: number): string {
  const diff = Date.now() - ts * 1000;
  const minutes = Math.floor(diff / 60_000);
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(ts * 1000).toLocaleDateString();
}

export default function ConversationSidebar({
  activeId,
  onSelect,
  refreshKey = 0,
  searchQuery = "",
}: Props) {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setConversations(await fetchConversations());
    } catch (err) {
      setError(err);
      setConversations([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  const filtered = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return conversations;
    return conversations.filter(
      (c) =>
        c.title.toLowerCase().includes(q) ||
        c.summary?.toLowerCase().includes(q),
    );
  }, [conversations, searchQuery]);

  const handleDelete = async (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    if (!confirm("Delete this conversation permanently?")) return;
    try {
      await deleteConversation(id);
      if (activeId === id) onSelect(null);
      await load();
    } catch (err) {
      setError(err);
    }
  };

  if (loading && conversations.length === 0 && !error) {
    return (
      <div className="flex flex-1 items-center justify-center p-4">
        <p className="text-xs text-oky-muted">Loading chats…</p>
      </div>
    );
  }

  if (error != null && conversations.length === 0) {
    return (
      <div className="flex flex-1 flex-col gap-3 p-3">
        <ErrorBanner error={error} onRetry={load} />
      </div>
    );
  }

  if (conversations.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center p-6 text-center">
        <MessageSquare className="mb-2 h-8 w-8 text-oky-purple/30" />
        <p className="text-sm font-medium text-oky-text">No conversations yet</p>
        <p className="mt-1 text-xs text-oky-muted">
          Start a new chat to ask clinical questions.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      {error != null && (
        <div className="shrink-0 p-2">
          <ErrorBanner error={error} onRetry={load} />
        </div>
      )}
      {filtered.length === 0 ? (
        <div className="flex flex-1 items-center justify-center p-4">
          <p className="text-xs text-oky-muted">No chats match your search.</p>
        </div>
      ) : (
        <div className="flex-1 space-y-0.5 overflow-y-auto p-2">
          {filtered.map((conv) => {
            const isActive = activeId === conv.id;
            return (
              <div
                key={conv.id}
                className={`group flex items-stretch rounded-xl transition ${
                  isActive ? "bg-oky-purple/10" : "hover:bg-white/80"
                }`}
              >
                <button
                  type="button"
                  onClick={() => onSelect(conv.id)}
                  className={`flex min-w-0 flex-1 items-start gap-2 px-3 py-2.5 text-left ${
                    isActive ? "text-oky-purple" : "text-oky-text-secondary"
                  }`}
                >
                  <MessageSquare
                    className={`mt-0.5 h-3.5 w-3.5 shrink-0 ${
                      isActive ? "text-oky-purple" : "opacity-40"
                    }`}
                  />
                  <span className="min-w-0 flex-1">
                    <span
                      className={`block truncate text-sm ${
                        isActive ? "font-medium" : ""
                      }`}
                    >
                      {conv.title}
                    </span>
                    <span className="mt-0.5 block text-[11px] text-oky-muted">
                      {formatRelativeTime(conv.updated_at)}
                      {conv.message_count > 0 && ` · ${conv.message_count} msgs`}
                    </span>
                  </span>
                </button>
                <button
                  type="button"
                  onClick={(e) => handleDelete(e, conv.id)}
                  className="flex shrink-0 items-center px-2 opacity-0 transition group-hover:opacity-100"
                  title="Delete conversation"
                >
                  <Trash2 className="h-3.5 w-3.5 text-red-400 hover:text-red-500" />
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
