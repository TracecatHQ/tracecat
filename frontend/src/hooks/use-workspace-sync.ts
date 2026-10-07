import { useRef, useState } from "react"
import {
  type ApiError,
  type CatalogMappingSelection,
  type GitBranchInfo,
  type GitCommitInfo,
  type McpIntegrationMappingSelection,
  type PullResult,
  type ResourceRef,
  type SecretStoreMappingSelection,
  type SyncOperationRead,
  type VcsProvider,
  type WorkflowSyncPullRequest,
  type WorkspaceSyncExportPreview,
  type WorkspaceSyncExportRequest_Input as WorkspaceSyncExportRequest,
  type WorkspaceSyncExportResult,
  workflowsApplySyncOperation,
  workflowsCreateSyncOperation,
  workflowsListWorkflowBranches,
  workflowsListWorkflowCommits,
} from "@/client"
import {
  useSyncOperation,
  useSyncOperationWaiter,
} from "@/hooks/use-sync-operation"
import {
  type QueryClient,
  useMutation,
  useQuery,
  useQueryClient,
} from "@/lib/query"

interface WorkflowPullOptions {
  commit_sha: string
  dry_run?: boolean
  sync_schedules?: boolean
  catalog_mappings?: CatalogMappingSelection[]
  mcp_integration_mappings?: McpIntegrationMappingSelection[]
  secret_store_mappings?: SecretStoreMappingSelection[]
}

/**
 * Hook for pulling workspace config from Git repositories.
 */
export function useWorkflowSync(workspaceId: string) {
  const queryClient = useQueryClient()
  const wait = useSyncOperationWaiter(workspaceId)
  const [previewOperation, setPreviewOperation] = useState<SyncOperationRead>()
  const prepared = useRef<{ id: string; key: string } | null>(null)
  const pendingPreview = useRef<{ id: string; key: string } | null>(null)

  // Mutation for pulling workspace config
  const {
    mutateAsync: pullWorkflows,
    isPending: pullWorkflowsIsPending,
    error: pullWorkflowsError,
  } = useMutation({
    mutationFn: async (options: WorkflowPullOptions): Promise<PullResult> => {
      const requestBody: WorkflowSyncPullRequest = {
        commit_sha: options.commit_sha,
        dry_run: options.dry_run ?? false,
        sync_schedules: options.sync_schedules ?? false,
        ...(options.catalog_mappings?.length
          ? { catalog_mappings: options.catalog_mappings }
          : {}),
        ...(options.mcp_integration_mappings?.length
          ? { mcp_integration_mappings: options.mcp_integration_mappings }
          : {}),
        ...(options.secret_store_mappings?.length
          ? { secret_store_mappings: options.secret_store_mappings }
          : {}),
      }

      const key = JSON.stringify({ ...requestBody, dry_run: true })
      let operation: SyncOperationRead
      if (options.dry_run) {
        if (!pendingPreview.current || pendingPreview.current.key !== key) {
          pendingPreview.current = { key, id: crypto.randomUUID() }
        }
        const started = await workflowsCreateSyncOperation({
          workspaceId,
          requestBody: {
            id: pendingPreview.current.id,
            direction: "pull",
            pull: { ...requestBody, dry_run: true },
          },
        })
        queryClient.invalidateQueries({
          queryKey: ["sync-operations", workspaceId],
        })
        operation = await wait(started.id)
        pendingPreview.current = null
        prepared.current = { id: operation.id, key }
        setPreviewOperation(operation)
      } else {
        if (!prepared.current || prepared.current.key !== key)
          throw new Error("Preview these changes before applying")
        const started = await workflowsApplySyncOperation({
          workspaceId,
          operationId: prepared.current.id,
        })
        queryClient.invalidateQueries({
          queryKey: ["sync-operations", workspaceId],
        })
        operation = await wait(started.id)
      }
      const response = options.dry_run ? operation.preview : operation.result
      if (!response || !("workflows_found" in response))
        throw new Error(operation.error ?? "Sync did not complete")
      return response
    },
    onSuccess: (result) => {
      const importedCount = result.resource_counts
        ? Object.values(result.resource_counts).reduce(
            (total, count) => total + count.imported,
            0
          )
        : result.workflows_imported

      if (result.success && importedCount > 0) {
        invalidateWorkspaceSyncResources(queryClient, workspaceId)
      }
    },
  })

  return {
    previewOperation,
    pullWorkflows,
    pullWorkflowsIsPending,
    pullWorkflowsError,
  }
}

/**
 * Hook for exporting workspace config specs to Git.
 */
export function useWorkspaceSyncExport(workspaceId: string) {
  const wait = useSyncOperationWaiter(workspaceId)
  const queryClient = useQueryClient()
  const {
    mutateAsync: exportWorkspace,
    isPending: exportWorkspaceIsPending,
    error: exportWorkspaceError,
  } = useMutation({
    mutationFn: async ({
      operationId,
    }: {
      operationId: string
    }): Promise<WorkspaceSyncExportResult> => {
      const started = await workflowsApplySyncOperation({
        workspaceId,
        operationId,
      })
      queryClient.invalidateQueries({
        queryKey: ["sync-operations", workspaceId],
      })
      const operation = await wait(started.id)
      if (!operation.result || !("commit" in operation.result))
        throw new Error(operation.error ?? "Push did not complete")
      return operation.result
    },
  })
  return { exportWorkspace, exportWorkspaceIsPending, exportWorkspaceError }
}

