import type { ApiError } from "@/client"
import { toast } from "@/components/ui/use-toast"

type ErrorHandler<TError, TArguments extends unknown[]> = (
  error: TError,
  ...args: TArguments
) => unknown

type GlobalErrorHandler = (error: unknown) => boolean

export interface TracecatApiError<T = unknown> extends ApiError {
  readonly body: {
    detail?: T
    message?: string | null
  }
}

export function retryHandler(failureCount: number, error: ApiError) {
  // Check for 4XX errors and terminate
  if (Math.floor(error.status / 100) === 4) {
    console.debug("Got 4XX error, terminating early")
    return false
  }
  // Retry for all other errors up to 3 times
  return failureCount < 3
}

/**
 * Type for request validation errors
 * Returned with 422 status code
 */
export interface RequestValidationError {
  loc: string[]
  ctx: {
    [key: string]: unknown
  }
  msg: string
  type: string
}

export function isRequestValidationError(
  obj: unknown
): obj is RequestValidationError {
  return typeof obj === "object" && obj !== null && "loc" in obj && "msg" in obj
}

export function isRequestValidationErrorArray(
  obj: unknown
): obj is RequestValidationError[] {
  return Array.isArray(obj) && obj.every((o) => isRequestValidationError(o))
}

export function getApiErrorDetail(error: unknown): string | null {
  if (!(error instanceof Error)) {
    return null
  }

  const maybeApiError = error as TracecatApiError<unknown>
  const detail = maybeApiError.body?.detail
  if (typeof detail === "string") {
    return detail
  }
  if (detail != null) {
    try {
      return JSON.stringify(detail)
    } catch {
      return error.message
    }
  }
  const message = maybeApiError.body?.message
  if (typeof message === "string" && message.length > 0) {
    return message
  }
  return error.message
}

function getErrorStatus(error: unknown): number | null {
  if (typeof error !== "object" || error === null || !("status" in error)) {
    return null
  }
  return typeof error.status === "number" ? error.status : null
}

const GLOBAL_ERROR_HANDLERS: GlobalErrorHandler[] = [
  (error) => {
    if (getErrorStatus(error) !== 403) {
      return false
    }
    toast({
      title: "Permission denied",
      description: getApiErrorDetail(error) ?? undefined,
      variant: "destructive",
    })
    return true
  },
]

/**
 * Run application-wide error handlers in priority order.
 *
 * @returns Whether a handler displayed user-facing feedback for the error.
 */
export function handleGlobalError(error: unknown): boolean {
  return GLOBAL_ERROR_HANDLERS.some((handler) => handler(error))
}

/** Display a safe destructive toast when no more specific handler exists. */
export function showFallbackErrorToast(
  error: unknown,
  description?: string
): void {
  toast({
    description: description ?? getApiErrorDetail(error) ?? "Please try again.",
    variant: "destructive",
  })
}

/**
 * Compose the mutation error pipeline used by the React Query facade.
 *
 * Global handlers run first, followed by the hook-local handler when present.
 * Mutations without a local handler always receive the shared fallback toast.
 * The variadic argument tuple preserves React Query's complete callback
 * signature without coupling this module to a particular library version.
 */
export function chainError<TError, TArguments extends unknown[]>(
  local?: ErrorHandler<TError, TArguments>
): ErrorHandler<TError, TArguments> {
  return (error, ...args) => {
    if (handleGlobalError(error)) {
      return
    }
    if (local) {
      return local(error, ...args)
    }
    showFallbackErrorToast(error)
  }
}

/**
 * Strip credentials and query parameters from any URLs embedded in free text.
 *
 * Backend connection errors can echo a user-supplied server URI, which may
 * carry secrets in its userinfo (`user:pass@`) or query string. Sanitize before
 * surfacing such text in toasts or the console so those values are not leaked
 * into UI output or logs. Non-URL text is returned unchanged.
 */
export function sanitizeUrlsInText(text: string): string {
  return text.replace(/\bhttps?:\/\/[^\s]+/gi, (match) => {
    try {
      const url = new URL(match)
      url.username = ""
      url.password = ""
      url.search = ""
      url.hash = ""
      return url.toString()
    } catch {
      // Not a parseable URL (e.g. trailing punctuation captured); fall back to
      // dropping everything from the first `?` and any `userinfo@` segment.
      return match
        .replace(/^(https?:\/\/)[^/@]*@/i, "$1")
        .replace(/[?#].*$/, "")
    }
  })
}

const MCP_OAUTH_DISCOVERY_ERROR_PATTERNS = [
  "dynamic registration",
  "discover oauth",
  "oauth discovery",
  "oauth server",
  "authorization-server",
  "oauth endpoint host",
  "registration_endpoint",
]

export function getMcpOAuthConnectErrorDetail(error: unknown): string {
  const detail = getApiErrorDetail(error) ?? "Unknown error"
  const normalized = detail.toLowerCase()
  if (
    MCP_OAUTH_DISCOVERY_ERROR_PATTERNS.some((pattern) =>
      normalized.includes(pattern)
    )
  ) {
    return `MCP OAuth discovery failed. Create an OAuth integration manually, then select it from Advanced. ${detail}`
  }
  return detail
}

/**
 * Extract a structured `code` field from an API error's detail payload, when
 * the backend returns `{ "code": "...", ... }` for machine-readable handling.
 */
export function getApiErrorCode(error: unknown): string | null {
  if (!(error instanceof Error)) {
    return null
  }
  const detail = (error as TracecatApiError<unknown>).body?.detail
  if (
    typeof detail === "object" &&
    detail !== null &&
    "code" in detail &&
    typeof (detail as { code: unknown }).code === "string"
  ) {
    return (detail as { code: string }).code
  }
  return null
}
