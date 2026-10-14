"use client"

import { Check, Loader2, Save } from "lucide-react"
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
import { cn } from "@/lib/utils"
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
  /** Render the version chip here; the standalone route shows it in the title. */
  showVersionChip?: boolean
}

/**
 * Chat visibility, discard, save, and sync actions. Rendered in the global
 * controls header or inline in the document panel.
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
  showVersionChip = false,
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
  const save = getSaveCopy({
    presetId,
    isDirty,
    cutsVersion,
    currentVersionNumber,
    submitLabel,
  })
  const active = canSubmit && !isSaving

  return (
    <>
      {showVersionChip && presetId ? (
        <AgentPresetVersionHistory
          workspaceId={workspaceId}
          presetId={presetId}
          currentVersionId={currentVersionId}
          getDraftPayload={getDraftPayload}
          disabled={isSaving}
          versionChip
        />
      ) : null}
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
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className={cn(
              "h-7 gap-1.5 px-2.5 text-xs shadow-none",
              active
                ? "border-foreground bg-foreground font-medium text-background hover:bg-foreground/90 hover:text-background"
                : "bg-background text-muted-foreground"
            )}
            onClick={() => {
              if (active) onPublish()
            }}
            aria-disabled={!active}
            aria-label={save.label}
          >
            <SaveStatusIcon isSaving={isSaving} isDirty={isDirty} />
            {save.label}
          </Button>
        </TooltipTrigger>
        <TooltipContent className="max-w-60">{save.tip}</TooltipContent>
      </Tooltip>
      {syncActions}
    </>
  )
}

function getSaveCopy({
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
      label: "Saved",
      tip:
        currentVersionNumber !== null
          ? `All changes are saved in v${currentVersionNumber}.`
          : "All changes are saved.",
    }
  }
  if (!cutsVersion) {
    return {
      label: "Save",
      tip: "Saves name and description. The version does not change.",
    }
  }
  const next = currentVersionNumber !== null ? currentVersionNumber + 1 : null
  return {
    label: next !== null ? `Save as v${next}` : "Save",
    tip:
      next !== null
        ? `Saves your changes as v${next}. Chat and workflows run it right away.`
        : "Saves your changes as a new version.",
  }
}

/**
 * Version chip for the controls-header title, next to the breadcrumb. Pulls
 * the handles the preset form registered into `AgentPresetDetailProvider`.
 */
export function AgentPresetDetailVersionChip() {
  const actions = useAgentPresetDetailContext()?.actions
  if (!actions?.presetId) {
    return null
  }
  return (
    <AgentPresetVersionHistory
      workspaceId={actions.workspaceId}
      presetId={actions.presetId}
      currentVersionId={actions.currentVersionId}
      getDraftPayload={actions.getDraftPayload}
      disabled={actions.isSaving}
      versionChip
      align="start"
    />
  )
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

function SaveStatusIcon({
  isSaving,
  isDirty,
}: {
  isSaving: boolean
  isDirty: boolean
}) {
  if (isSaving) return <Loader2 className="size-3.5 animate-spin" />
  if (isDirty) return <Save className="size-3.5" />
  return <Check className="size-3.5" />
}
