import type { ApiError } from "../types/api";

/** Matches backend `error` envelope from api/core/exceptions.py */
export class ApiRequestError extends Error {
  readonly code: string;
  readonly status: number;
  readonly isRetryable: boolean;
  readonly requestId?: string;

  constructor(
    message: string,
    options: {
      code?: string;
      status?: number;
      isRetryable?: boolean;
      requestId?: string;
    } = {},
  ) {
    super(message);
    this.name = "ApiRequestError";
    this.code = options.code ?? "UNKNOWN_ERROR";
    this.status = options.status ?? 0;
    this.isRetryable = options.isRetryable ?? false;
    this.requestId = options.requestId;
  }

  get displayMessage(): string {
    return friendlyMessage(this.code, this.message);
  }
}

const FRIENDLY: Record<string, string> = {
  UNAUTHORIZED: "Your session expired. Please sign in again.",
  RATE_LIMIT_EXCEEDED: "Too many requests. Please wait a moment and try again.",
  VALIDATION_ERROR: "Invalid request. Check your input and try again.",
  EMPTY_QUERY: "Please enter a message before sending.",
  DOCUMENT_TOO_LARGE: "File exceeds the 25 MB upload limit.",
  DOCUMENT_NOT_FOUND: "Document not found. It may have been deleted.",
  DOCUMENT_ALREADY_EXISTS: "A document with this name already exists.",
  DOCUMENT_PARSING_FAILED: "Could not parse the PDF. Try a different file.",
  CONVERSATION_NOT_FOUND: "Conversation not found.",
  GROQ_RATE_LIMITED: "AI service is busy. Please retry in a few seconds.",
  GROQ_ERROR: "AI service error. Please try again.",
  REDIS_ERROR: "Cache service unavailable. Some features may be degraded.",
  PINECONE_ERROR: "Search index unavailable. Please try again later.",
  INTERNAL_ERROR: "Something went wrong on our side. Please try again.",
};

export function friendlyMessage(code: string, fallback: string): string {
  return FRIENDLY[code] ?? fallback;
}

export async function parseApiError(res: Response): Promise<ApiRequestError> {
  try {
    const body = (await res.json()) as ApiError;
    const err = body.error;
    if (err?.message) {
      return new ApiRequestError(err.message, {
        code: err.code ?? "HTTP_ERROR",
        status: res.status,
        isRetryable: err.is_retryable ?? res.status >= 500,
        requestId: err.request_id,
      });
    }
  } catch {
    // non-JSON body
  }
  return new ApiRequestError(`Request failed (${res.status})`, {
    code: "HTTP_ERROR",
    status: res.status,
    isRetryable: res.status >= 500 || res.status === 429,
  });
}

export function toApiError(err: unknown): ApiRequestError {
  if (err instanceof ApiRequestError) return err;
  if (err instanceof Error) {
    if (err.name === "AbortError") {
      return new ApiRequestError("Request cancelled.", { code: "ABORTED" });
    }
    if (err.message === "Failed to fetch" || err.message.includes("NetworkError")) {
      return new ApiRequestError("Network error. Check your connection and try again.", {
        code: "NETWORK_ERROR",
        isRetryable: true,
      });
    }
    return new ApiRequestError(err.message, { code: "CLIENT_ERROR" });
  }
  return new ApiRequestError("An unexpected error occurred.", { code: "UNKNOWN_ERROR" });
}

export function errorMessage(err: unknown): string {
  return toApiError(err).displayMessage;
}

/** Dispatched when a 401 is returned on an authenticated route. */
export const AUTH_EXPIRED_EVENT = "clinical-rag:auth-expired";

export function dispatchAuthExpired(): void {
  window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT));
}
