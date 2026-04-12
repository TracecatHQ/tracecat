import { useEffect, useState } from "react"
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

  const handlePresetChange = async (nextPresetId: string | null) => {
    if (nextPresetId === effectivePresetId) {
      return
    }

    if (!selectedChatId) {
      setDraftPresetId({ ownerId: null, value: nextPresetId })
      setDraftPresetVersionId({ ownerId: null, value: null })
      return
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
    }
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
  const selectedPresetConfig = selectedPresetVersion ?? selectedPreset

  return {
    presets: presetOptions,
    presetsIsLoading,
    presetsError,
    selectedPreset,
    selectedPresetConfig,
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
    presetMenuLabel,
    presetMenuDisabled,
    showPresetSpinner,
    presetVersionMenuLabel,
    versionMenuDisabled,
    showVersionSpinner,
  }
}
