"use client"

import { useId } from "react"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { useAgentPreset, useUpdateAgentPreset } from "@/hooks/use-agent-presets"

/** Persist the preset's opt-in visibility in chat mention pickers. */
export function AgentPresetChatToggle({
  workspaceId,
  presetId,
  disabled = false,
}: {
  workspaceId: string
  presetId: string
  disabled?: boolean
}) {
  const id = useId()
  const canUpdateAgent = useScopeCheck("agent:update") === true
  const { preset } = useAgentPreset(workspaceId, presetId)
  const { updateAgentPreset, updateAgentPresetIsPending } =
    useUpdateAgentPreset(workspaceId)

  return (
    <div className="flex items-center gap-2">
      <Label htmlFor={id} className="text-xs font-normal">
        Use in chat
      </Label>
      <Switch
        id={id}
        checked={preset?.use_in_chat === true}
        disabled={
          disabled || !canUpdateAgent || !preset || updateAgentPresetIsPending
        }
        onCheckedChange={(checked) => {
          void updateAgentPreset({
            presetId,
            use_in_chat: checked,
          }).catch(() => {})
        }}
      />
    </div>
  )
}
