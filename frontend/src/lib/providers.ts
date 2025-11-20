import type { ProviderRead } from "@/client"

/**
 * Check if a provider is an MCP (Model Context Protocol) provider.
 * MCP providers follow the naming convention of ending with "_mcp".
 */
export function isMCPProvider(provider: ProviderRead): boolean {
  return provider.metadata.id.endsWith("_mcp")
}

/**
 * Check if a provider is a custom workspace OAuth provider.
 * Custom providers follow the naming convention of starting with "custom_".
 * Legacy providers may start with "custom-" (hyphen).
 * This is enforced by the backend when creating custom providers.
 */
export function isCustomProvider(provider: ProviderRead): boolean {
  const providerId = provider.metadata.id
  return (
    typeof providerId === "string" &&
    (providerId.startsWith("custom_") || providerId.startsWith("custom-"))
  )
}
