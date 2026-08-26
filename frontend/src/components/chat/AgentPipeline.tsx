import { CheckCircle2 } from "lucide-react";
import type { AgentStatusEvent } from "../../types/api";
import ThinkingOrb from "./ThinkingOrb";

interface Props {
  steps: AgentStatusEvent[];
}

export default function AgentPipeline({ steps }: Props) {
  if (steps.length === 0) return null;

  const hasRunning = steps.some((s) => s.status === "running");

  return (
    <div className="rounded-xl border border-oky-border/50 bg-oky-purple/5 px-3 py-3">
      <div className="flex flex-wrap items-center gap-3">
        {hasRunning && <ThinkingOrb compact steps={steps} />}
        <div className="min-w-0 flex-1">
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-oky-purple">
            Agent pipeline
          </p>
          <div className="flex flex-wrap gap-2">
            {steps.map((step, i) => (
              <div
                key={`${step.agent}-${i}`}
                className={`flex animate-fade-in items-center gap-1.5 rounded-lg px-2 py-1 text-xs shadow-sm transition ${
                  step.status === "running"
                    ? "bg-oky-purple/10 text-oky-purple ring-1 ring-oky-purple/20"
                    : "bg-white/80 text-oky-text-secondary"
                }`}
                title={step.output}
              >
                {step.status === "running" ? (
                  <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-oky-purple" />
                ) : (
                  <CheckCircle2 className="h-3 w-3 text-emerald-500" />
                )}
                <span>{step.agent}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
