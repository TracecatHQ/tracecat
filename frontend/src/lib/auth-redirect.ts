type PostAuthRedirectPathParams = {
  isSuperuser: boolean
  eeMultiTenant: boolean
  returnUrl?: string | null
}

const APP_URL_PLACEHOLDER = "https://tracecat.local"
const MCP_AUTH_CONTINUE_PATH = "/oauth/mcp/continue"
const MCP_AUTH_LEGACY_SELECT_ORG_PATH = "/oauth/mcp/select-org"

function getMcpAuthReturnUrl(
  returnUrl: string | null | undefined
): string | null {
  if (!returnUrl) {
    return null
  }

  try {
    const parsed = new URL(returnUrl, APP_URL_PLACEHOLDER)
    if (!parsed.searchParams.get("txn")) {
      return null
    }
    if (parsed.pathname === MCP_AUTH_CONTINUE_PATH) {
      return returnUrl
    }
    if (parsed.pathname === MCP_AUTH_LEGACY_SELECT_ORG_PATH) {
      return `${MCP_AUTH_CONTINUE_PATH}${parsed.search}${parsed.hash}`
    }
    return null
  } catch {
    return null
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
  const mcpAuthReturnUrl = getMcpAuthReturnUrl(returnUrl)
  if (mcpAuthReturnUrl) {
    return mcpAuthReturnUrl
  }
  if (isSuperuser && eeMultiTenant) {
    return "/admin"
  }
  return returnUrl ?? "/workspaces"
}
