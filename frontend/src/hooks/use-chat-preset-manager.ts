import { useEffect, useState } from "react"
import type { AgentSessionsGetSessionVercelResponse } from "@/client"
import { toast } from "@/components/ui/use-toast"
import {
  useAgentPreset,
  useAgentPresets,
  useAgentPresetVersions,
} from "@/hooks/use-agent-presets"
import { parseChatError, type useUpdateChat } from "@/hooks/use-chat"

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
  const [draftPresetId, setDraftPresetId] = useState<string | null>(null)
  const [draftPresetVersionId, setDraftPresetVersionId] = useState<
    string | null
  >(null)

  const { presets, presetsIsLoading, presetsError } = useAgentPresets(
    workspaceId,
    { enabled }
  )

  const presetOptions = enabled ? (presets ?? []) : []
  const effectivePresetId = selectedChatId
    ? (chat?.agent_preset_id ?? null)
    : draftPresetId
  const effectivePresetVersionId = selectedChatId
    ? (chat?.agent_preset_version_id ?? null)
    : draftPresetVersionId

  useEffect(() => {
    if (!selectedChatId) {
      return
    }
    setDraftPresetId(chat?.agent_preset_id ?? null)
    setDraftPresetVersionId(chat?.agent_preset_version_id ?? null)
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
  const selectedPresetVersion =
    versions?.find((version) => version.id === effectivePresetVersionId) ?? null

  const handlePresetChange = async (nextPresetId: string | null) => {
    if (nextPresetId === effectivePresetId) {
      return
    }

    if (!selectedChatId) {
      setDraftPresetId(nextPresetId)
      setDraftPresetVersionId(null)
      return
    }

    try {
      await updateChat({
        chatId: selectedChatId,
        update: {
          agent_preset_id: nextPresetId,
          agent_preset_version_id: null,
        },
      })
      setDraftPresetId(nextPresetId)
      setDraftPresetVersionId(null)
    } catch (error) {
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
      setDraftPresetVersionId(nextVersionId)
      return
    }

    try {
      await updateChat({
        chatId: selectedChatId,
        update: {
          agent_preset_id: effectivePresetId,
          agent_preset_version_id: nextVersionId,
        },
      })
      setDraftPresetVersionId(nextVersionId)
    } catch (error) {
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
  const presetVersionMenuLabel = selectedPresetVersion
    ? selectedPresetVersion.id === currentPresetVersion?.id
      ? `Current (v${selectedPresetVersion.version})`
      : `Pinned v${selectedPresetVersion.version}`
    : currentPresetVersion
      ? `Current (v${currentPresetVersion.version})`
      : "Current"
  const versionMenuDisabled =
    !enabled ||
    !effectivePresetId ||
    chatLoading ||
    isUpdatingChat ||
    versionsIsLoading
  const showVersionSpinner = versionsIsLoading || isUpdatingChat || chatLoading
  const selectedPresetConfig = selectedPresetVersion ?? selectedPreset

  return {
    presets: presetOptions,
    presetsIsLoading,
    presetsError,
    selectedPreset,
    selectedPresetConfig,
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
