/**
 * Action timeout policy. Mirrors `tracecat/agent/constants.py` and
 * `tracecat/dsl/constants.py` — keep the values in sync.
 *
 * The agent timeout ceiling is deployment-configured on the server
 * (TRACECAT__AGENT_SANDBOX_TIMEOUT, one hour unless overridden) and unknown
 * to the client; the server clamps out-of-range values rather than rejecting
 * them, so the UI states no numeric maximum.
 */
export const AGENT_TIMEOUT_SECONDS_DEFAULT = 1800
export const DEFAULT_ACTION_TIMEOUT_SECONDS = 300

const AGENT_ACTION_TYPES = new Set(["ai.agent", "ai.action", "ai.preset_agent"])

/**
 * Agent-backed platform actions, whose timeout is the agent's maximum active
 * runtime (approval waits excluded) rather than a plain activity timeout.
 */
export function isAgentAction(actionType: string | undefined): boolean {
  return actionType !== undefined && AGENT_ACTION_TYPES.has(actionType)
}
