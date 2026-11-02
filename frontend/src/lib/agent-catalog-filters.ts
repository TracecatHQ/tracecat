import type {
  AgentFolderDirectoryItem,
  AgentPresetDirectoryItem,
  AgentPresetWorkflowReference,
} from "@/client"

/** URL params holding the agents catalog filters; each value repeats. */
const AGENT_FILTER_PARAMS = {
  mcp: "mcp",
  workflow: "workflow",
} as const

/** Selected agents catalog filters. Each facet matches any of its values. */
export interface AgentCatalogFilters {
  mcpSlugs: string[]
  workflowIds: string[]
}

/** A workflow that runs an agent preset. */
export interface AgentWorkflowRef {
  id: string
  title: string
}

/** Read the catalog filters from URL search params. */
export function parseAgentCatalogFilters(
  params: Pick<URLSearchParams, "getAll"> | null
): AgentCatalogFilters {
  return {
    mcpSlugs: params?.getAll(AGENT_FILTER_PARAMS.mcp) ?? [],
    workflowIds: params?.getAll(AGENT_FILTER_PARAMS.workflow) ?? [],
  }
}

/** Return a copy of `params` with the catalog filters replaced. */
export function writeAgentCatalogFilters(
  params: URLSearchParams | null,
  filters: AgentCatalogFilters
): URLSearchParams {
  const next = new URLSearchParams(params ?? undefined)
  next.delete(AGENT_FILTER_PARAMS.mcp)
  next.delete(AGENT_FILTER_PARAMS.workflow)
  for (const slug of filters.mcpSlugs) {
    next.append(AGENT_FILTER_PARAMS.mcp, slug)
  }
  for (const id of filters.workflowIds) {
    next.append(AGENT_FILTER_PARAMS.workflow, id)
  }
  return next
}

/** Whether any catalog filter is selected. */
export function hasAgentCatalogFilters(filters: AgentCatalogFilters): boolean {
  return filters.mcpSlugs.length > 0 || filters.workflowIds.length > 0
}

/**
 * Group workflow references by preset ID, deduplicated and sorted by title.
 * The paginated scan can repeat a pair across pages.
 */
export function groupWorkflowsByPreset(
  references: AgentPresetWorkflowReference[]
): Map<string, AgentWorkflowRef[]> {
  const byPreset = new Map<string, Map<string, AgentWorkflowRef>>()
  for (const ref of references) {
    const workflows = byPreset.get(ref.preset_id) ?? new Map()
    workflows.set(ref.workflow_id, {
      id: ref.workflow_id,
      title: ref.workflow_title,
    })
    byPreset.set(ref.preset_id, workflows)
  }
  return new Map(
    [...byPreset].map(([presetId, workflows]) => [
      presetId,
      [...workflows.values()].sort((a, b) => a.title.localeCompare(b.title)),
    ])
  )
}

/** List the distinct workflows across presets, sorted by title. */
export function listReferencedWorkflows(
  workflowsByPreset: Map<string, AgentWorkflowRef[]>
): AgentWorkflowRef[] {
  const byId = new Map(
    [...workflowsByPreset.values()].flat().map((ref) => [ref.id, ref])
  )
  return [...byId.values()].sort((a, b) => a.title.localeCompare(b.title))
}

/**
 * Match a catalog item against the filters. Facets combine with AND; values
 * within a facet with OR. Folders never match an active filter.
 */
export function matchesAgentCatalogFilters(
  item: AgentFolderDirectoryItem | AgentPresetDirectoryItem,
  filters: AgentCatalogFilters,
  workflowsByPreset: Map<string, AgentWorkflowRef[]>
): boolean {
  if (!hasAgentCatalogFilters(filters)) return true
  if (item.type === "folder") return false

  if (filters.mcpSlugs.length > 0) {
    const slugs = item.tool_summary?.mcp_slugs ?? []
    if (!filters.mcpSlugs.some((slug) => slugs.includes(slug))) return false
  }
  if (filters.workflowIds.length > 0) {
    const workflows = workflowsByPreset.get(item.id) ?? []
    if (!workflows.some(({ id }) => filters.workflowIds.includes(id))) {
      return false
    }
  }
  return true
}
