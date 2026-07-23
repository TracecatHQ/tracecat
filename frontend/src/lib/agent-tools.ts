const EXCLUDED_AGENT_TOOL_ACTIONS = new Set(["core.script.run_python"])

/** Return whether a registry action should be offered as an agent tool. */
export function isAgentToolSelectable(action: string): boolean {
  return !EXCLUDED_AGENT_TOOL_ACTIONS.has(action)
}
