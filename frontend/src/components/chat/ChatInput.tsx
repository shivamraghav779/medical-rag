import {
  ArrowUp,
  Globe,
  Loader2,
  Mic,
  MicOff,
  Plus,
  Settings2,
  Sparkles,
  Square,
  Wand2,
  X,
} from "lucide-react";
import { useCallback, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { enhanceQuery, errorMessage } from "../../api/client";
import { useSpeechInput } from "../../hooks/useSpeechInput";

interface Props {
  onSend: (query: string) => void;
  onStop?: () => void;
  disabled?: boolean;
  isStreaming?: boolean;
  onOpenContext: () => void;
  onOpenAttach: () => void;
  suggestions?: string[];
  showSuggestions?: boolean;
  className?: string;
  webSearchEnabled?: boolean;
  onToggleWebSearch?: () => void;
  attachedDocNames?: string[];
  onRemoveAttached?: (docName: string) => void;
  specialty?: string;
  /** "agent" tints the input border with the reserved human-agent accent
   * while HUMAN_ACTIVE, so the input itself signals "you're talking to a
   * person" (UX_AUDIT.md: Chat handoff states). */
  accent?: "default" | "agent";
}

export default function ChatInput({
  onSend,
  onStop,
  disabled,
  isStreaming,
  onOpenContext,
  onOpenAttach,
  suggestions = [],
  showSuggestions = false,
  className = "",
  webSearchEnabled = false,
  onToggleWebSearch,
  attachedDocNames = [],
  onRemoveAttached,
  specialty,
  accent = "default",
}: Props) {
  const [value, setValue] = useState("");
  const [enhancing, setEnhancing] = useState(false);
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const resizeTextarea = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, []);

  const { listening, supported, toggle: toggleVoice } = useSpeechInput({
    onTranscript: (text) => {
      setValue(text);
      resizeTextarea();
    },
    onError: setVoiceError,
  });

  const submit = (text?: string) => {
    const trimmed = (text ?? value).trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setValue("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    submit();
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  const handleEnhance = async () => {
    const draft = value.trim();
    if (!draft || enhancing || disabled) return;
    setEnhancing(true);
    setVoiceError(null);
    try {
      const enhanced = await enhanceQuery(draft, specialty);
      setValue(enhanced);
      resizeTextarea();
      textareaRef.current?.focus();
    } catch (err) {
      setVoiceError(errorMessage(err));
    } finally {
      setEnhancing(false);
    }
  };

  const handleVoiceToggle = () => {
    setVoiceError(null);
    toggleVoice(value);
  };

  const toolBtn = (active?: boolean) =>
    `rounded-lg p-2 transition ${
      active
        ? "bg-oky-purple/15 text-oky-purple"
        : "text-oky-muted hover:bg-oky-purple/5 hover:text-oky-purple"
    } disabled:cursor-not-allowed disabled:opacity-40`;

  return (
    <div className={className}>
      <form onSubmit={handleSubmit} className="mx-auto w-full max-w-5xl">
        {attachedDocNames.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-2">
            {attachedDocNames.map((name) => (
              <span
                key={name}
                className="inline-flex items-center gap-1 rounded-full bg-oky-purple/10 px-3 py-1 text-xs text-oky-purple"
              >
                {name}
                {onRemoveAttached && (
                  <button
                    type="button"
                    onClick={() => onRemoveAttached(name)}
                    className="rounded-full p-0.5 hover:bg-oky-purple/20"
                    title="Remove attachment"
                  >
                    <X className="h-3 w-3" />
                  </button>
                )}
              </span>
            ))}
          </div>
        )}

        <div
          className={`overflow-hidden rounded-4xl border bg-white/95 shadow-input backdrop-blur-xl focus-within:ring-2 focus-within:ring-oky-purple/15 ${
            listening
              ? "border-oky-purple/50 ring-2 ring-oky-purple/20"
              : accent === "agent"
                ? "border-agent-500/50 focus-within:border-agent-500/60"
                : "border-oky-border/60 focus-within:border-oky-purple/30"
          }`}
        >
          {listening && (
            <div className="flex items-center gap-2 border-b border-oky-purple/10 bg-oky-purple/5 px-5 py-2 text-xs text-oky-purple">
              <span className="relative flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-oky-purple opacity-60" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-oky-purple" />
              </span>
              Listening… speak now (Google speech recognition)
            </div>
          )}

          <textarea
            ref={textareaRef}
            value={value}
            onChange={(e) => {
              setValue(e.target.value);
              resizeTextarea();
            }}
            onKeyDown={handleKeyDown}
            placeholder="Ask anything about clinical data and guidelines..."
            rows={1}
            disabled={disabled && !isStreaming}
            className="block w-full resize-none bg-transparent px-5 pb-2 pt-4 text-[15px] leading-relaxed text-oky-text placeholder:text-oky-muted focus:outline-none"
          />

          <div className="flex items-center justify-between gap-3 px-3 pb-3 pt-1">
            <div className="flex items-center gap-0.5 border-r border-oky-border/50 pr-2">
              <button type="button" onClick={onOpenAttach} className={toolBtn(attachedDocNames.length > 0)} title="Attach documents" aria-label="Attach documents">
                <Plus className="h-4 w-4" aria-hidden="true" />
              </button>
              <button type="button" onClick={onToggleWebSearch} className={toolBtn(webSearchEnabled)} title="Search PubMed for supplemental context" aria-label="Toggle PubMed search">
                <Globe className="h-4 w-4" aria-hidden="true" />
              </button>
              <button type="button" onClick={onOpenContext} className={toolBtn()} title="Clinical context" aria-label="Open clinical context">
                <Settings2 className="h-4 w-4" aria-hidden="true" />
              </button>
              <button
                type="button"
                onClick={handleEnhance}
                disabled={!value.trim() || enhancing || disabled}
                className={toolBtn()}
                title="Enhance prompt with AI"
                aria-label="Enhance prompt with AI"
              >
                {enhancing ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                ) : (
                  <Wand2 className="h-4 w-4" aria-hidden="true" />
                )}
              </button>
            </div>

            <div className="flex shrink-0 items-center gap-1.5">
              <button
                type="button"
                onClick={handleVoiceToggle}
                disabled={!supported || (disabled && !isStreaming)}
                className={toolBtn(listening)}
                title={
                  !supported
                    ? "Voice input requires Chrome or Edge"
                    : listening
                      ? "Stop listening"
                      : "Voice input (Google STT)"
                }
              >
                {listening ? <MicOff className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
              </button>
              {isStreaming ? (
                <button
                  type="button"
                  onClick={onStop}
                  className="flex h-9 w-9 items-center justify-center rounded-xl bg-red-50 text-red-500 transition hover:bg-red-100"
                >
                  <Square className="h-3.5 w-3.5 fill-current" />
                </button>
              ) : (
                <button
                  type="submit"
                  disabled={!value.trim() || disabled}
                  className="flex h-9 w-9 items-center justify-center rounded-xl bg-btn-gradient text-white shadow-md transition hover:opacity-90 disabled:opacity-40"
                >
                  <ArrowUp className="h-4 w-4" />
                </button>
              )}
            </div>
          </div>
        </div>

        {(voiceError || (webSearchEnabled && !listening)) && (
          <p className={`mt-2 text-center text-xs ${voiceError ? "text-red-500" : "text-oky-muted"}`}>
            {voiceError ?? "PubMed search enabled for next message"}
          </p>
        )}

        <div className="mt-1.5 flex items-center justify-between px-2 text-[11px] text-oky-muted">
          <span>
            <kbd className="rounded border border-oky-border/60 bg-white/60 px-1 py-0.5 font-sans">Enter</kbd> to send
            {" · "}
            <kbd className="rounded border border-oky-border/60 bg-white/60 px-1 py-0.5 font-sans">Shift+Enter</kbd> for new line
          </span>
          {value.length > 200 && <span>{value.length} characters</span>}
        </div>

        {showSuggestions && suggestions.length > 0 && (
          <div className="mt-4 flex flex-wrap justify-center gap-2">
            {suggestions.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => submit(s)}
                disabled={disabled}
                className="chip max-w-xs truncate"
              >
                <Sparkles className="mr-1.5 inline h-3 w-3 shrink-0 text-oky-purple" />
                {s}
              </button>
            ))}
          </div>
        )}

        <p className="mt-3 text-center text-xs text-oky-muted">
          Clinical decision support only — verify against current guidelines before clinical use.
        </p>
      </form>
    </div>
  );
}
