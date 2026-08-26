import { AlertTriangle, RefreshCw, X } from "lucide-react";
import type { ApiRequestError } from "../../api/errors";
import { toApiError } from "../../api/errors";

interface Props {
  error: unknown;
  onRetry?: () => void;
  onDismiss?: () => void;
  className?: string;
}

export default function ErrorBanner({ error, onRetry, onDismiss, className = "" }: Props) {
  const apiErr = toApiError(error) as ApiRequestError;
  const message = apiErr.displayMessage;

  return (
    <div
      className={`flex items-start gap-3 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 ${className}`}
      role="alert"
    >
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="min-w-0 flex-1">
        <p>{message}</p>
        {apiErr.code !== "CLIENT_ERROR" && apiErr.code !== "UNKNOWN_ERROR" && (
          <p className="mt-1 text-xs text-red-500/80">
            {apiErr.code.replace(/_/g, " ")}
            {apiErr.requestId ? ` · ref ${apiErr.requestId.slice(0, 8)}` : ""}
          </p>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-1">
        {apiErr.isRetryable && onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="inline-flex items-center gap-1 rounded-lg bg-red-100 px-2.5 py-1 text-xs font-medium text-red-700 transition hover:bg-red-200"
          >
            <RefreshCw className="h-3 w-3" />
            Retry
          </button>
        )}
        {onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            aria-label="Dismiss"
            className="rounded-lg p-1 text-red-500 transition hover:bg-red-100"
          >
            <X className="h-4 w-4" />
          </button>
        )}
      </div>
    </div>
  );
}
