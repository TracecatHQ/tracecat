import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  type ChangeSetCreate,
  type ChangeSetExport,
  type ChangeSetRead,
  type GitCommitInfo,
  type PullResult,
  type WorkflowSyncPullRequest,
  type WorkspaceSyncExportResult,
  type WorkspaceSyncPendingChanges,
  type WorkspaceSyncStatus,
  workflowsCreateWorkspaceSyncChangeset,
  workflowsExportWorkspaceSyncChangeset,
  workflowsGetWorkspaceSyncStatus,
  workflowsListWorkflowCommits,
  workflowsListWorkspaceSyncChangesets,
  workflowsListWorkspaceSyncPendingChanges,
  workflowsPullWorkflows,
} from "@/client"

interface WorkflowPullOptions {
  commit_sha: string
  dry_run?: boolean
}

/**
 * Hook for pulling workflows from Git repositories
 */
export function useWorkflowSync(workspaceId: string) {
  const queryClient = useQueryClient()

  // Mutation for pulling workflows
  const {
    mutateAsync: pullWorkflows,
    isPending: pullWorkflowsIsPending,
    error: pullWorkflowsError,
  } = useMutation({
    mutationFn: async (options: WorkflowPullOptions): Promise<PullResult> => {
      const requestBody: WorkflowSyncPullRequest = {
        commit_sha: options.commit_sha,
        dry_run: options.dry_run ?? false,
      }

      const response = await workflowsPullWorkflows({
        workspaceId,
        requestBody,
      })

      return response
    },
    onSuccess: (result) => {
      if (result.success && result.workflows_imported > 0) {
        // Invalidate workflow-related queries to refresh the UI
        queryClient.invalidateQueries({ queryKey: ["workflows", workspaceId] })
        queryClient.invalidateQueries({
          queryKey: ["workflow_definitions", workspaceId],
        })
      }
    },
  })

  return {
    pullWorkflows,
    pullWorkflowsIsPending,
    pullWorkflowsError,
  }
}

/**
 * Hook for fetching Git repository commits
 */
export function useRepositoryCommits(
  workspaceId: string,
  options?: {
    branch?: string
    limit?: number
    enabled?: boolean
  }
) {
  const {
    data: commits,
    isLoading: commitsIsLoading,
    error: commitsError,
  } = useQuery<GitCommitInfo[]>({
    queryKey: [
      "repository_commits",
      workspaceId,
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

/**
 * Fetches the workspace-level Git sync status for the current repository.
 */
export function useWorkspaceSyncStatus(
  workspaceId: string | undefined,
  options?: { enabled?: boolean }
) {
  return useQuery<WorkspaceSyncStatus>({
    queryKey: ["workspace-sync-status", workspaceId],
    queryFn: async () => {
      if (!workspaceId) {
        throw new Error("Workspace ID is required")
      }
      return await workflowsGetWorkspaceSyncStatus({ workspaceId })
    },
    enabled: !!workspaceId && options?.enabled !== false,
  })
}

/**
 * Fetches local workspace changes that can be exported to Git.
 */
export function useWorkspaceSyncPendingChanges(
  workspaceId: string | undefined,
  options?: { enabled?: boolean }
) {
  return useQuery<WorkspaceSyncPendingChanges>({
    queryKey: ["workspace-sync-pending", workspaceId],
    queryFn: async () => {
      if (!workspaceId) {
        throw new Error("Workspace ID is required")
      }
      return await workflowsListWorkspaceSyncPendingChanges({ workspaceId })
    },
    enabled: !!workspaceId && options?.enabled !== false,
  })
}

/**
 * Fetches recent workspace sync changesets for review and re-selection.
 */
export function useWorkspaceSyncChangesets(
  workspaceId: string | undefined,
  options?: { enabled?: boolean; limit?: number }
) {
  return useQuery<ChangeSetRead[]>({
    queryKey: ["workspace-sync-changesets", workspaceId, options?.limit ?? 20],
    queryFn: async () => {
      if (!workspaceId) {
        throw new Error("Workspace ID is required")
      }
      return await workflowsListWorkspaceSyncChangesets({
        workspaceId,
        limit: options?.limit ?? 20,
      })
    },
    enabled: !!workspaceId && options?.enabled !== false,
  })
}

/**
 * Provides mutations for creating and exporting workspace sync changesets.
 */
export function useWorkspaceSyncChangesetActions(
  workspaceId: string | undefined
) {
  const queryClient = useQueryClient()
  const invalidateSyncQueries = () => {
    queryClient.invalidateQueries({
      queryKey: ["workspace-sync-status", workspaceId],
    })
    queryClient.invalidateQueries({
      queryKey: ["workspace-sync-pending", workspaceId],
    })
    queryClient.invalidateQueries({
      queryKey: ["workspace-sync-changesets", workspaceId],
    })
  }

  const createChangeset = useMutation<ChangeSetRead, Error, ChangeSetCreate>({
    mutationFn: async (requestBody) => {
      if (!workspaceId) {
        throw new Error("Workspace ID is required")
      }
      return await workflowsCreateWorkspaceSyncChangeset({
        workspaceId,
        requestBody,
      })
    },
    onSuccess: invalidateSyncQueries,
  })

  const exportChangeset = useMutation<
    WorkspaceSyncExportResult,
    Error,
    { changesetId: string; requestBody: ChangeSetExport }
  >({
    mutationFn: async ({ changesetId, requestBody }) => {
      if (!workspaceId) {
        throw new Error("Workspace ID is required")
      }
      return await workflowsExportWorkspaceSyncChangeset({
        workspaceId,
        changesetId,
        requestBody,
      })
    },
    onSuccess: invalidateSyncQueries,
  })

  return {
    createChangeset,
    exportChangeset,
  }
}
