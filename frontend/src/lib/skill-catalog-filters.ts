import type { AgentPresetReadMinimal } from "@/client"
import type { SkillDirectoryEntry } from "@/hooks/use-skill-folders"

/** URL param holding the selected agent preset IDs; the value repeats. */
const SKILL_AGENT_FILTER_PARAM = "agent"

/** Read the selected agent preset IDs from URL search params. */
export function parseSkillAgentFilter(
  params: Pick<URLSearchParams, "getAll"> | null
): string[] {
  return params?.getAll(SKILL_AGENT_FILTER_PARAM) ?? []
}

/** Return a copy of `params` with the agent filter replaced. */
export function writeSkillAgentFilter(
  params: URLSearchParams | null,
  agentIds: string[]
): URLSearchParams {
  const next = new URLSearchParams(params ?? undefined)
  next.delete(SKILL_AGENT_FILTER_PARAM)
  for (const id of agentIds) {
    next.append(SKILL_AGENT_FILTER_PARAM, id)
  }
  return next
}

/**
 * Skill IDs attached to any selected agent, or null when no agent is
 * selected and every skill matches.
 */
export function skillIdsForAgents(
  presets: Pick<AgentPresetReadMinimal, "id" | "skill_ids">[],
  agentIds: string[]
): Set<string> | null {
  if (agentIds.length === 0) return null
  const selected = new Set(agentIds)
  return new Set(
    presets
      .filter((preset) => selected.has(preset.id))
      .flatMap((preset) => preset.skill_ids ?? [])
  )
}

/** Match a catalog item against the agent filter. Folders never match one. */
export function matchesSkillAgentFilter(
  item: Pick<SkillDirectoryEntry, "type" | "id">,
  agentSkillIds: Set<string> | null
): boolean {
  if (agentSkillIds === null) return true
  return item.type === "skill" && agentSkillIds.has(item.id)
}
