"use client"

import { useCallback, useMemo } from "react"
import type { MCPIntegrationRead } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import type { FilterOption } from "@/components/filters/filter-multi-select"
import { getMcpProviderIconId, ProviderIcon } from "@/components/icons"
import { useAgentPresetWorkflowReferences } from "@/hooks/use-agent-presets"
import {
  type AgentCatalogFilters,
  type AgentWorkflowRef,
  groupWorkflowsByPreset,
  listReferencedWorkflows,
  parseAgentCatalogFilters,
  writeAgentCatalogFilters,
} from "@/lib/agent-catalog-filters"
import type { TracecatApiError } from "@/lib/errors"

/** URL-backed agents catalog filters, scoped to what the viewer can read. */
export function useAgentCatalogFilterState({
  searchParams,
  replaceParams,
}: {
  searchParams: URLSearchParams | null
  replaceParams: (params: URLSearchParams) => void
}): {
  filters: AgentCatalogFilters
  setFilters: (filters: AgentCatalogFilters) => void
  canReadWorkflows: boolean
} {
  // The references endpoint needs both scopes.
  const canReadAgents = useScopeCheck("agent:read") === true
  const canReadWorkflows =
    useScopeCheck("workflow:read") === true && canReadAgents

  // Without workflow access the filter is hidden, so a URL value cannot apply.
  const filters = useMemo(() => {
    const parsed = parseAgentCatalogFilters(searchParams)
    return canReadWorkflows ? parsed : { ...parsed, workflowIds: [] }
  }, [canReadWorkflows, searchParams])
  const setFilters = useCallback(
    (next: AgentCatalogFilters) =>
      replaceParams(writeAgentCatalogFilters(searchParams, next)),
    [replaceParams, searchParams]
  )

  return { filters, setFilters, canReadWorkflows }
}

/** Filter options and the workflow references behind the workflow facet. */
export function useAgentCatalogFilterOptions({
  workspaceId,
  filters,
  canReadWorkflows,
  mcpBySlug,
  usedMcpSlugs,
}: {
  workspaceId: string
  filters: AgentCatalogFilters
  canReadWorkflows: boolean
  mcpBySlug: Map<string, MCPIntegrationRead>
  usedMcpSlugs: string[]
}): {
  workflowsByPreset: Map<string, AgentWorkflowRef[]>
  mcpOptions: FilterOption[]
  /** Null without workflow read access. */
  workflowOptions: FilterOption[] | null
  /** Shown when there are no workflow options, reflecting the request state. */
  workflowEmptyMessage: string
  /** An active workflow filter is waiting on references. */
  workflowFilterIsLoading: boolean
  workflowFilterError: TracecatApiError | null
} {
  const {
    workflowReferences,
    workflowReferencesIsLoading,
    workflowReferencesError,
  } = useAgentPresetWorkflowReferences(workspaceId, {
    enabled: canReadWorkflows,
  })
  // A skipped query still returns data cached before the scope was lost.
  const references = canReadWorkflows ? workflowReferences : undefined
  const workflowsByPreset = useMemo(
    () => groupWorkflowsByPreset(references ?? []),
    [references]
  )

  const mcpOptions = useMemo(
    () =>
      [...new Set([...mcpBySlug.keys(), ...usedMcpSlugs])]
        .map(
          (slug): FilterOption => ({
            value: slug,
            label: mcpBySlug.get(slug)?.name ?? slug,
            renderIcon: () => (
              <ProviderIcon
                providerId={getMcpProviderIconId(slug)}
                className="size-4 shrink-0 rounded"
              />
            ),
          })
        )
        .sort((a, b) => a.label.localeCompare(b.label)),
    [mcpBySlug, usedMcpSlugs]
  )
  const workflowOptions = useMemo(
    () =>
      canReadWorkflows
        ? listReferencedWorkflows(workflowsByPreset).map(
            ({ id, title }): FilterOption => ({ value: id, label: title })
          )
        : null,
    [canReadWorkflows, workflowsByPreset]
  )

  let workflowEmptyMessage = "No workflows run an agent."
  if (workflowReferencesIsLoading) {
    workflowEmptyMessage = "Loading workflows..."
  } else if (workflowReferencesError) {
    workflowEmptyMessage = "Failed to load workflows."
  }

  const filteringByWorkflow = filters.workflowIds.length > 0
  return {
    workflowsByPreset,
    mcpOptions,
    workflowOptions,
    workflowEmptyMessage,
    workflowFilterIsLoading: filteringByWorkflow && workflowReferencesIsLoading,
    workflowFilterError: filteringByWorkflow ? workflowReferencesError : null,
  }
}
