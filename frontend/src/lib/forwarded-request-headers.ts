const CLIENT_ATTRIBUTION_HEADERS = ["x-forwarded-for", "user-agent"] as const

/**
 * Copy bounded client attribution headers into a server-side backend request.
 */
export function forwardClientAttributionHeaders(
  source: Headers,
  destination: Headers
): void {
  for (const name of CLIENT_ATTRIBUTION_HEADERS) {
    const value = source.get(name)
    if (value) {
      destination.set(name, value)
    }
  }
}
