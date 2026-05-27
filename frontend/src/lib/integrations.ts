import type {
  McpIntegrationsListMcpIntegrationsData,
  OAuthGrantType,
} from "@/client"

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
