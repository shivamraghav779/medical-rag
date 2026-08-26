// Shared display formatters — raw seconds and backend enum values are fine
// in logs/APIs but not for a human scanning a queue or dashboard.
// (UX_AUDIT.md: raw-seconds wait times appear on the Agent dashboard and
// Analytics Live Queue; one formatter keeps both consistent.)

export function formatDuration(totalSeconds: number): string {
  const s = Math.max(0, Math.round(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${sec}s`;
  return `${sec}s`;
}

const REASON_LABELS: Record<string, string> = {
  patient_request: "Patient requested",
  agent_disconnected: "Specialist disconnected",
  consecutive_faithfulness_fail: "Repeated low-confidence answers",
  consecutive_not_found: "Repeated not-found answers",
  routed_to_agent: "Routed to specialist",
};

export function formatReason(reason?: string | null): string {
  if (!reason) return "—";
  return (
    REASON_LABELS[reason] ??
    reason
      .split("_")
      .map((w) => w[0]?.toUpperCase() + w.slice(1))
      .join(" ")
  );
}
