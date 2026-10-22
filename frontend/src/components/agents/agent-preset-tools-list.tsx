"use client"

import { useFormContext, useWatch } from "react-hook-form"
import {
  ToolSelectionList,
  type ToolSelectionListProps,
} from "@/components/tools/tool-selection-list"
import type {
  PresetToolFields,
  ToolSelectionValue,
} from "@/lib/agent-preset-tools"

/** Props for the preset form's tools list; the selection comes from the form. */
export type AgentPresetToolsListProps = Omit<
  ToolSelectionListProps,
  "value" | "onChange"
>

/** The tool fields plus per-tool tunnels, when the form has them. */
type PresetToolFormFields = PresetToolFields & {
  toolTunnels?: Record<string, string>
}

/** Bind the preset form's tool fields to a controlled value and change handler. */
export function usePresetToolFields(): {
  value: PresetToolFields
  onChange: (next: ToolSelectionValue) => void
} {
  const { control, setValue, getValues } =
    useFormContext<PresetToolFormFields>()
  const value: PresetToolFields = {
    actions: useWatch({ control, name: "actions" }),
    mcpIntegrations: useWatch({ control, name: "mcpIntegrations" }),
    namespaces: useWatch({ control, name: "namespaces" }),
    toolApprovals: useWatch({ control, name: "toolApprovals" }),
  }
  // Compare against the value the view derived `next` from, so only fields
  // whose reference changed are written and a no-op leaves the form clean.
  function onChange(next: ToolSelectionValue) {
    if (next.actions !== value.actions) {
      setValue("actions", next.actions, { shouldDirty: true })
      dropRemovedToolTunnels(next.actions)
    }
    if (next.mcpIntegrations !== value.mcpIntegrations)
      setValue("mcpIntegrations", next.mcpIntegrations, { shouldDirty: true })
    if (next.namespaces && next.namespaces !== value.namespaces)
      setValue("namespaces", next.namespaces, { shouldDirty: true })
    if (next.toolApprovals && next.toolApprovals !== value.toolApprovals)
      setValue("toolApprovals", next.toolApprovals, { shouldDirty: true })
  }
  // The backend rejects a tunnel for a tool the preset no longer has, so a
  // tool removed by any path (picker or rail) takes its tunnel with it.
  function dropRemovedToolTunnels(nextActions: string[]) {
    const tunnels = getValues("toolTunnels")
    if (!tunnels) return
    const kept = new Set(nextActions)
    const removed = value.actions.filter(
      (key) => !kept.has(key) && key in tunnels
    )
    if (removed.length === 0) return
    const next = { ...tunnels }
    for (const key of removed) delete next[key]
    setValue("toolTunnels", next, { shouldDirty: true })
  }
  return { value, onChange }
}

/** React Hook Form adapter for the preset builder. */
export function AgentPresetToolsList(props: AgentPresetToolsListProps) {
  const { value, onChange } = usePresetToolFields()
  return <ToolSelectionList {...props} value={value} onChange={onChange} />
}
