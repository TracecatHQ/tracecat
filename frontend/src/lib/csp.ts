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
 *
 * Inline scripts are allowed only with the per-request nonce from the
 * middleware. Next.js reads it from the request's CSP header and applies it to
 * its own inline scripts; the root layout passes the `x-nonce` request header
 * to the remaining inline scripts (runtime env and theme). There is no
 * `'strict-dynamic'`, so scripts loaded at runtime must still match a host
 * source. Styles keep `'unsafe-inline'`: React `style` props, CodeMirror,
 * Mermaid, KaTeX and Shiki all emit inline styles.
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

/** Request header the middleware uses to pass the CSP nonce to the layout. */
export const NONCE_HEADER = "x-nonce"

/** Options for `buildContentSecurityPolicy`, other than the nonce. */
export interface ContentSecurityPolicyOptions {
  posthogEnabled?: boolean
  extraConnectSrc?: readonly string[]
}

/**
 * Generate a CSP nonce: 128 random bits, base64 encoded.
 *
 * @returns The nonce.
 */
export function generateNonce(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  return btoa(String.fromCharCode(...bytes))
}

/**
 * Build the `Content-Security-Policy` header value for the UI.
 *
 * @param options.nonce Per-request nonce that inline scripts must carry.
 * @param options.posthogEnabled Allow PostHog script and ingest origins.
 * @param options.extraConnectSrc Extra origins appended to `connect-src`, for
 *   example presigned blob storage buckets the browser uploads to directly.
 * @returns The header value.
 */
export function buildContentSecurityPolicy(
  options: ContentSecurityPolicyOptions & { nonce: string }
): string {
  const posthogEnabled = options.posthogEnabled ?? false
  const extraConnectSrc = options.extraConnectSrc ?? []

  const connectSrc = ["'self'"]
  if (posthogEnabled) {
    connectSrc.push(POSTHOG_ORIGIN)
  }
  connectSrc.push(...extraConnectSrc)

  const scriptSrc = ["'self'", `'nonce-${options.nonce}'`]
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
 * Read `buildContentSecurityPolicy` options from an environment.
 *
 * Reads `NEXT_PUBLIC_POSTHOG_KEY` to decide whether PostHog origins are needed,
 * and `TRACECAT__CSP_CONNECT_SRC_ORIGINS` for extra `connect-src` origins.
 * Invalid origin tokens are dropped and reported once through `console.warn`.
 * Only the rejected count is logged because tokens may contain tenant URLs.
 *
 * @param env Environment to read, normally `process.env`.
 * @returns The policy options.
 */
export function contentSecurityPolicyOptionsFromEnv(
  env: Readonly<Record<string, string | undefined>>
): ContentSecurityPolicyOptions {
  let rejectedCount = 0
  const extraConnectSrc = parseOrigins(env.TRACECAT__CSP_CONNECT_SRC_ORIGINS, {
    onReject: () => {
      rejectedCount += 1
    },
  })
  if (rejectedCount > 0) {
    console.warn(
      `TRACECAT__CSP_CONNECT_SRC_ORIGINS: ignoring ${rejectedCount} invalid origin token(s)`
    )
  }
  return {
    posthogEnabled: Boolean(env.NEXT_PUBLIC_POSTHOG_KEY),
    extraConnectSrc,
  }
}
