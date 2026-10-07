import type { ChatCapabilitySelection, WorkspaceChatSettings } from "@/client"

export type ResolvedChatSettings = {
  [Key in keyof WorkspaceChatSettings]-?: Required<ChatCapabilitySelection>
}

/** Missing workspace settings start unrestricted. */
export function resolveChatSettings(
  settings?: WorkspaceChatSettings | null
): ResolvedChatSettings {
  // Fresh arrays per category so one selection cannot alias another.
  function all() {
    return { mode: "all" as const, selected: [] }
  }
  return {
    tools: { ...all(), ...settings?.tools },
    mcp: { ...all(), ...settings?.mcp },
    subagents: { ...all(), ...settings?.subagents },
  }
}

/** Intersect current resources with workspace limits and optional chat choices. */
export function selectChatCapabilities(
  available: string[],
  selection: ChatCapabilitySelection,
  override?: string[] | null
): string[] {
  if (selection.mode === "none") return []
  const selected = new Set(selection.selected)
  const choices = override == null ? null : new Set(override)
  return available.filter(
    (id) =>
      (selection.mode !== "selected" || selected.has(id)) &&
      (choices === null || choices.has(id))
  )
}
