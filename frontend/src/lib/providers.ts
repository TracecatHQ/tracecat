import type { ProviderRead } from "@/client"

/**
 * Check if a provider is an MCP (Model Context Protocol) provider.
 * MCP providers follow the naming convention of ending with "_mcp".
 */
export function isMCPProvider(provider: ProviderRead): boolean {
  return provider.metadata.id.endsWith("_mcp")
}
