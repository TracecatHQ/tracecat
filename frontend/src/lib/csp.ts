/**
 * Content Security Policy construction for the Next.js UI.
 *
 * Kept free of `next/*` imports so it can be evaluated in the edge middleware
 * sandbox and unit tested in plain Node.
 */

const POSTHOG_ORIGIN = "https://*.posthog.com"

/**
 * Parse a space-, comma-, or newline-separated list of origins into normalized
 * HTTP(S) origins.
 *
 * Anything that is not a parseable `http:`/`https:` URL is dropped, so the
 * result can never inject a `;` or a stray directive into the policy. This
 * function never throws: it runs at module scope in the middleware, where a
 * throw would 500 every page.
 *
 * @param raw Raw environment variable value.
 * @returns Deduplicated origins in input order.
 */
export function parseOrigins(raw: string | null | undefined): string[] {
  if (!raw) {
    return []
  }
  const origins: string[] = []
  for (const token of raw.split(/[\s,]+/)) {
    if (!token) {
      continue
    }
    let url: URL
    try {
      url = new URL(token)
    } catch {
      continue
    }
    if (url.protocol !== "http:" && url.protocol !== "https:") {
      continue
    }
    if (!origins.includes(url.origin)) {
      origins.push(url.origin)
    }
  }
  return origins
}

/**
 * Build the `Content-Security-Policy` header value for the UI.
 *
 * @param options.posthogEnabled Allow PostHog script and ingest origins.
 * @param options.extraConnectSrc Extra origins appended to `connect-src`, for
 *   example presigned blob storage buckets the browser uploads to directly.
 * @returns The header value.
 */
export function buildContentSecurityPolicy(options?: {
  posthogEnabled?: boolean
  extraConnectSrc?: readonly string[]
}): string {
  const posthogEnabled = options?.posthogEnabled ?? false
  const extraConnectSrc = options?.extraConnectSrc ?? []

  const connectSrc = ["'self'"]
  if (posthogEnabled) {
    connectSrc.push(POSTHOG_ORIGIN)
  }
  connectSrc.push(...extraConnectSrc)

  const scriptSrc = ["'self'", "'unsafe-inline'"]
  if (posthogEnabled) {
    scriptSrc.push(POSTHOG_ORIGIN)
  }

  return [
    `connect-src ${connectSrc.join(" ")}`,
    "default-src 'self'",
    "worker-src 'self' blob:",
    "frame-ancestors 'none'",
    "img-src 'self' data: blob:",
    "object-src 'none'",
    "base-uri 'self'",
    `script-src ${scriptSrc.join(" ")}`,
    "script-src-attr 'none'",
    "style-src 'self' 'unsafe-inline'",
  ].join("; ")
}
