import { agentCheckWorkspaceModelStatus, type ModelStatusRead } from "@/client"
import type { ModelInfo } from "@/lib/chat"
import { useMutation, useQuery, useQueryClient } from "@/lib/query"

/** Matches the server's cache, so a re-render never re-probes the provider. */
const MODEL_STATUS_STALE_MS = 2 * 60 * 1000

function modelStatusQueryKey(workspaceId: string, modelInfo: ModelInfo) {
  return [
    "workspace",
    workspaceId,
    "model-status",
    modelInfo.catalogId ?? null,
    modelInfo.provider,
    modelInfo.name,
    modelInfo.presetId ?? null,
    modelInfo.presetVersionId ?? null,
  ] as const
}

async function fetchModelStatus(
  workspaceId: string,
  modelInfo: ModelInfo,
  refresh: boolean
): Promise<ModelStatusRead> {
  return await agentCheckWorkspaceModelStatus({
    workspaceId,
    requestBody: {
      catalog_id: modelInfo.catalogId ?? null,
      model_provider: modelInfo.provider,
      model_name: modelInfo.name,
      preset_id: modelInfo.presetId ?? null,
      preset_version_id: modelInfo.presetVersionId ?? null,
      refresh,
    },
  })
}

/**
 * Connectivity of the chat's selected model: whether its credentials exist
 * and the provider accepts them. `recheck` skips the server's cache.
 */
export function useModelStatus(
  workspaceId: string | null | undefined,
  modelInfo: ModelInfo | null | undefined
) {
  const queryClient = useQueryClient()
  const query = useQuery<ModelStatusRead>({
    queryKey:
      workspaceId && modelInfo
        ? modelStatusQueryKey(workspaceId, modelInfo)
        : ["workspace", workspaceId, "model-status", null],
    queryFn: async () =>
      await fetchModelStatus(
        workspaceId as string,
        modelInfo as ModelInfo,
        false
      ),
    enabled: Boolean(workspaceId && modelInfo),
    // The dot itself shows a failed check.
    meta: { suppressErrorToast: true },
    staleTime: MODEL_STATUS_STALE_MS,
    retry: false,
  })
  // A recheck belongs to the model it ran for; its state never shows on
  // another model the pane switched to.
  const currentKey =
    workspaceId && modelInfo
      ? JSON.stringify(modelStatusQueryKey(workspaceId, modelInfo))
      : null
  const recheck = useMutation({
    // The dot itself shows a failed check.
    meta: { suppressErrorToast: true },
    mutationFn: async (target: {
      key: string
      workspaceId: string
      modelInfo: ModelInfo
    }) => {
      const fresh = await fetchModelStatus(
        target.workspaceId,
        target.modelInfo,
        true
      )
      queryClient.setQueryData(
        modelStatusQueryKey(target.workspaceId, target.modelInfo),
        fresh
      )
    },
  })
  const recheckIsCurrent =
    currentKey !== null && recheck.variables?.key === currentKey
  return {
    status: query.data,
    isChecking: query.isLoading || (recheckIsCurrent && recheck.isPending),
    error: query.error ?? (recheckIsCurrent ? recheck.error : null),
    recheck: () => {
      if (!workspaceId || !modelInfo || currentKey === null) return
      recheck.mutate({ key: currentKey, workspaceId, modelInfo })
    },
  }
}
