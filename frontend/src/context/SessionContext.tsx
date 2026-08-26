import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { ClinicalContext } from "../types/api";

interface SessionContextValue {
  conversationId: string | null;
  setConversationId: (id: string | null) => void;
  newConversation: () => void;
  clinicalContext: ClinicalContext;
  setClinicalContext: (ctx: ClinicalContext) => void;
  refreshConversations: () => void;
  conversationRefreshKey: number;
  pendingFaqQuery: string | null;
  askFaq: (question: string) => void;
  clearPendingFaqQuery: () => void;
}

const SessionContext = createContext<SessionContextValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [conversationId, setConversationIdState] = useState<string | null>(null);
  const [clinicalContext, setClinicalContext] = useState<ClinicalContext>({
    patient_age_group: "adult",
  });
  const [conversationRefreshKey, setConversationRefreshKey] = useState(0);
  const [pendingFaqQuery, setPendingFaqQuery] = useState<string | null>(null);

  const setConversationId = useCallback((id: string | null) => {
    setConversationIdState(id);
  }, []);

  const newConversation = useCallback(() => {
    setConversationIdState(null);
  }, []);

  const refreshConversations = useCallback(() => {
    setConversationRefreshKey((k) => k + 1);
  }, []);

  const askFaq = useCallback((question: string) => {
    const trimmed = question.trim();
    if (!trimmed) return;
    setConversationIdState(null);
    setPendingFaqQuery(trimmed);
  }, []);

  const clearPendingFaqQuery = useCallback(() => {
    setPendingFaqQuery(null);
  }, []);

  const value = useMemo(
    () => ({
      conversationId,
      setConversationId,
      newConversation,
      clinicalContext,
      setClinicalContext,
      refreshConversations,
      conversationRefreshKey,
      pendingFaqQuery,
      askFaq,
      clearPendingFaqQuery,
    }),
    [
      conversationId,
      setConversationId,
      newConversation,
      clinicalContext,
      refreshConversations,
      conversationRefreshKey,
      pendingFaqQuery,
      askFaq,
      clearPendingFaqQuery,
    ],
  );

  return (
    <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
  );
}

export function useSession() {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession must be used within SessionProvider");
  return ctx;
}
