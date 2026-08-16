"use client"

import {
  createContext,
  type ReactNode,
  useContext,
  useMemo,
  useState,
} from "react"
import type { AgentPresetCreate } from "@/client"

/**
 * Action handles the agent preset form registers so surfaces outside its
 * tree (the global controls header) can render version history and publish
 * controls against live form state.
 */
export type AgentPresetDetailActionsState = {
  workspaceId: string
  presetId: string | null
  currentVersionId: string | null
  getDraftPayload: () => AgentPresetCreate | null
  isSaving: boolean
  canSubmit: boolean
  submitLabel: string
  submit: () => void
}

type AgentPresetDetailContextValue = {
  actions: AgentPresetDetailActionsState | null
  registerActions: (actions: AgentPresetDetailActionsState | null) => void
}

const AgentPresetDetailContext =
  createContext<AgentPresetDetailContextValue | null>(null)

/**
 * Provides a registration slot for the active agent preset form so both the
 * preset detail page and the global controls header can render against the
 * same form state.
 *
 * @param props.children Tree consuming the registered actions.
 */
export function AgentPresetDetailProvider({
  children,
}: {
  children: ReactNode
}) {
  const [actions, setActions] = useState<AgentPresetDetailActionsState | null>(
    null
  )
  const value = useMemo(
    () => ({ actions, registerActions: setActions }),
    [actions]
  )
  return (
    <AgentPresetDetailContext.Provider value={value}>
      {children}
    </AgentPresetDetailContext.Provider>
  )
}

/**
 * Reads the agent preset detail registration slot. Returns null when no
 * provider is mounted (e.g., on routes other than /agents/[presetId]).
 */
export function useAgentPresetDetailContext(): AgentPresetDetailContextValue | null {
  return useContext(AgentPresetDetailContext)
}
