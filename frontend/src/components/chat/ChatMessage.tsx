import ReactMarkdown from "react-markdown";
import { AlertTriangle, Bot, ThumbsDown, ThumbsUp, User } from "lucide-react";
import { useEffect, useState } from "react";
import type { ChatMessage as ChatMessageType } from "../../types/api";
import AgentPipeline from "./AgentPipeline";
import DrugInteractionCard from "./DrugInteractionCard";
import EmergencyBanner from "./EmergencyBanner";
import EventDivider from "./EventDivider";
import ThinkingOrb from "./ThinkingOrb";

interface Props {
  message: ChatMessageType;
  isStreaming?: boolean;
  onShowCitations?: () => void;
  onFeedbackUp?: () => void;
  onFeedbackDown?: () => void;
  onRequestHandoff?: () => void;
  showNotFoundPrompt?: boolean;
}

function FaithfulnessBadge({ score, verdict }: { score: number; verdict: string }) {
  // Counts up from 0 the first time this badge appears, instead of
  // popping in at its final value (UX_AUDIT.md: micro-interactions).
  const target = Math.round(score * 100);
  const [display, setDisplay] = useState(0);
  useEffect(() => {
    let raf: number;
    const start = performance.now();
    const duration = 500;
    const tick = (now: number) => {
      const progress = Math.min(1, (now - start) / duration);
      setDisplay(Math.round(target * progress));
      if (progress < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
    // Deliberately only re-runs if the target itself changes, not on every
    // render, so it counts up once rather than restarting.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target]);

  const color =
    verdict === "PASS"
      ? "bg-emerald-50 text-emerald-600"
      : verdict === "WARN"
        ? "bg-amber-50 text-amber-600"
        : "bg-red-50 text-red-600";

  const explanation =
    verdict === "PASS"
      ? "This answer's claims were checked against the retrieved documents and matched closely."
      : verdict === "WARN"
        ? "Some claims in this answer could not be fully matched to the retrieved documents — verify before relying on it."
        : "This answer's claims did not match the retrieved documents well — verify independently before use.";

  return (
    <span className={`badge ${color}`} title={`${explanation} (score: ${target}%)`}>
      Faithfulness {display}% · {verdict}
    </span>
  );
}

export default function ChatMessage({
  message,
  isStreaming,
  onShowCitations,
  onFeedbackUp,
  onFeedbackDown,
  onRequestHandoff,
  showNotFoundPrompt,
}: Props) {
  if (message.role === "event") {
    return <EventDivider label={message.content} />;
  }

  const isUser = message.role === "user";
  const isThinking = isStreaming && !message.content;
  const rating = message.feedbackRating;
  const canFeedback =
    !isUser &&
    !isStreaming &&
    !!message.content &&
    !!message.serverId &&
    !!onFeedbackUp &&
    !!onFeedbackDown;

  const avatar = (
    <div
      className={`flex shrink-0 items-center justify-center ${
        isUser
          ? "h-9 w-9 rounded-xl bg-white text-oky-text-secondary shadow-sm ring-1 ring-oky-border"
          : isThinking
            ? "h-11 w-11"
            : "h-9 w-9 rounded-xl bg-btn-gradient text-white shadow-sm"
      }`}
    >
      {isUser ? (
        <User className="h-4 w-4" />
      ) : isThinking ? (
        <ThinkingOrb compact steps={message.agentSteps} />
      ) : (
        <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-btn-gradient text-white shadow-sm">
          <Bot className="h-4 w-4" />
        </div>
      )}
    </div>
  );

  return (
    <div
      className={`flex gap-3 px-3 py-4 sm:gap-4 sm:px-4 sm:py-5 ${
        isUser ? "flex-row-reverse animate-slide-in-right" : "flex-row animate-slide-in-left"
      }`}
    >
      {avatar}

      <div
        className={`min-w-0 space-y-3 ${
          isUser ? "flex max-w-[min(100%,42rem)] flex-col items-end" : "flex-1"
        }`}
      >
        <p
          className={`text-xs font-semibold text-oky-muted ${
            isUser ? "text-right" : ""
          }`}
        >
          {isUser ? "You" : message.senderLabel || "Clinical Assistant"}
        </p>

        <div
          className={`rounded-2xl px-4 py-3 ${
            isUser
              ? "bg-oky-purple text-white shadow-sm"
              : "bg-white/60 backdrop-blur-sm"
          }`}
        >
          {message.emergency && (
            <div className="mb-3">
              <EmergencyBanner
                message={message.emergency.message}
                matchedTerms={message.emergency.matched_terms}
              />
            </div>
          )}

          {message.agentSteps && message.agentSteps.length > 0 && (
            <div className="mb-3">
              <AgentPipeline steps={message.agentSteps} />
            </div>
          )}

          {message.drugInteraction && (
            <div className="mb-3">
              <DrugInteractionCard interaction={message.drugInteraction} />
            </div>
          )}

          {isThinking ? (
            // AgentPipeline (above) already carries its own compact
            // "running" indicator once a step exists — the full-size orb
            // is now reserved for the true empty instant before any step
            // event has arrived at all, so it never coexists with the
            // pipeline row (was: full-width intrusion every message,
            // UX_AUDIT.md — Chat page).
            message.agentSteps && message.agentSteps.length > 0 ? null : (
              <div className="flex items-center gap-2 py-1">
                <ThinkingOrb compact steps={message.agentSteps} />
                <span className="text-sm font-medium text-oky-muted">Thinking…</span>
              </div>
            )
          ) : (
            <div className={`prose-chat ${isUser ? "prose-chat-user" : ""}`}>
              {isUser ? (
                <p className="text-white">{message.content}</p>
              ) : (
                <>
                  <ReactMarkdown>{message.content}</ReactMarkdown>
                  {isStreaming && (
                    <span className="ml-0.5 inline-block h-4 w-0.5 animate-pulse-soft rounded bg-oky-purple align-middle" />
                  )}
                </>
              )}
            </div>
          )}
        </div>

        {message.error && (
          <div className="flex items-start gap-2 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            {message.error}
          </div>
        )}

        {!isUser &&
          (message.faithfulness ||
            message.citations?.length ||
            message.webSources?.length ||
            canFeedback) && (
            <div className="flex flex-wrap items-center gap-2">
              {message.faithfulness && (
                <FaithfulnessBadge
                  score={message.faithfulness.score}
                  verdict={message.faithfulness.verdict}
                />
              )}
              {message.citations && message.citations.length > 0 && onShowCitations && (
                <button
                  type="button"
                  onClick={onShowCitations}
                  className="badge cursor-pointer bg-oky-purple/10 text-oky-purple transition hover:bg-oky-purple/20"
                >
                  {message.citations.length} sources
                </button>
              )}
              {message.webSources && message.webSources.length > 0 && (
                <span className="badge bg-blue-50 text-blue-600">
                  {message.webSources.length} PubMed refs
                </span>
              )}

              {canFeedback && (
                <div className="ml-auto flex items-center gap-1">
                  <button
                    type="button"
                    aria-label="Thumbs up"
                    title="Helpful"
                    onClick={onFeedbackUp}
                    disabled={rating === "up"}
                    className={`rounded-lg p-1.5 transition ${
                      rating === "up"
                        ? "bg-emerald-50 text-emerald-600"
                        : "text-oky-muted hover:bg-oky-purple/10 hover:text-oky-purple"
                    }`}
                  >
                    <ThumbsUp className="h-4 w-4" />
                  </button>
                  <button
                    type="button"
                    aria-label="Thumbs down"
                    title="Not helpful"
                    onClick={onFeedbackDown}
                    disabled={rating === "down"}
                    className={`rounded-lg p-1.5 transition ${
                      rating === "down"
                        ? "bg-red-50 text-red-500"
                        : "text-oky-muted hover:bg-red-50 hover:text-red-500"
                    }`}
                  >
                    <ThumbsDown className="h-4 w-4" />
                  </button>
                </div>
              )}
            </div>
          )}

          {!isUser && !isStreaming && message.content && onRequestHandoff && (
            <button
              type="button"
              onClick={onRequestHandoff}
              className="text-left text-xs text-oky-purple underline-offset-2 hover:underline"
            >
              Talk to a human instead
            </button>
          )}

          {showNotFoundPrompt && onRequestHandoff && (
            <div className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
              Having trouble finding what you need?{" "}
              <button type="button" className="font-medium underline" onClick={onRequestHandoff}>
                Connect with a specialist
              </button>
            </div>
          )}

        {!isUser && message.webSources && message.webSources.length > 0 && (
          <div className="space-y-2 rounded-xl border border-oky-border/50 bg-white/70 p-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-oky-muted">
              PubMed sources
            </p>
            {message.webSources.map((src, idx) => (
              <a
                key={`${src.url}-${idx}`}
                href={src.url}
                target="_blank"
                rel="noreferrer"
                className="block rounded-lg px-2 py-1.5 text-sm transition hover:bg-oky-purple/5"
              >
                <span className="font-medium text-oky-purple">{src.title}</span>
                <span className="mt-0.5 block text-xs text-oky-muted">{src.snippet}</span>
              </a>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
