export const POST_AUTH_RETURN_URL_COOKIE_NAME = "tracecat_post_auth_return_url"
export const POST_AUTH_RETURN_URL_COOKIE_MAX_AGE_SECONDS = 60 * 15

const APP_URL_PLACEHOLDER = "https://tracecat.local"

function getPostAuthReturnUrlCookieSameSite(secure: boolean): "None" | "Lax" {
  // Cross-site POSTs (SAML ACS) require SameSite=None; browsers require Secure with None.
  return secure ? "None" : "Lax"
}

export function sanitizeReturnUrl(
  value: string | null | undefined
): string | null {
  if (!value) {
    return null
  }

  const trimmedValue = value.trim()
  if (!trimmedValue.startsWith("/") || trimmedValue.startsWith("//")) {
    return null
  }

  try {
    const parsed = new URL(trimmedValue, APP_URL_PLACEHOLDER)
    if (parsed.origin !== APP_URL_PLACEHOLDER) {
      return null
    }
    return `${parsed.pathname}${parsed.search}${parsed.hash}`
  } catch {
    return null
  }
}

export function decodeAndSanitizeReturnUrl(
  value: string | null | undefined
): string | null {
  if (!value) {
    return null
  }

  try {
    return sanitizeReturnUrl(decodeURIComponent(value))
  } catch {
    return sanitizeReturnUrl(value)
  }
}

export function serializePostAuthReturnUrlCookie(
  value: string,
  secure: boolean
): string {
  const sameSite = getPostAuthReturnUrlCookieSameSite(secure)
  const secureAttr = secure ? "; Secure" : ""
  return `${POST_AUTH_RETURN_URL_COOKIE_NAME}=${encodeURIComponent(value)}; Path=/; Max-Age=${POST_AUTH_RETURN_URL_COOKIE_MAX_AGE_SECONDS}; SameSite=${sameSite}${secureAttr}`
}

export function serializeClearPostAuthReturnUrlCookie(secure: boolean): string {
  const sameSite = getPostAuthReturnUrlCookieSameSite(secure)
  const secureAttr = secure ? "; Secure" : ""
  return `${POST_AUTH_RETURN_URL_COOKIE_NAME}=; Path=/; Max-Age=0; SameSite=${sameSite}${secureAttr}`
}
