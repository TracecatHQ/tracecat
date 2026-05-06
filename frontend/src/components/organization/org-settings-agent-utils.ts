import type { AgentCatalogRead, AgentCustomProviderRead } from "@/client"

/**
 * Return a stable key for a catalog row.
 */
export function getAgentCatalogEntryKey(
  entry: Pick<AgentCatalogRead, "id">
): string {
  return entry.id
}

/**
 * Resolve a catalog row's source label from the current provider map.
 */
export function getAgentCatalogSourceName(
  entry: Pick<AgentCatalogRead, "custom_provider_id">,
  providersById: ReadonlyMap<
    string,
    Pick<AgentCustomProviderRead, "display_name">
  >
): string {
  if (!entry.custom_provider_id) {
    return "Platform"
  }
  return (
    providersById.get(entry.custom_provider_id)?.display_name ??
    "Custom provider"
  )
}
