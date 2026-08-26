import type { DrugInteractionEvent } from "../../types/api";

const SEVERITY_COLORS: Record<string, string> = {
  MAJOR: "bg-red-500/20 text-red-400 border-red-500/30",
  MODERATE: "bg-amber-500/20 text-amber-400 border-amber-500/30",
  MINOR: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
  NONE: "bg-green-500/20 text-green-400 border-green-500/30",
  UNKNOWN: "bg-oky-purple/5 text-oky-muted border-oky-border",
};

interface Props {
  interaction: DrugInteractionEvent;
}

export default function DrugInteractionCard({ interaction }: Props) {
  const severity = interaction.severity?.toUpperCase() ?? "UNKNOWN";
  const colorClass = SEVERITY_COLORS[severity] ?? SEVERITY_COLORS.UNKNOWN;

  return (
    <div className="card border-l-4 border-l-oky-purple p-4">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold">
          {interaction.drug_a} + {interaction.drug_b}
        </span>
        <span className={`badge border ${colorClass}`}>{severity}</span>
      </div>
      {interaction.description && (
        <p className="text-sm text-oky-muted">{interaction.description}</p>
      )}
      {interaction.clinical_recommendation && (
        <p className="mt-2 text-sm">
          <span className="font-medium text-oky-purple">Recommendation: </span>
          {interaction.clinical_recommendation}
        </p>
      )}
      {interaction.monitoring_parameters && interaction.monitoring_parameters.length > 0 && (
        <p className="mt-2 text-xs text-oky-muted">
          Monitor: {interaction.monitoring_parameters.join(", ")}
        </p>
      )}
      {interaction.source_doc_name && (
        <p className="mt-2 text-xs text-oky-muted">
          Source: {interaction.source_doc_name}
        </p>
      )}
    </div>
  );
}
