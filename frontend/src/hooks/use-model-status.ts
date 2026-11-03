import { agentGetWorkspaceModelStatus, type ModelStatusRead } from "@/client"
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
  ] as const
}

async function fetchModelStatus(
  workspaceId: string,
  modelInfo: ModelInfo,
  refresh: boolean
): Promise<ModelStatusRead> {
  return await agentGetWorkspaceModelStatus({
    workspaceId,
    catalogId: modelInfo.catalogId ?? undefined,
    modelProvider: modelInfo.provider,
    modelName: modelInfo.name,
    refresh,
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
    staleTime: MODEL_STATUS_STALE_MS,
    retry: false,
  })
  const recheck = useMutation({
    // The dot itself shows a failed check.
    meta: { suppressErrorToast: true },
    mutationFn: async () => {
      if (!workspaceId || !modelInfo) return
      const fresh = await fetchModelStatus(workspaceId, modelInfo, true)
      queryClient.setQueryData(
        modelStatusQueryKey(workspaceId, modelInfo),
        fresh
      )
    },
  })
  return {
    status: query.data,
    isChecking: query.isLoading || recheck.isPending,
    error: query.error ?? recheck.error,
    recheck: () => recheck.mutate(),
  }
}
