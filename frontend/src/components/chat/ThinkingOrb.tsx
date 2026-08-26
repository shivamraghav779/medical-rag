import type { AgentStatusEvent } from "../../types/api";

interface Props {
  label?: string;
  steps?: AgentStatusEvent[];
  compact?: boolean;
}

function thinkingLabel(steps?: AgentStatusEvent[], fallback = "Thinking"): string {
  const running = steps?.find((s) => s.status === "running");
  if (running) {
    return running.agent.replace(/Agent$/, "").trim() || running.agent;
  }
  return fallback;
}

export default function ThinkingOrb({ label, steps, compact = false }: Props) {
  const statusText = label ?? `${thinkingLabel(steps)}…`;

  if (compact) {
    return (
      <div className="thinking-orb thinking-orb--compact" aria-label={statusText}>
        <div className="thinking-orb__glow" />
        <div className="thinking-orb__ring thinking-orb__ring--1" />
        <div className="thinking-orb__core" />
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center gap-4 py-6">
      <div className="thinking-orb" aria-label={statusText} role="status">
        <div className="thinking-orb__glow" />
        <div className="thinking-orb__ring thinking-orb__ring--1" />
        <div className="thinking-orb__ring thinking-orb__ring--2" />
        <div className="thinking-orb__ring thinking-orb__ring--3" />
        <div className="thinking-orb__core">
          <div className="thinking-orb__core-inner" />
        </div>
        <div className="thinking-orb__orbit">
          <span className="thinking-orb__particle" />
          <span className="thinking-orb__particle" />
          <span className="thinking-orb__particle" />
        </div>
      </div>
      <p className="text-sm font-medium text-oky-purple">{statusText}</p>
    </div>
  );
}
