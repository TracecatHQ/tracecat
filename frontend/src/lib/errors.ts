import type { ApiError } from "@/client"

export interface TracecatApiError<T = unknown> extends ApiError {
  readonly body: {
    detail: T
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
