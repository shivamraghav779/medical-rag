/** Thin horizontal rule with centered label — used for handoff start/end. */
export default function EventDivider({
  label,
  compact = false,
}: {
  label: string;
  compact?: boolean;
}) {
  return (
    <div
      className={`flex items-center gap-3 ${compact ? "px-1 py-2" : "px-4 py-5"}`}
      role="separator"
      aria-label={label}
    >
      <div className="h-px flex-1 bg-oky-border/70" />
      <span className="shrink-0 text-center text-[11px] font-medium uppercase tracking-wide text-oky-muted">
        {label}
      </span>
      <div className="h-px flex-1 bg-oky-border/70" />
    </div>
  );
}
