import { useCallback, useEffect, useRef, useState } from "react"
import type { AgentSessionsGetSessionVercelResponse } from "@/client"
import { toast } from "@/components/ui/use-toast"
import {
  useAgentPreset,
  useAgentPresets,
  useAgentPresetVersion,
  useAgentPresetVersions,
} from "@/hooks/use-agent-presets"
import { parseChatError, type useUpdateChat } from "@/hooks/use-chat"

type DraftPresetSelection = {
  ownerId: string | null
  value: string | null
}

/** Preset and pinned version to persist onto a session. */
export interface PresetSelection {
  presetId: string | null
  versionId: string | null
}

interface UseChatPresetManagerProps {
  workspaceId: string
  chat: AgentSessionsGetSessionVercelResponse | undefined
  updateChat: ReturnType<typeof useUpdateChat>["updateChat"]
  isUpdatingChat: boolean
  chatLoading: boolean
  selectedChatId: string | undefined
  enabled?: boolean
}

export function useChatPresetManager({
  workspaceId,
  chat,
  updateChat,
  isUpdatingChat,
  chatLoading,
  selectedChatId,
  enabled = true,
}: UseChatPresetManagerProps) {
  const [draftPresetId, setDraftPresetId] =
    useState<DraftPresetSelection | null>(null)
  const [draftPresetVersionId, setDraftPresetVersionId] =
    useState<DraftPresetSelection | null>(null)

  const { presets, presetsIsLoading, presetsError } = useAgentPresets(
    workspaceId,
    { enabled }
  )

  const presetOptions = enabled ? (presets ?? []) : []
  const selectionOwnerId = selectedChatId ?? null
  const effectivePresetId =
    draftPresetId?.ownerId === selectionOwnerId
      ? draftPresetId.value
      : selectedChatId
        ? (chat?.agent_preset_id ?? null)
        : null
  const effectivePresetVersionId =
    draftPresetVersionId?.ownerId === selectionOwnerId
      ? draftPresetVersionId.value
      : selectedChatId
        ? (chat?.agent_preset_version_id ?? null)
        : null

  // Session creation on first send runs in the same tick as the preset change
  // that can precede it, before React has re-rendered with the new draft. The
  // ref is the only copy that is current at that point.
  const pendingSelectionRef = useRef<PresetSelection>({
    presetId: effectivePresetId,
    versionId: effectivePresetVersionId,
  })
  pendingSelectionRef.current = {
    presetId: effectivePresetId,
    versionId: effectivePresetVersionId,
  }
  const getPendingPresetSelection = useCallback(
    (): PresetSelection => pendingSelectionRef.current,
    []
  )

  useEffect(() => {
    if (!selectedChatId) {
      setDraftPresetId(null)
      setDraftPresetVersionId(null)
      return
    }
    setDraftPresetId({
      ownerId: selectedChatId,
      value: chat?.agent_preset_id ?? null,
    })
    setDraftPresetVersionId({
      ownerId: selectedChatId,
      value: chat?.agent_preset_version_id ?? null,
    })
  }, [chat?.agent_preset_id, chat?.agent_preset_version_id, selectedChatId])

  const { preset: selectedPreset, presetIsLoading: selectedPresetLoading } =
    useAgentPreset(workspaceId, effectivePresetId, {
      enabled: enabled && Boolean(effectivePresetId),
    })
  const { versions, versionsIsLoading, versionsError } = useAgentPresetVersions(
    workspaceId,
    effectivePresetId,
    {
      enabled: enabled && Boolean(effectivePresetId),
    }
  )
  const currentPresetVersion =
    versions?.find(
      (version) => version.id === selectedPreset?.current_version_id
    ) ??
    versions?.[0] ??
    null
  const selectedPresetVersionMeta =
    versions?.find((version) => version.id === effectivePresetVersionId) ?? null
  const {
    presetVersion: selectedPresetVersion,
    presetVersionIsLoading: selectedPresetVersionIsLoading,
    presetVersionError: selectedPresetVersionError,
  } = useAgentPresetVersion(
    workspaceId,
    effectivePresetId,
    effectivePresetVersionId,
    {
      enabled:
        enabled &&
        Boolean(workspaceId) &&
        Boolean(effectivePresetId) &&
        Boolean(effectivePresetVersionId),
    }
  )

  /**
   * Apply a preset to the session. Returns false when the write failed, so a
   * caller that depends on the preset landing -- sending a turn from an
   * `@Agent` mention, for one -- can stop instead of running the turn under
   * the previous agent.
   */
  const handlePresetChange = async (
    nextPresetId: string | null
  ): Promise<boolean> => {
    if (nextPresetId === effectivePresetId) {
      return true
    }

    if (!selectedChatId) {
      setDraftPresetId({ ownerId: null, value: nextPresetId })
      setDraftPresetVersionId({ ownerId: null, value: null })
      pendingSelectionRef.current = { presetId: nextPresetId, versionId: null }
      return true
    }

    const previousPresetId = effectivePresetId
    const previousPresetVersionId = effectivePresetVersionId
    setDraftPresetId({ ownerId: selectedChatId, value: nextPresetId })
    setDraftPresetVersionId({ ownerId: selectedChatId, value: null })

    try {
      await updateChat({
        chatId: selectedChatId,
        update: {
          agent_preset_id: nextPresetId,
          agent_preset_version_id: null,
        },
      })
    } catch (error) {
      setDraftPresetId({ ownerId: selectedChatId, value: previousPresetId })
      setDraftPresetVersionId({
        ownerId: selectedChatId,
        value: previousPresetVersionId,
      })
      console.error("Failed to update chat preset:", error)
      toast({
        title: "Failed to update preset",
        description: parseChatError(error),
        variant: "destructive",
      })
      return false
    }

    return true
  }

  const handlePresetVersionChange = async (nextVersionId: string | null) => {
    if (!effectivePresetId || nextVersionId === effectivePresetVersionId) {
      return
    }

    if (!selectedChatId) {
      setDraftPresetVersionId({ ownerId: null, value: nextVersionId })
      return
    }

    const previousVersionId = effectivePresetVersionId
    setDraftPresetVersionId({ ownerId: selectedChatId, value: nextVersionId })

    try {
      await updateChat({
        chatId: selectedChatId,
        update: {
          agent_preset_id: effectivePresetId,
          agent_preset_version_id: nextVersionId,
        },
      })
    } catch (error) {
      setDraftPresetVersionId({
        ownerId: selectedChatId,
        value: previousVersionId,
      })
      console.error("Failed to update chat preset version:", error)
      toast({
        title: "Failed to update version",
        description: parseChatError(error),
        variant: "destructive",
      })
    }
  }

  const presetMenuLabel = selectedPreset?.name ?? "No preset"
  const presetMenuDisabled = !enabled || chatLoading || isUpdatingChat
  const showPresetSpinner =
    presetsIsLoading || isUpdatingChat || chatLoading || selectedPresetLoading
  const presetVersionMenuLabel = selectedPresetVersionMeta
    ? selectedPresetVersionMeta.id === currentPresetVersion?.id
      ? `Current (v${selectedPresetVersionMeta.version})`
      : `Pinned v${selectedPresetVersionMeta.version}`
    : currentPresetVersion
      ? `Current (v${currentPresetVersion.version})`
      : "Current"
  const versionMenuDisabled =
    !enabled ||
    !effectivePresetId ||
    chatLoading ||
    isUpdatingChat ||
    versionsIsLoading
  const showVersionSpinner =
    versionsIsLoading ||
    selectedPresetVersionIsLoading ||
    isUpdatingChat ||
    chatLoading
  const selectedPresetConfig = effectivePresetVersionId
    ? (selectedPresetVersion ?? null)
    : selectedPreset
  const selectedPresetConfigError = effectivePresetVersionId
    ? (selectedPresetVersionError ??
      (!selectedPresetVersionIsLoading && !selectedPresetVersion
        ? new Error("Failed to load pinned preset version.")
        : null))
    : null

  return {
    presets: presetOptions,
    presetsIsLoading,
    presetsError,
    selectedPreset,
    selectedPresetConfig,
    selectedPresetConfigError,
    selectedPresetVersionIsLoading,
    selectedPresetId: effectivePresetId,
    selectedPresetVersionId: effectivePresetVersionId,
    selectedPresetLoading,
    versions,
    versionsIsLoading,
    versionsError,
    currentPresetVersionId: currentPresetVersion?.id ?? null,
    handlePresetChange,
    handlePresetVersionChange,
    getPendingPresetSelection,
    presetMenuLabel,
    presetMenuDisabled,
    showPresetSpinner,
    presetVersionMenuLabel,
    versionMenuDisabled,
    showVersionSpinner,
  }
}
