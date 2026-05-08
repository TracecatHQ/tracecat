/**
 * URL helpers that respect the Next.js `basePath` configuration when building
 * absolute URLs from a public app URL and an in-app path.
 *
 * The native `new URL("/foo", base)` constructor replaces the path of `base`
 * with the leading-slash argument, dropping any sub-path that may be part of
 * the base (e.g. when serving the app under `https://example.com/tracecat`).
 * The helpers below preserve that sub-path so generated URLs stay reachable.
 */

/**
 * Append `path` to `base`, preserving any sub-path on `base`.
 *
 * @param path - In-app path that should be relative to the app root, with or
 *   without a leading slash (e.g. "/auth/error" or "auth/error").
 * @param base - Absolute URL that may include a sub-path
 *   (e.g. "https://example.com/tracecat").
 * @returns An absolute `URL` object that targets the requested in-app path.
 *
 * @example
 *   buildAppUrl("/auth/error", "https://example.com/tracecat")
 *   // → URL("https://example.com/tracecat/auth/error")
 */
export function buildAppUrl(path: string, base: string): URL {
  const trimmedBase = base.replace(/\/+$/, "")
  const trimmedPath = path.startsWith("/") ? path : `/${path}`
  return new URL(`${trimmedBase}${trimmedPath}`)
}
