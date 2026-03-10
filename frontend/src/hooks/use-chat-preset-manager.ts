import { useEffect, useState } from "react"
import type { AgentSessionsGetSessionVercelResponse } from "@/client"
import { toast } from "@/components/ui/use-toast"
import {
  useAgentPreset,
  useAgentPresets,
  useAgentPresetVersions,
} from "@/hooks/use-agent-presets"
import { parseChatError, type useUpdateChat } from "@/hooks/use-chat"
import { useAgentDefaultModel, useAgentModels } from "@/lib/hooks"

type AgentSessionWithModelSelection = AgentSessionsGetSessionVercelResponse & {
  model_catalog_ref?: string | null
}

type DraftSelection = {
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

function getSessionModelCatalogRef(
  chat: AgentSessionsGetSessionVercelResponse | undefined
): string | null {
  return (
    (chat as AgentSessionWithModelSelection | undefined)?.model_catalog_ref ??
    null
  )
}

function getDraftSelectionValue(
  selection: DraftSelection | null,
  ownerId: string | null,
  fallback: string | null
): string | null {
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
  const [draftModelCatalogRef, setDraftModelCatalogRef] =
    useState<DraftSelection | null>(null)

  const { presets, presetsIsLoading, presetsError } = useAgentPresets(
    workspaceId,
    { enabled }
  )
  const { defaultModel, defaultModelLoading } = useAgentDefaultModel()
  const { models, modelsLoading, modelsError } = useAgentModels(workspaceId)

  const presetOptions = enabled ? (presets ?? []) : []
  const selectionOwnerId = selectedChatId ?? null
  const effectivePresetId = getDraftSelectionValue(
    draftPresetId,
    selectionOwnerId,
    selectedChatId ? (chat?.agent_preset_id ?? null) : null
  )
  const effectivePresetVersionId = getDraftSelectionValue(
    draftPresetVersionId,
    selectionOwnerId,
    selectedChatId ? (chat?.agent_preset_version_id ?? null) : null
  )
  const effectiveModelCatalogRef = getDraftSelectionValue(
    draftModelCatalogRef,
    selectionOwnerId,
    selectedChatId ? getSessionModelCatalogRef(chat) : null
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
    setDraftModelCatalogRef({
      ownerId: selectedChatId,
      value: getSessionModelCatalogRef(chat),
    })
  }, [
    chat?.agent_preset_id,
    chat?.agent_preset_version_id,
    chat,
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
    models?.find((model) => model.catalog_ref === effectiveModelCatalogRef) ??
    null
  const effectiveChatModel =
    selectedModel ?? (effectiveModelCatalogRef ? null : (defaultModel ?? null))

  const handlePresetChange = async (nextPresetId: string | null) => {
    if (nextPresetId === effectivePresetId) {
      return
    }

    if (!selectedChatId) {
      setDraftPresetId({ ownerId: null, value: nextPresetId })
      setDraftPresetVersionId({ ownerId: null, value: null })
      if (nextPresetId !== null) {
        setDraftModelCatalogRef({ ownerId: null, value: null })
      }
      return
    }

    const previousPresetId = effectivePresetId
    const previousPresetVersionId = effectivePresetVersionId
    const previousModelCatalogRef = effectiveModelCatalogRef

    setDraftPresetId({ ownerId: selectedChatId, value: nextPresetId })
    setDraftPresetVersionId({ ownerId: selectedChatId, value: null })
    if (nextPresetId !== null) {
      setDraftModelCatalogRef({ ownerId: selectedChatId, value: null })
    }

    try {
      await updateChat({
        chatId: selectedChatId,
        update: {
          agent_preset_id: nextPresetId,
          agent_preset_version_id: null,
          ...(nextPresetId !== null ? { model_catalog_ref: null } : {}),
        },
      })
    } catch (error) {
      setDraftPresetId({ ownerId: selectedChatId, value: previousPresetId })
      setDraftPresetVersionId({
        ownerId: selectedChatId,
        value: previousPresetVersionId,
      })
      setDraftModelCatalogRef({
        ownerId: selectedChatId,
        value: previousModelCatalogRef,
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

  const handleModelCatalogChange = async (nextCatalogRef: string | null) => {
    if (nextCatalogRef === effectiveModelCatalogRef) {
      return
    }

    if (!selectedChatId) {
      setDraftPresetId({ ownerId: null, value: null })
      setDraftPresetVersionId({ ownerId: null, value: null })
      setDraftModelCatalogRef({ ownerId: null, value: nextCatalogRef })
      return
    }

    const previousPresetId = effectivePresetId
    const previousPresetVersionId = effectivePresetVersionId
    const previousCatalogRef = effectiveModelCatalogRef

    setDraftPresetId({ ownerId: selectedChatId, value: null })
    setDraftPresetVersionId({ ownerId: selectedChatId, value: null })
    setDraftModelCatalogRef({ ownerId: selectedChatId, value: nextCatalogRef })

    try {
      await updateChat({
        chatId: selectedChatId,
        update: {
          agent_preset_id: null,
          agent_preset_version_id: null,
          model_catalog_ref: nextCatalogRef,
        },
      })
    } catch (error) {
      setDraftPresetId({ ownerId: selectedChatId, value: previousPresetId })
      setDraftPresetVersionId({
        ownerId: selectedChatId,
        value: previousPresetVersionId,
      })
      setDraftModelCatalogRef({
        ownerId: selectedChatId,
        value: previousCatalogRef,
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
    ? selectedModel.display_name
    : effectiveChatModel?.display_name
      ? `${effectiveChatModel.display_name} (default)`
      : "Default model"
  const defaultModelLabel = defaultModel?.display_name ?? "Default model"
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
    selectedModelCatalogRef: effectiveModelCatalogRef,
    effectiveChatModel,
    handleModelCatalogChange,
    defaultModelLabel,
    defaultModelProvider,
    modelSelectorLabel,
    modelSelectorDisabled,
    showModelSelectorSpinner,
  }
}
