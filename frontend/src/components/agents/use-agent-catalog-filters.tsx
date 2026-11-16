"use client"

import { Lock } from "lucide-react"
import { useCallback, useMemo } from "react"
import type { MCPIntegrationRead } from "@/client"
import { slackPlacementLabel } from "@/components/agents/agent-slack-badges"
import { useScopeCheck } from "@/components/auth/scope-guard"
import type { FilterOption } from "@/components/filters/filter-multi-select"
import { getMcpProviderIconId, ProviderIcon } from "@/components/icons"
import { useAgentSlackPlacements } from "@/hooks/use-agent-channel-bots"
import { useAgentPresetWorkflowReferences } from "@/hooks/use-agent-presets"
import {
  type AgentCatalogFilters,
  type AgentWorkflowRef,
  groupWorkflowsByPreset,
  listReferencedWorkflows,
  parseAgentCatalogFilters,
  SLACK_FILTER_ANY,
  SLACK_FILTER_DEFAULT,
  type SlackPlacement,
  writeAgentCatalogFilters,
} from "@/lib/agent-catalog-filters"
import type { TracecatApiError } from "@/lib/errors"

/** URL-backed agents catalog filters, scoped to what the viewer can read. */
export function useAgentCatalogFilterState({
  workspaceId,
  searchParams,
  replaceParams,
}: {
  workspaceId: string
  searchParams: URLSearchParams | null
  replaceParams: (params: URLSearchParams) => void
}): {
  filters: AgentCatalogFilters
  setFilters: (filters: AgentCatalogFilters) => void
  canReadWorkflows: boolean
  slackPlacementsByPreset: Map<string, SlackPlacement[]>
  /** An active Slack filter is waiting on placements. */
  slackFilterIsLoading: boolean
} {
  // The references endpoint needs both scopes.
  const canReadAgents = useScopeCheck("agent:read") === true
  const canReadWorkflows =
    useScopeCheck("workflow:read") === true && canReadAgents

  const { slackPlacementsByPreset, slackPlacementsIsLoading } =
    useAgentSlackPlacements(workspaceId)
  const showsSlackFilter =
    slackPlacementsIsLoading || slackPlacementsByPreset.size > 0

  // A hidden filter's URL value cannot apply.
  const filters = useMemo(() => {
    const parsed = parseAgentCatalogFilters(searchParams)
    return {
      ...parsed,
      workflowIds: canReadWorkflows ? parsed.workflowIds : [],
      slackChannels: showsSlackFilter ? parsed.slackChannels : [],
    }
  }, [canReadWorkflows, searchParams, showsSlackFilter])
  const setFilters = useCallback(
    (next: AgentCatalogFilters) =>
      replaceParams(writeAgentCatalogFilters(searchParams, next)),
    [replaceParams, searchParams]
  )

  return {
    filters,
    setFilters,
    canReadWorkflows,
    slackPlacementsByPreset,
    slackFilterIsLoading:
      filters.slackChannels.length > 0 && slackPlacementsIsLoading,
  }
}

/** Filter options and the workflow references behind the workflow facet. */
export function useAgentCatalogFilterOptions({
  workspaceId,
  filters,
  canReadWorkflows,
  mcpBySlug,
  usedMcpSlugs,
  slackPlacementsByPreset,
}: {
  workspaceId: string
  filters: AgentCatalogFilters
  canReadWorkflows: boolean
  mcpBySlug: Map<string, MCPIntegrationRead>
  usedMcpSlugs: string[]
  slackPlacementsByPreset: Map<string, SlackPlacement[]>
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
  /** Null when no agent answers in Slack. */
  slackOptions: FilterOption[] | null
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

  const slackOptions = useMemo(
    () => listSlackFilterOptions(slackPlacementsByPreset),
    [slackPlacementsByPreset]
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
    slackOptions,
  }
}

function listSlackFilterOptions(
  slackPlacementsByPreset: Map<string, SlackPlacement[]>
): FilterOption[] | null {
  if (slackPlacementsByPreset.size === 0) return null
  const channels = new Map<string, FilterOption>()
  for (const placements of slackPlacementsByPreset.values()) {
    for (const placement of placements) {
      if (placement.kind === "channel") {
        channels.set(placement.channelId, {
          value: placement.channelId,
          label: slackPlacementLabel(placement),
          icon: placement.channel?.is_private ? Lock : undefined,
        })
      }
    }
  }
  return [
    ...[...channels.values()].sort((a, b) =>
      a.label.replace(/^#/, "").localeCompare(b.label.replace(/^#/, ""))
    ),
    { value: SLACK_FILTER_DEFAULT, label: "All other channels (default)" },
    { value: SLACK_FILTER_ANY, label: "Any Slack channel" },
  ]
}
