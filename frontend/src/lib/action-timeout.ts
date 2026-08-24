import { $ActionRetryPolicy } from "@/client"

export const DEFAULT_ACTION_TIMEOUT_SECONDS =
  $ActionRetryPolicy.properties.timeout.default

/** Mirrors `AGENT_TIMEOUT_SECONDS_DEFAULT` in `tracecat/agent/constants.py`. */
export const AGENT_TIMEOUT_SECONDS_DEFAULT = 1800

const AGENT_ACTION_TYPES = new Set(["ai.agent", "ai.action", "ai.preset_agent"])

/**
 * Agent-backed platform actions, whose timeout is the agent's maximum active
 * runtime (approval waits excluded) rather than a plain activity timeout.
 */
export function isAgentAction(actionType: string | undefined): boolean {
  return actionType !== undefined && AGENT_ACTION_TYPES.has(actionType)
}
