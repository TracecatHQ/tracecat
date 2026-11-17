import type { CancelablePromise } from "@/client/core/CancelablePromise"

/** Forward query cancellation to the generated SDK's HTTP request. */
export function withAbortSignal<T>(
  signal: AbortSignal,
  request: CancelablePromise<T>
): Promise<T> {
  function cancelRequest() {
    request.cancel()
  }
  if (signal.aborted) {
    cancelRequest()
  } else {
    signal.addEventListener("abort", cancelRequest, { once: true })
  }
  return request.finally(() => {
    signal.removeEventListener("abort", cancelRequest)
  })
}
