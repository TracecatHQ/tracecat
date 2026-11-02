"use client"

import { useCallback, useMemo } from "react"
import { useScopeCheck } from "@/components/auth/scope-guard"
import type { FilterOption } from "@/components/filters/filter-multi-select"
import { useAgentPresets } from "@/hooks/use-agent-presets"
import type { TracecatApiError } from "@/lib/errors"
import {
  parseSkillAgentFilter,
  skillIdsForAgents,
  writeSkillAgentFilter,
} from "@/lib/skill-catalog-filters"

/** Agent filter state and options for the skills header. */
export interface SkillAgentFilter {
  /** Whether any agent is selected. */
  active: boolean
  agentIds: string[]
  setAgentIds: (agentIds: string[]) => void
  options: FilterOption[]
  emptyMessage: string
}

/** URL-backed agent filter for the skills catalog. */
export function useSkillAgentFilter({
  workspaceId,
  searchParams,
  replaceParams,
  enabled,
}: {
  workspaceId: string
  searchParams: URLSearchParams | null
  replaceParams: (params: URLSearchParams) => void
  enabled: boolean
}): {
  filter: SkillAgentFilter
  /** Null when no agent is selected. */
  agentSkillIds: Set<string> | null
  /** An active filter is waiting on agents. */
  filterIsLoading: boolean
  filterError: TracecatApiError | null
} {
  const canReadAgents = useScopeCheck("agent:read") === true
  const { presets, presetsIsLoading, presetsError } = useAgentPresets(
    workspaceId,
    { enabled: enabled && canReadAgents }
  )
  const agentIds = useMemo(
    () => parseSkillAgentFilter(searchParams),
    [searchParams]
  )
  const setAgentIds = useCallback(
    (next: string[]) =>
      replaceParams(writeSkillAgentFilter(searchParams, next)),
    [replaceParams, searchParams]
  )
  const options = useMemo(
    () =>
      (presets ?? [])
        .map(({ id, name }): FilterOption => ({ value: id, label: name }))
        .sort((a, b) => a.label.localeCompare(b.label)),
    [presets]
  )
  const agentSkillIds = useMemo(
    () => skillIdsForAgents(presets ?? [], agentIds),
    [agentIds, presets]
  )

  let emptyMessage = "No agents yet."
  if (presetsIsLoading) {
    emptyMessage = "Loading agents..."
  } else if (presetsError) {
    emptyMessage = "Failed to load agents."
  }

  const active = agentIds.length > 0
  return {
    filter: { active, agentIds, setAgentIds, options, emptyMessage },
    agentSkillIds,
    filterIsLoading: active && presetsIsLoading,
    filterError: active ? presetsError : null,
  }
}
