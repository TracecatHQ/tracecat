/** Machine-readable login failure codes returned by the backend auth routes. */
export const AUTH_ERROR_CODES = {
  SAML_ENFORCED: "saml_enforced",
} as const

/** A login failure code the auth error page knows how to explain. */
export type AuthErrorCode =
  (typeof AUTH_ERROR_CODES)[keyof typeof AUTH_ERROR_CODES]

const AUTH_ERROR_PATH = "/auth/error"

/**
 * Narrow an arbitrary value to a known {@link AuthErrorCode}.
 */
export function parseAuthErrorCode(value: unknown): AuthErrorCode | null {
  const codes: readonly unknown[] = Object.values(AUTH_ERROR_CODES)
  return codes.includes(value) ? (value as AuthErrorCode) : null
}

/**
 * Extract a known error code from a backend error body of the form
 * `{"detail": {"code": "..."}}`.
 */
export function getAuthErrorCodeFromBody(body: string): AuthErrorCode | null {
  let parsed: unknown
  try {
    parsed = JSON.parse(body)
  } catch {
    return null
  }
  if (typeof parsed !== "object" || parsed === null || !("detail" in parsed)) {
    return null
  }
  const { detail } = parsed
  if (typeof detail !== "object" || detail === null || !("code" in detail)) {
    return null
  }
  return parseAuthErrorCode(detail.code)
}

/**
 * Build the auth error page path, carrying the error code when known.
 */
export function getAuthErrorPath(code: AuthErrorCode | null): string {
  if (!code) {
    return AUTH_ERROR_PATH
  }
  const params = new URLSearchParams({ code })
  return `${AUTH_ERROR_PATH}?${params.toString()}`
}