interface ExportPreviewOptions {
  push?: WorkspaceSyncExportRequest
  resources?: ResourceRef[] | null
  includeSchedules?: boolean
  compareRef?: string
  provider?: VcsProvider
  enabled?: boolean
}

/**
 * Hook for previewing which resources an export would commit.
 *
 * Runs a read-only projection so the push dialog can show an accurate count of
 * the resources that will be committed before the user confirms.
 */
export function useWorkspaceSyncExportPreview(
  workspaceId: string,
  { push, compareRef, provider = "github" }: ExportPreviewOptions
) {
  const queryClient = useQueryClient()
  const key = JSON.stringify([workspaceId, push, compareRef, provider])
  const pendingRequest = useRef<{ key: string; id: string } | null>(null)
  const [accepted, setAccepted] = useState<{ key: string; id: string }>()
  const creation = useMutation({
    mutationFn: ({ id }: { id: string; key: string }) => {
      if (!push)
        throw new Error("Select a target branch and commit message first")
      return workflowsCreateSyncOperation({
        workspaceId,
        requestBody: { id, direction: "push", push, compare_ref: compareRef },
      })
    },
    onSuccess: (operation, request) => {
      pendingRequest.current = null
      setAccepted({ key: request.key, id: operation.id })
      queryClient.setQueryData(
        ["sync-operation", workspaceId, operation.id],
        operation
      )
      void queryClient.invalidateQueries({
        queryKey: ["sync-operations", workspaceId],
      })
    },
  })
  const operationId = accepted?.key === key ? accepted.id : undefined
  const poll = useSyncOperation(workspaceId, operationId)
  const operation = poll.data
  const preview = operation?.status === "ready" ? operation : undefined

  function refetchPreview() {
    if (operationId && poll.error) {
      void poll.refetch()
      return
    }
    // Preserve the request ID after an ambiguous POST failure. Poll failures
    // never enter this mutation or create another background job.
    if (!pendingRequest.current || pendingRequest.current.key !== key) {
      pendingRequest.current = { key, id: crypto.randomUUID() }
    }
    creation.mutate(pendingRequest.current)
  }

  return {
    preview: preview?.preview as WorkspaceSyncExportPreview | undefined,
    previewOperationId: preview?.id,
    previewDiffCount: preview?.diff_count ?? 0,
    previewIsLoading:
      creation.isPending ||
      Boolean(
        operationId &&
          !poll.error &&
          (!operation || ["queued", "running"].includes(operation.status))
      ),
    previewError:
      creation.error ??
      poll.error ??
      (operation?.status === "failed"
        ? new Error(operation.error ?? "Preview did not complete")
        : null),
    refetchPreview,
  }
}

/**
 * Hook for fetching Git repository branches.
 */
export function useRepositoryBranches(
  workspaceId: string,
  options?: {
    gitRepoUrl?: string
    provider?: VcsProvider
    limit?: number
    enabled?: boolean
  }
) {
  const provider = options?.provider ?? "github"
  const {
    data: branches,
    isLoading: branchesIsLoading,
    error: branchesError,
  } = useQuery<GitBranchInfo[], ApiError>({
    queryKey: [
      "workflow-sync-branches",
      workspaceId,
      options?.gitRepoUrl ?? "__workspace_repo__",
      provider,
      options?.limit ?? 200,
    ],
    queryFn: async (): Promise<GitBranchInfo[]> => {
      if (!workspaceId) {
        throw new Error("Workspace ID is required")
      }

      return await workflowsListWorkflowBranches({
        limit: options?.limit ?? 200,
        workspaceId,
      })
    },
    enabled: !!(workspaceId && options?.enabled !== false),
    staleTime: 5 * 60 * 1000,
  })

  return {
    branches,
    branchesIsLoading,
    branchesError,
  }
}

/**
 * Hook for fetching Git repository commits
 */
export function useRepositoryCommits(
  workspaceId: string,
  options?: {
    gitRepoUrl?: string
    provider?: VcsProvider
    branch?: string
    limit?: number
    enabled?: boolean
  }
) {
  const provider = options?.provider ?? "github"
  const {
    data: commits,
    isLoading: commitsIsLoading,
    error: commitsError,
  } = useQuery<GitCommitInfo[]>({
    queryKey: [
      "repository_commits",
      workspaceId,
      options?.gitRepoUrl ?? "__workspace_repo__",
      provider,
      options?.branch ?? "main",
      options?.limit ?? 10,
    ],
    queryFn: async (): Promise<GitCommitInfo[]> => {
      if (!workspaceId) {
        throw new Error("Workspace ID is required")
      }

      const response = await workflowsListWorkflowCommits({
        branch: options?.branch ?? "main",
        limit: options?.limit ?? 10,
        workspaceId,
      })

      return response
    },
    enabled: !!(workspaceId && options?.enabled !== false),
    staleTime: 5 * 60 * 1000, // 5 minutes
  })

  return {
    commits,
    commitsIsLoading,
    commitsError,
  }
}

/** Refresh resources when a pull finishes, including pulls resumed from history. */
export function invalidateWorkspaceSyncResources(
  queryClient: QueryClient,
  workspaceId: string
) {
  const keys = [
    "workflows",
    "workflow_definitions",
    "workspace",
    "agent-presets",
    "agent-directory-items",
    "agent-tags",
    "skills",
    "skill-library",
    "tables",
    "case-tag-catalog",
    "case-duration-definitions",
    "case-fields",
    "case-dropdown-definitions",
    "workspace-variables",
    "workspace-secrets",
  ]
  for (const key of keys) {
    queryClient.invalidateQueries({ queryKey: [key, workspaceId] })
  }
}
