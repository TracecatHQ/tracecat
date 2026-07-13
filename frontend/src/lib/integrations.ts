import type {
  MCPIntegrationRead,
  McpIntegrationsListMcpIntegrationsData,
  OAuthGrantType,
  PlatformMCPCatalogRead,
} from "@/client"

export const MCP_STDIO_VERIFICATION_POLL_INTERVAL_MS = 3000

type PendingStdioMcpVerificationFields = {
  id: string | null | undefined
  serverType: string | null | undefined
  state: string | null | undefined
  tools: unknown
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
 * IDs for stdio integrations waiting on background verification.
 */
export function getPendingStdioMcpVerificationIds(
  integrations: MCPIntegrationRead[] | undefined
): string[] {
  return (
    integrations?.flatMap((integration) => {
      const fields = {
        id: integration.id,
        serverType: integration.server_type,
        state: integration.state,
        tools: integration.tools,
      }
      return isPendingStdioMcpVerification(fields) ? [fields.id] : []
    }) ?? []
  )
}

/**
 * MCP integration IDs for catalog rows waiting on stdio verification.
 */
export function getPendingStdioMcpCatalogVerificationIds(
  items: PlatformMCPCatalogRead[] | undefined
): string[] {
  return (
    items?.flatMap((item) => {
      const fields = {
        id: item.mcp_integration_id,
        serverType: item.mcp_server_type,
        state: item.state,
        tools: item.tools,
      }
      return isPendingStdioMcpVerification(fields) ? [fields.id] : []
    }) ?? []
  )
}

function isPendingStdioMcpVerification(
  fields: PendingStdioMcpVerificationFields
): fields is PendingStdioMcpVerificationFields & { id: string } {
  return (
    typeof fields.id === "string" &&
    fields.serverType === "stdio" &&
    fields.state === "configured" &&
    fields.tools == null
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
