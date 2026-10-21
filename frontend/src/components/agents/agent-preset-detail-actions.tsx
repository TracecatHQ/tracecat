"use client"

import { LayersPlus, Loader2 } from "lucide-react"
import type { ReactNode } from "react"
import type { AgentPresetCreate } from "@/client"
import { AgentPresetChatToggle } from "@/components/agents/agent-preset-chat-toggle"
import { AgentPresetVersionHistory } from "@/components/agents/agent-preset-version-history"
import { getAgentPresetVersionNumber } from "@/components/agents/agent-preset-version-select"
import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useAgentPresetVersions } from "@/hooks/use-agent-presets"
import { useAgentPresetDetailContext } from "@/providers/agent-preset-detail"

/**
 * Props for the agent preset detail action group, driven by live form state.
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
  isDirty?: boolean
  cutsVersion?: boolean
  onDiscard?: () => void
  syncActions?: ReactNode
}

/**
 * Chat visibility, discard, sync, version history, and publish actions,
 * laid out like the skill detail header. Rendered in the global controls
 * header or inline in the document panel.
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
  isDirty = canSubmit,
  cutsVersion = true,
  onDiscard,
  syncActions,
}: AgentPresetDetailActionsProps) {
  const { versions } = useAgentPresetVersions(
    workspaceId,
    presetId ?? undefined,
    {
      enabled: Boolean(presetId),
    }
  )
  const currentVersionNumber = getAgentPresetVersionNumber(
    versions,
    currentVersionId
  )
  const publish = getPublishCopy({
    presetId,
    isDirty,
    cutsVersion,
    currentVersionNumber,
    submitLabel,
  })
  const active = canSubmit && !isSaving

  return (
    <>
      {presetId ? (
        <AgentPresetChatToggle
          workspaceId={workspaceId}
          presetId={presetId}
          disabled={isSaving}
        />
      ) : null}
      {isDirty && presetId && onDiscard ? (
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="h-7 px-2.5 text-xs font-normal text-muted-foreground"
          onClick={onDiscard}
          disabled={isSaving}
        >
          Discard
        </Button>
      ) : null}
      {syncActions}
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
            className="size-7 aria-disabled:cursor-default aria-disabled:opacity-50 aria-disabled:hover:bg-transparent"
            onClick={() => {
              if (active) onPublish()
            }}
            aria-disabled={!active}
            aria-label={publish.label}
          >
            {isSaving ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <LayersPlus className="size-4" />
            )}
          </Button>
        </TooltipTrigger>
        <TooltipContent className="max-w-60">{publish.tip}</TooltipContent>
      </Tooltip>
    </>
  )
}

function getPublishCopy({
  presetId,
  isDirty,
  cutsVersion,
  currentVersionNumber,
  submitLabel,
}: {
  presetId: string | null
  isDirty: boolean
  cutsVersion: boolean
  currentVersionNumber: number | null
  submitLabel: string
}): { label: string; tip: string } {
  if (!presetId) {
    return { label: submitLabel, tip: "Creates the agent as v1." }
  }
  if (!isDirty) {
    return {
      label: "Publish",
      tip:
        currentVersionNumber !== null
          ? `No changes to publish. v${currentVersionNumber} is live.`
          : "No changes to publish.",
    }
  }
  if (!cutsVersion) {
    return {
      label: "Publish",
      tip: "Publishes name and description. The version does not change.",
    }
  }
  const next = currentVersionNumber !== null ? currentVersionNumber + 1 : null
  return {
    label: "Publish",
    tip:
      next !== null
        ? `Publish v${next}. Chat and workflows run it right away.`
        : "Publish a new version.",
  }
}

/**
 * Controls-header wrapper for the agent preset detail actions. Pulls the
 * handles the preset form registered into `AgentPresetDetailProvider`.
 *
 * @returns The header actions, or only sync actions until the form registers.
 */
export function AgentPresetDetailHeaderActions({
  syncActions,
}: {
  syncActions?: ReactNode
}) {
  const detail = useAgentPresetDetailContext()
  const actions = detail?.actions
  if (!actions) {
    return syncActions ?? null
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
      isDirty={actions.isDirty}
      cutsVersion={actions.cutsVersion}
      onDiscard={actions.discard}
      syncActions={syncActions}
    />
  )
}
