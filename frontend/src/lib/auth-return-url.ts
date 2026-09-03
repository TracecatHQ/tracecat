export const POST_AUTH_RETURN_URL_COOKIE_NAME = "tracecat_post_auth_return_url"
export const POST_AUTH_RETURN_URL_COOKIE_MAX_AGE_SECONDS = 60 * 15

const APP_URL_PLACEHOLDER = "https://tracecat.local"
const BLOCKED_POST_AUTH_RETURN_URL_PREFIXES = [
  "/auth",
  "/sign-in",
  "/sign-up",
] as const
const MCP_AUTH_CONTINUE_PATH = "/oauth/mcp/continue"
const MCP_AUTH_LEGACY_SELECT_ORG_PATH = "/oauth/mcp/select-org"

function getPostAuthReturnUrlCookieSameSite(secure: boolean): "None" | "Lax" {
  // Cross-site POSTs (SAML ACS) require SameSite=None; browsers require Secure with None.
  return secure ? "None" : "Lax"
}

function isBlockedPostAuthReturnPath(pathname: string): boolean {
  const normalizedPathname = pathname.toLowerCase()
  return BLOCKED_POST_AUTH_RETURN_URL_PREFIXES.some(
    (prefix) =>
      normalizedPathname === prefix ||
      normalizedPathname.startsWith(`${prefix}/`)
  )
}

/**
 * Convert MCP auth return URLs to the current continuation route.
 */
export function normalizeMcpAuthReturnUrl(
  value: string | null | undefined
): string | null {
  if (!value) {
    return null
  }

  try {
    const parsed = new URL(value, APP_URL_PLACEHOLDER)
    if (parsed.origin !== APP_URL_PLACEHOLDER) {
      return null
    }
    if (!parsed.searchParams.get("txn")) {
      return null
    }
    if (parsed.pathname === MCP_AUTH_CONTINUE_PATH) {
      return `${parsed.pathname}${parsed.search}${parsed.hash}`
    }
    if (parsed.pathname === MCP_AUTH_LEGACY_SELECT_ORG_PATH) {
      return `${MCP_AUTH_CONTINUE_PATH}${parsed.search}${parsed.hash}`
    }
    return null
  } catch {
    return null
  }
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
    if (isBlockedPostAuthReturnPath(parsed.pathname)) {
      return null
    }
    const normalizedValue = `${parsed.pathname}${parsed.search}${parsed.hash}`
    const mcpAuthReturnUrl = normalizeMcpAuthReturnUrl(normalizedValue)
    if (mcpAuthReturnUrl) {
      return mcpAuthReturnUrl
    }
    if (parsed.pathname === MCP_AUTH_LEGACY_SELECT_ORG_PATH) {
      return null
    }
    return normalizedValue
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
