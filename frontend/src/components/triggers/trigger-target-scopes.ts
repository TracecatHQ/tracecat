import type { TriggerTargetType } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"

/**
 * The scope a caller needs, on top of `trigger:update`, to make a target of
 * this type live. Mirrors `EXECUTE_SCOPE_BY_TARGET` in the API.
 */
export const EXECUTE_SCOPE_BY_TARGET: Record<TriggerTargetType, string> = {
  workflow: "workflow:execute",
  agent: "agent:execute",
}

/** "run workflows (workflow:execute)": the action and scope, for messages. */
export const RUN_ACTION_BY_TARGET: Record<TriggerTargetType, string> = {
  workflow: `run workflows (${EXECUTE_SCOPE_BY_TARGET.workflow})`,
  agent: `run agents (${EXECUTE_SCOPE_BY_TARGET.agent})`,
}

/**
 * Whether the caller holds the execute scope for a target type: `undefined`
 * while scopes load.
 */
export function useCanRunTarget(): (
  type: TriggerTargetType
) => boolean | undefined {
  const canRunWorkflows = useScopeCheck(EXECUTE_SCOPE_BY_TARGET.workflow)
  const canRunAgents = useScopeCheck(EXECUTE_SCOPE_BY_TARGET.agent)
  return (type) => (type === "agent" ? canRunAgents : canRunWorkflows)
}

/**
 * Whether the caller holds every scope in a list: `undefined` while scopes
 * load. Covers the scopes trigger kinds name in `TRIGGER_KINDS[...].scopes`.
 */
export function useHasScopes(): (
  scopes: readonly string[]
) => boolean | undefined {
  const held: Record<string, boolean | undefined> = {
    "schedule:create": useScopeCheck("schedule:create"),
    "schedule:read": useScopeCheck("schedule:read"),
    "schedule:update": useScopeCheck("schedule:update"),
    "schedule:delete": useScopeCheck("schedule:delete"),
    "workflow:execute": useScopeCheck("workflow:execute"),
  }
  return (scopes) => {
    let all: boolean | undefined = true
    for (const scope of scopes) {
      const has = held[scope]
      if (has === false) {
        return false
      }
      if (has === undefined) {
        all = undefined
      }
    }
    return all
  }
}
