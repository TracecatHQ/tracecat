"use client"

import {
  type ApiError,
  type EmbeddingConfigurationRead,
  searchGetEmbeddingConfiguration,
  type TableSearchConfiguration,
  tablesGetTableSearch,
  tablesRetryTableSearch,
  tablesSelectTableSearchColumn,
} from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { toast } from "@/components/ui/use-toast"
import { useMutation, useQuery, useQueryClient } from "@/lib/query"

/** Shared cache prefix for selection, status and bounded progress. */
export function tableSearchKey(workspaceId: string, tableId: string) {
  return ["table-search", workspaceId, tableId] as const
}

/** Poll only active work; enumeration alone does not mean the index is ready. */
export function searchPollInterval(
  data?: TableSearchConfiguration,
  availability?: EmbeddingConfigurationRead
) {
  if (
    data?.selected_column_ids?.length &&
    availability?.available &&
    availability.reindex_required
  )
    return 3000
  return data?.status === "indexing" || data?.status === "updating"
    ? 3000
    : false
}

/** Own table-wide selection mutations and confirmed server status. */
export function useTableSearch(workspaceId: string, tableId: string) {
  const canReadWorkspace = useScopeCheck("workspace:read") === true
  const canReadTable = useScopeCheck("table:read") === true
  const canUpdate = useScopeCheck("table:update") === true
  const canRead = canReadWorkspace && canReadTable
  const client = useQueryClient()
  const key = tableSearchKey(workspaceId, tableId)
  const configuration = useQuery({
    queryKey: [...key, "configuration"],
    queryFn: () => tablesGetTableSearch({ workspaceId, tableId }),
    enabled: canRead,
    retry: false,
    meta: { suppressErrorToast: true },
    refetchInterval: (query) =>
      query.state.error
        ? false
        : searchPollInterval(
            query.state.data,
            client.getQueryData<EmbeddingConfigurationRead>([
              "embedding-configuration",
              workspaceId,
            ])
          ),
  })
  const provider = useQuery({
    queryKey: ["embedding-configuration", workspaceId],
    queryFn: () => searchGetEmbeddingConfiguration({ workspaceId }),
    enabled: canRead,
    retry: false,
    meta: { suppressErrorToast: true },
    refetchInterval: (query) =>
      query.state.error || configuration.error
        ? false
        : searchPollInterval(configuration.data, query.state.data),
  })
  async function refresh() {
    await Promise.all([
      client.invalidateQueries({ queryKey: key }),
      client.invalidateQueries({
        queryKey: ["embedding-configuration", workspaceId],
      }),
    ])
  }
  function mutationError(error: ApiError) {
    toast({
      title:
        error.status === 409
          ? "Search settings changed"
          : "Could not update semantic search",
      description:
        error.status === 409
          ? "The latest settings have been refreshed. Try again."
          : "Refresh to confirm the current state, then try again.",
      variant: "destructive",
    })
    void refresh()
  }
  const selection = useMutation({
    mutationFn: ({
      columnId,
      enabled,
    }: {
      columnId: string
      enabled: boolean
    }) =>
      tablesSelectTableSearchColumn({
        workspaceId,
        tableId,
        requestBody: {
          column_id: columnId,
          enabled,
          expected_generation: configuration.data?.generation ?? 0,
        },
      }),
    onSuccess: async (data) => {
      client.setQueryData([...key, "configuration"], data)
      await refresh()
    },
    onError: mutationError,
  })
  const retry = useMutation({
    mutationFn: (documentIds: string[]) =>
      tablesRetryTableSearch({
        workspaceId,
        tableId,
        requestBody: {
          expected_generation: configuration.data?.generation ?? 0,
          document_ids: documentIds,
        },
      }),
    onSuccess: refresh,
    onError: mutationError,
  })
  return {
    workspaceId,
    tableId,
    canRead,
    canUpdate,
    configuration,
    provider,
    selection,
    retry,
    refresh,
  }
}
