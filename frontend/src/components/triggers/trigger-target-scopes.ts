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
