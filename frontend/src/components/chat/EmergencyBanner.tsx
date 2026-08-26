interface Props {
  message: string;
  matchedTerms: string[];
}

export default function EmergencyBanner({ message, matchedTerms }: Props) {
  return (
    <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3">
      <p className="text-sm font-semibold text-red-400">{message}</p>
      {matchedTerms.length > 0 && (
        <p className="mt-1 text-xs text-red-300/80">
          Matched: {matchedTerms.join(", ")}
        </p>
      )}
    </div>
  );
}
