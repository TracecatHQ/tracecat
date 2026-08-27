/**
 * Content Security Policy construction for the Next.js UI.
 *
 * Kept free of `next/*` imports so it can be evaluated in the edge middleware
 * sandbox and unit tested in plain Node.
 *
 * `TRACECAT__CSP_CONNECT_SRC_ORIGINS` exists for the browser calls that read a
 * presigned blob storage URL directly, which `connect-src` governs:
 *
 * - `src/lib/skills-studio.tsx` issues a presigned PUT of skill files.
 * - `src/lib/cases/use-attachment-object-url.ts` fetches a presigned
 *   attachment URL for inline image previews.
 *
 * Anchor and navigation downloads, and plain `<img src>` loads, are not
 * governed by `connect-src`, so their origins do not belong in this variable.
 */

const POSTHOG_ORIGIN = "https://*.posthog.com"

/**
 * Shape an accepted host must have after URL normalization: a hostname of
 * letters, digits, `.` and `-` with an optional leading `*.` label, and an
 * optional port. WHATWG URL parsing alone is not enough: it keeps characters
 * such as `;` in a hostname, which would terminate the `connect-src` directive
 * and inject another one.
 */
const HOST_PATTERN = /^(\*\.)?[a-z0-9.-]+(:\d+)?$/
const REDACTED_URL_COMPONENT = "<redacted>"

/** Preserve a rejected token's shape without logging URL secrets. */
function redactRejectedOriginToken(token: string): string {
  const queryIndex = token.indexOf("?")
  const fragmentIndex = token.indexOf("#")
  const hasQuery =
    queryIndex !== -1 && (fragmentIndex === -1 || queryIndex < fragmentIndex)
  const sensitiveSuffixIndexes = [
    hasQuery ? queryIndex : -1,
    fragmentIndex,
  ].filter((index) => index !== -1)
  const baseEnd =
    sensitiveSuffixIndexes.length > 0
      ? Math.min(...sensitiveSuffixIndexes)
      : token.length
  const redactedBase = token
    .slice(0, baseEnd)
    .replace(
      /^([a-z][a-z0-9+.-]*:\/\/)?[^/@\s]+@/i,
      `$1${REDACTED_URL_COMPONENT}@`
    )

  const redactedQuery = hasQuery ? `?${REDACTED_URL_COMPONENT}` : ""
  const redactedFragment =
    fragmentIndex !== -1 ? `#${REDACTED_URL_COMPONENT}` : ""
  return `${redactedBase}${redactedQuery}${redactedFragment}`
}

/**
 * Parse a space-, comma-, or newline-separated list of origins into normalized
 * HTTP(S) origins.
 *
 * A token is kept only if it parses as an `http:`/`https:` URL and its host
 * matches `HOST_PATTERN`: the hostname is restricted to letters, digits, `.`
 * and `-`, with an optional leading `*.` label (CSP host sources accept one)
 * and an optional port. Everything else, including IPv6 literals and any
 * hostname carrying a `;`, is dropped, so the output can never inject a stray
 * directive into the policy. Userinfo, path, and query are stripped, keeping
 * only scheme, host, and port.
 *
 * There is no public-suffix guard, so `https://*.com` is accepted and would
 * allow every `.com` origin; operators should list exact bucket origins.
 *
 * This function never throws: it runs at module scope in the middleware, where
 * a throw would 500 every page.
 *
 * @param raw Raw environment variable value.
 * @param options.onReject Called once per non-empty token that is dropped.
 * @returns Deduplicated origins in input order.
 */
export function parseOrigins(
  raw: string | null | undefined,
  options?: { onReject?: (token: string) => void }
): string[] {
  if (!raw) {
    return []
  }
  const onReject = options?.onReject
  const origins: string[] = []
  for (const token of raw.split(/[\s,]+/)) {
    if (!token) {
      continue
    }
    let url: URL
    try {
      url = new URL(token)
    } catch {
      onReject?.(token)
      continue
    }
    if (url.protocol !== "http:" && url.protocol !== "https:") {
      onReject?.(token)
      continue
    }
    if (!HOST_PATTERN.test(url.host)) {
      onReject?.(token)
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

/**
 * Build the `Content-Security-Policy` header value from an environment.
 *
 * Reads `NEXT_PUBLIC_POSTHOG_KEY` to decide whether PostHog origins are needed,
 * and `TRACECAT__CSP_CONNECT_SRC_ORIGINS` for extra `connect-src` origins.
 * Invalid origin tokens are dropped and reported once through `console.warn`,
 * with URL credentials, queries, and fragments redacted.
 *
 * @param env Environment to read, normally `process.env`.
 * @returns The header value.
 */
export function buildContentSecurityPolicyFromEnv(
  env: Readonly<Record<string, string | undefined>>
): string {
  const rejected: string[] = []
  const extraConnectSrc = parseOrigins(env.TRACECAT__CSP_CONNECT_SRC_ORIGINS, {
    onReject: (token) => rejected.push(token),
  })
  if (rejected.length > 0) {
    const redacted = rejected.map(redactRejectedOriginToken)
    console.warn(
      `TRACECAT__CSP_CONNECT_SRC_ORIGINS: ignoring invalid origins: ${redacted.join(", ")}`
    )
  }
  return buildContentSecurityPolicy({
    posthogEnabled: Boolean(env.NEXT_PUBLIC_POSTHOG_KEY),
    extraConnectSrc,
  })
}
