import { ApiError } from "@/client"

export interface TracecatApiError extends ApiError {
  readonly body: {
    detail: string
  }
}

export function retryHandler(failureCount: number, error: ApiError) {
  // Check for 4XX errors and terminate
  if (Math.floor(error.status / 100) === 4) {
    console.error("Got 4XX error, terminating early")
    return false
  }
  // Retry for all other errors up to 3 times
  return failureCount < 3
}
