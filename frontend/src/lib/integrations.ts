import type {
  MCPIntegrationRead,
  McpIntegrationsListMcpIntegrationsData,
  OAuthGrantType,
  PlatformMCPCatalogRead,
} from "@/client"

export const MCP_STDIO_VERIFICATION_POLL_INTERVAL_MS = 3000
export const MCP_STDIO_VERIFICATION_POLL_WINDOW_MS = 5 * 60 * 1000

type PendingStdioMcpVerificationFields = {
  serverType: string | null | undefined
  state: string | null | undefined
  tools: unknown
  updatedAt: string
}

/**
 * Whether a provider ID belongs to a platform-shipped MCP auth provider.
 *
 * Mirrors the backend `MCPAuthProvider` subclass check. All MCPAuthProvider
 * IDs end with the `_mcp` suffix by convention; if a future MCPAuthProvider
 * deviates from that, update both this predicate and the backend's
 * `_is_platform_managed_mcp_integration` check.
 *
 * Custom OAuth providers can also end in `_mcp`, but they are prefixed with
 * `custom_` and should remain on the integrations page.
 */
export function isMcpProvider(providerId: string): boolean {
  return providerId.endsWith("_mcp") && !providerId.startsWith("custom_")
}

/**
 * Whether a stdio integration is likely waiting on background verification.
 *
 * The backend currently exposes async stdio verification as `configured` with
 * `tools == null` until a successful probe persists tools. Bound polling to
 * recently touched rows so old unverified integrations do not poll forever.
 */
export function hasPendingStdioMcpVerification(
  integrations: MCPIntegrationRead[] | undefined,
  now = Date.now()
): boolean {
  return (
    integrations?.some((integration) =>
      isPendingStdioMcpVerification(
        {
          serverType: integration.server_type,
          state: integration.state,
          tools: integration.tools,
          updatedAt: integration.updated_at,
        },
        now
      )
    ) ?? false
  )
}

export function hasPendingStdioMcpCatalogVerification(
  items: PlatformMCPCatalogRead[] | undefined,
  now = Date.now()
): boolean {
  return (
    items?.some((item) =>
      isPendingStdioMcpVerification(
        {
          serverType: item.mcp_server_type,
          state: item.state,
          tools: item.tools,
          updatedAt: item.updated_at,
        },
        now
      )
    ) ?? false
  )
}

function isPendingStdioMcpVerification(
  fields: PendingStdioMcpVerificationFields,
  now: number
): boolean {
  if (
    fields.serverType !== "stdio" ||
    fields.state !== "configured" ||
    fields.tools != null
  ) {
    return false
  }
  return isWithinStdioVerificationPollWindow(Date.parse(fields.updatedAt), now)
}

function isWithinStdioVerificationPollWindow(
  updatedAt: number,
  now: number
): boolean {
  return (
    Number.isNaN(updatedAt) ||
    now - updatedAt <= MCP_STDIO_VERIFICATION_POLL_WINDOW_MS
  )
}

/**
 * Centralized React Query keys for integration data.
 *
 * Keeping these in one place avoids invalidation drift: when a mutation needs
 * to invalidate the OAuth integrations list and the providers list, it can
 * reference the keys by name rather than re-typing the tuple.
 */
export const integrationKeys = {
  providers: (workspaceId: string) => ["providers", workspaceId] as const,
  integrations: (workspaceId: string) => ["integrations", workspaceId] as const,
  integration: (
    providerId: string,
    workspaceId: string,
    grantType: OAuthGrantType
  ) => ["integration", providerId, workspaceId, grantType] as const,
  mcpIntegrations: (
    workspaceId: string,
    source?: McpIntegrationsListMcpIntegrationsData["source"]
  ) => ["mcp-integrations", workspaceId, source] as const,
} as const
