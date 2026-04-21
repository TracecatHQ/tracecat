type PostAuthRedirectPathParams = {
  isSuperuser: boolean
  eeMultiTenant: boolean
  returnUrl?: string | null
}

const APP_URL_PLACEHOLDER = "https://tracecat.local"
const MCP_AUTH_CONTINUE_PATH = "/oauth/mcp/continue"

function isMcpAuthContinuePath(
  returnUrl: string | null | undefined
): returnUrl is string {
  if (!returnUrl) {
    return false
  }

  try {
    const parsed = new URL(returnUrl, APP_URL_PLACEHOLDER)
    return (
      parsed.pathname === MCP_AUTH_CONTINUE_PATH &&
      Boolean(parsed.searchParams.get("txn"))
    )
  } catch {
    return false
  }
}

/**
 * Resolve the app route an authenticated user should land on after auth.
 */
export function getPostAuthRedirectPath({
  isSuperuser,
  eeMultiTenant,
  returnUrl,
}: PostAuthRedirectPathParams): string {
  if (isMcpAuthContinuePath(returnUrl)) {
    return returnUrl
  }
  if (isSuperuser && eeMultiTenant) {
    return "/admin"
  }
  return returnUrl ?? "/workspaces"
}
