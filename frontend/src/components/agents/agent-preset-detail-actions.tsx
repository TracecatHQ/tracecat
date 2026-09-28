"use client"

import { LayersPlus, Loader2 } from "lucide-react"
import type { AgentPresetCreate } from "@/client"
import { AgentPresetVersionHistory } from "@/components/agents/agent-preset-version-history"
import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useAgentPresetDetailContext } from "@/providers/agent-preset-detail"

/**
 * Props for the agent preset detail action group: version history plus the
 * publish button, driven by live form state.
 */
export type AgentPresetDetailActionsProps = {
  workspaceId: string
  presetId: string | null
  currentVersionId: string | null
  getDraftPayload: () => AgentPresetCreate | null
  isSaving: boolean
  canSubmit: boolean
  submitLabel: string
  onPublish: () => void
}

/**
 * Icon-only action group for the agent preset detail surface: version
 * history followed by publish. Rendered either in the global controls
 * header (standalone preset route) or inline in the document panel (case
 * artifact view), matching the skills detail header treatment.
 */
export function AgentPresetDetailActions({
  workspaceId,
  presetId,
  currentVersionId,
  getDraftPayload,
  isSaving,
  canSubmit,
  submitLabel,
  onPublish,
}: AgentPresetDetailActionsProps) {
  return (
    <>
      {presetId ? (
        <AgentPresetVersionHistory
          workspaceId={workspaceId}
          presetId={presetId}
          currentVersionId={currentVersionId}
          getDraftPayload={getDraftPayload}
          disabled={isSaving}
        />
      ) : null}
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            type="button"
            size="icon"
            variant="ghost"
            className="size-7"
            onClick={onPublish}
            disabled={isSaving || !canSubmit}
            aria-label="Publish agent"
          >
            {isSaving ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <LayersPlus className="size-4" />
            )}
          </Button>
        </TooltipTrigger>
        <TooltipContent>{submitLabel}</TooltipContent>
      </Tooltip>
    </>
  )
}

/**
 * Controls-header wrapper for the agent preset detail actions. Pulls the
 * handles the preset form registered into `AgentPresetDetailProvider`.
 *
 * @returns The header actions, or null when no provider is mounted or the
 * form has not registered yet.
 */
export function AgentPresetDetailHeaderActions() {
  const detail = useAgentPresetDetailContext()
  const actions = detail?.actions
  if (!actions) {
    return null
  }
  return (
    <AgentPresetDetailActions
      workspaceId={actions.workspaceId}
      presetId={actions.presetId}
      currentVersionId={actions.currentVersionId}
      getDraftPayload={actions.getDraftPayload}
      isSaving={actions.isSaving}
      canSubmit={actions.canSubmit}
      submitLabel={actions.submitLabel}
      onPublish={actions.submit}
    />
  )
}
