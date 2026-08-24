import { $ActionRetryPolicy } from "@/client"

export const DEFAULT_ACTION_TIMEOUT_SECONDS =
  $ActionRetryPolicy.properties.timeout.default

const AGENT_ACTION_TYPES = new Set(["ai.agent", "ai.action", "ai.preset_agent"])

/**
 * Agent-backed platform actions, whose timeout is the agent's maximum active
 * runtime (approval waits excluded) rather than a plain activity timeout.
 */
export function isAgentAction(actionType: string | undefined): boolean {
  return actionType !== undefined && AGENT_ACTION_TYPES.has(actionType)
}
