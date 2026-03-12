import { useEffect, useState } from "react"
import type {
  AgentSessionsGetSessionVercelResponse,
  ModelSelection,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import {
  useAgentPreset,
  useAgentPresets,
  useAgentPresetVersions,
} from "@/hooks/use-agent-presets"
import { parseChatError, type useUpdateChat } from "@/hooks/use-chat"
import {
  getModelSelectionKey,
  isSameModelSelection,
  matchesModelSelection,
  useAgentDefaultModel,
  useAgentModels,
} from "@/lib/hooks"

type DraftSelection = {
  ownerId: string | null
  value: string | null
}

type DraftModelSelection = {
  ownerId: string | null
  value: ModelSelection | null
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

function getSessionModelSelection(
  chat: AgentSessionsGetSessionVercelResponse | undefined
): ModelSelection | null {
  const selectionChat = chat as
    | {
        source_id?: string | null
        model_name?: string | null
        model_provider?: string | null
      }
    | undefined
  if (!selectionChat?.model_name || !selectionChat.model_provider) {
    return null
  }
  return {
    source_id: selectionChat.source_id ?? null,
    model_name: selectionChat.model_name,
    model_provider: selectionChat.model_provider,
  }
}

function getDraftStringValue(
  selection: DraftSelection | null,
  ownerId: string | null,
  fallback: string | null
): string | null {
  if (selection?.ownerId === ownerId) {
    return selection.value
  }
  return fallback
}

function getDraftModelSelectionValue(
  selection: DraftModelSelection | null,
  ownerId: string | null,
  fallback: ModelSelection | null
): ModelSelection | null {
  if (selection?.ownerId === ownerId) {
    return selection.value
  }
  return fallback
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
  const [draftPresetId, setDraftPresetId] = useState<DraftSelection | null>(
    null
  )
  const [draftPresetVersionId, setDraftPresetVersionId] =
    useState<DraftSelection | null>(null)
  const [draftModelSelection, setDraftModelSelection] =
    useState<DraftModelSelection | null>(null)

  const { presets, presetsIsLoading, presetsError } = useAgentPresets(
    workspaceId,
    { enabled }
  )
  const { defaultModel, defaultModelLoading } = useAgentDefaultModel()
  const { models, modelsLoading, modelsError } = useAgentModels(workspaceId)

  const presetOptions = enabled ? (presets ?? []) : []
  const selectionOwnerId = selectedChatId ?? null
  const effectivePresetId = getDraftStringValue(
    draftPresetId,
    selectionOwnerId,
    selectedChatId ? (chat?.agent_preset_id ?? null) : null
  )
  const effectivePresetVersionId = getDraftStringValue(
    draftPresetVersionId,
    selectionOwnerId,
    selectedChatId ? (chat?.agent_preset_version_id ?? null) : null
  )
  const selectionChat = chat as
    | {
        source_id?: string | null
        model_name?: string | null
        model_provider?: string | null
      }
    | undefined
  const sessionModelSourceId = selectedChatId
    ? (selectionChat?.source_id ?? null)
    : null
  const sessionModelName = selectedChatId
    ? (selectionChat?.model_name ?? null)
    : null
  const sessionModelProvider = selectedChatId
    ? (selectionChat?.model_provider ?? null)
    : null
  const effectiveSessionModelSelection =
    sessionModelName && sessionModelProvider
      ? {
          source_id: sessionModelSourceId,
          model_name: sessionModelName,
          model_provider: sessionModelProvider,
        }
      : null
  const effectiveModelSelection = getDraftModelSelectionValue(
    draftModelSelection,
    selectionOwnerId,
    effectiveSessionModelSelection
  )

  useEffect(() => {
    if (!selectedChatId) {
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
    setDraftModelSelection({
      ownerId: selectedChatId,
      value: getSessionModelSelection(chat),
    })
  }, [
    chat?.agent_preset_id,
    chat?.agent_preset_version_id,
    sessionModelName,
    sessionModelProvider,
    sessionModelSourceId,
    selectedChatId,
  ])

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
  const selectedPresetConfig = selectedPresetVersion ?? selectedPreset

  const selectedModel =
    models?.find((model) =>
      matchesModelSelection(model, effectiveModelSelection)
    ) ?? null
  const effectiveChatModel =
    selectedModel ?? (effectiveModelSelection ? null : (defaultModel ?? null))

  const handlePresetChange = async (nextPresetId: string | null) => {
    if (nextPresetId === effectivePresetId) {
      return
    }

    if (!selectedChatId) {
      setDraftPresetId({ ownerId: null, value: nextPresetId })
      setDraftPresetVersionId({ ownerId: null, value: null })
      if (nextPresetId !== null) {
        setDraftModelSelection({ ownerId: null, value: null })
      }
      return
    }

    const previousPresetId = effectivePresetId
    const previousPresetVersionId = effectivePresetVersionId
    const previousModelSelection = effectiveModelSelection

    setDraftPresetId({ ownerId: selectedChatId, value: nextPresetId })
    setDraftPresetVersionId({ ownerId: selectedChatId, value: null })
    if (nextPresetId !== null) {
      setDraftModelSelection({ ownerId: selectedChatId, value: null })
    }

    try {
      await updateChat({
        chatId: selectedChatId,
        update: {
          agent_preset_id: nextPresetId,
          agent_preset_version_id: null,
          ...(nextPresetId !== null
            ? {
                source_id: null,
                model_name: null,
                model_provider: null,
              }
            : {}),
        },
      })
    } catch (error) {
      setDraftPresetId({ ownerId: selectedChatId, value: previousPresetId })
      setDraftPresetVersionId({
        ownerId: selectedChatId,
        value: previousPresetVersionId,
      })
      setDraftModelSelection({
        ownerId: selectedChatId,
        value: previousModelSelection,
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

  const handleModelSelectionChange = async (
    nextSelection: ModelSelection | null
  ) => {
    if (isSameModelSelection(nextSelection, effectiveModelSelection)) {
      return
    }

    if (!selectedChatId) {
      setDraftPresetId({ ownerId: null, value: null })
      setDraftPresetVersionId({ ownerId: null, value: null })
      setDraftModelSelection({ ownerId: null, value: nextSelection })
      return
    }

    const previousPresetId = effectivePresetId
    const previousPresetVersionId = effectivePresetVersionId
    const previousModelSelection = effectiveModelSelection

    setDraftPresetId({ ownerId: selectedChatId, value: null })
    setDraftPresetVersionId({ ownerId: selectedChatId, value: null })
    setDraftModelSelection({
      ownerId: selectedChatId,
      value: nextSelection,
    })

    try {
      await updateChat({
        chatId: selectedChatId,
        update: {
          agent_preset_id: null,
          agent_preset_version_id: null,
          source_id: nextSelection?.source_id ?? null,
          model_name: nextSelection?.model_name ?? null,
          model_provider: nextSelection?.model_provider ?? null,
        },
      })
    } catch (error) {
      setDraftPresetId({ ownerId: selectedChatId, value: previousPresetId })
      setDraftPresetVersionId({
        ownerId: selectedChatId,
        value: previousPresetVersionId,
      })
      setDraftModelSelection({
        ownerId: selectedChatId,
        value: previousModelSelection,
      })
      console.error("Failed to update chat model selection:", error)
      toast({
        title: "Failed to update model",
        description: parseChatError(error),
        variant: "destructive",
      })
    }
  }

  const presetMenuLabel = selectedPreset?.name ?? "No preset"
  const presetMenuDisabled = !enabled || chatLoading || isUpdatingChat
  const showPresetSpinner =
    presetsIsLoading || isUpdatingChat || chatLoading || selectedPresetLoading
  const versionMenuDisabled =
    !enabled ||
    !effectivePresetId ||
    chatLoading ||
    isUpdatingChat ||
    versionsIsLoading
  const modelSelectorLabel = selectedModel
    ? selectedModel.model_name
    : effectiveChatModel?.model_name
      ? `${effectiveChatModel.model_name} (default)`
      : "Default model"
  const defaultModelLabel = defaultModel?.model_name ?? "Default model"
  const defaultModelProvider = defaultModel?.model_provider ?? null
  const modelSelectorDisabled = !enabled || chatLoading || isUpdatingChat
  const showModelSelectorSpinner =
    modelsLoading || defaultModelLoading || isUpdatingChat || chatLoading

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
    versionMenuDisabled,
    enabledModels: models ?? [],
    enabledModelsError: modelsError,
    enabledModelsLoading: modelsLoading,
    selectedModel,
    selectedModelSelection: effectiveModelSelection,
    selectedModelSelectionKey: effectiveModelSelection
      ? getModelSelectionKey(effectiveModelSelection)
      : null,
    effectiveChatModel,
    handleModelSelectionChange,
    defaultModelLabel,
    defaultModelProvider,
    modelSelectorLabel,
    modelSelectorDisabled,
    showModelSelectorSpinner,
  }
}
