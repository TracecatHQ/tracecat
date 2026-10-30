"use client"

import { Info } from "lucide-react"
import { useId } from "react"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useAgentPreset, useUpdateAgentPreset } from "@/hooks/use-agent-presets"
import { useManagedResources } from "@/hooks/use-managed-resources"

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
  // Chat visibility is part of a managed preset's locked configuration.
  const { isManaged } = useManagedResources(workspaceId)
  const presetIsManaged = isManaged("agent_preset", presetId)

  return (
    <div className="flex items-center gap-2">
      <div className="flex items-center gap-1">
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              aria-label="About Use in chat"
              className="rounded-sm text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
            >
              <Info className="size-3.5" />
            </button>
          </TooltipTrigger>
          <TooltipContent className="max-w-60">
            When on, people in this workspace can pick this agent with @ in
            Chat.
          </TooltipContent>
        </Tooltip>
        <Label
          htmlFor={id}
          className="whitespace-nowrap text-xs font-normal [@container_agent-preset-bar_(max-width:35rem)]:sr-only"
        >
          Use in chat
        </Label>
      </div>
      <Switch
        id={id}
        checked={preset?.use_in_chat === true}
        disabled={
          disabled ||
          presetIsManaged ||
          !canUpdateAgent ||
          !preset ||
          updateAgentPresetIsPending
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
