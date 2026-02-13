import { useEffect, useState } from "react"
import type { AgentSessionsGetSessionVercelResponse } from "@/client"
import { toast } from "@/components/ui/use-toast"
import { useAgentPreset, useAgentPresets } from "@/hooks/use-agent-presets"
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

  const { presets, presetsIsLoading, presetsError } = useAgentPresets(
    workspaceId,
    { enabled }
  )

  const presetOptions = enabled ? (presets ?? []) : []
  const effectivePresetId = selectedChatId
    ? (chat?.agent_preset_id ?? null)
    : draftPresetId

  useEffect(() => {
    if (!selectedChatId) {
      return
    }
    setDraftPresetId(chat?.agent_preset_id ?? null)
  }, [chat?.agent_preset_id, selectedChatId])

  const { preset: selectedPreset, presetIsLoading: selectedPresetLoading } =
    useAgentPreset(workspaceId, effectivePresetId, {
      enabled: enabled && Boolean(effectivePresetId),
    })

  const handlePresetChange = async (nextPresetId: string | null) => {
    const currentPresetId = effectivePresetId
    if (nextPresetId === currentPresetId) {
      return
    }

    if (!selectedChatId) {
      setDraftPresetId(nextPresetId)
      return
    }

    try {
      await updateChat({
        chatId: selectedChatId,
        update: {
          agent_preset_id: nextPresetId,
        },
      })
      setDraftPresetId(nextPresetId)
    } catch (error) {
      console.error("Failed to update chat preset:", error)
      toast({
        title: "Failed to update preset",
        description: parseChatError(error),
        variant: "destructive",
      })
    }
  }

  const presetMenuLabel = selectedPreset?.name ?? "No preset"
  const presetMenuDisabled = !enabled || chatLoading || isUpdatingChat
  const showPresetSpinner =
    presetsIsLoading || isUpdatingChat || chatLoading || selectedPresetLoading

  return {
    presets: presetOptions,
    presetsIsLoading,
    presetsError,
    selectedPreset,
    selectedPresetId: effectivePresetId,
    selectedPresetLoading,
    handlePresetChange,
    presetMenuLabel,
    presetMenuDisabled,
    showPresetSpinner,
  }
}
