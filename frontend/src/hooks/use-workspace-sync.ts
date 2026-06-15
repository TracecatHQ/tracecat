import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  type ApiError,
  type GitBranchInfo,
  type GitCommitInfo,
  type PullResult,
  type VcsProvider,
  type WorkflowSyncPullRequest,
  type WorkspaceSyncExportRequest,
  type WorkspaceSyncExportResult,
  workflowsExportWorkspaceSync,
  workflowsListWorkflowBranches,
  workflowsListWorkflowCommits,
  workflowsPullWorkflows,
} from "@/client"

interface WorkflowPullOptions {
  commit_sha: string
  dry_run?: boolean
  sync_schedules?: boolean
  provider?: VcsProvider
}

/**
 * Hook for pulling workspace config from Git repositories.
 */
export function useWorkflowSync(workspaceId: string) {
  const queryClient = useQueryClient()

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
        provider: options.provider ?? "github",
      }

      const response = await workflowsPullWorkflows({
        workspaceId,
        requestBody,
      })

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
        queryClient.invalidateQueries({ queryKey: ["workflows", workspaceId] })
        queryClient.invalidateQueries({
          queryKey: ["workflow_definitions", workspaceId],
        })
        queryClient.invalidateQueries({ queryKey: ["workspace", workspaceId] })
        queryClient.invalidateQueries({
          queryKey: ["agent-presets", workspaceId],
        })
        queryClient.invalidateQueries({
          queryKey: ["agent-directory-items", workspaceId],
        })
        queryClient.invalidateQueries({ queryKey: ["agent-tags", workspaceId] })
        queryClient.invalidateQueries({ queryKey: ["skills", workspaceId] })
        queryClient.invalidateQueries({ queryKey: ["tables", workspaceId] })
        queryClient.invalidateQueries({
          queryKey: ["case-tag-catalog", workspaceId],
        })
        queryClient.invalidateQueries({
          queryKey: ["case-duration-definitions", workspaceId],
        })
        queryClient.invalidateQueries({
          queryKey: ["case-fields", workspaceId],
        })
        queryClient.invalidateQueries({
          queryKey: ["case-dropdown-definitions", workspaceId],
        })
        queryClient.invalidateQueries({
          queryKey: ["workspace-variables", workspaceId],
        })
        queryClient.invalidateQueries({
          queryKey: ["workspace-secrets", workspaceId],
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
 * Hook for exporting workspace config specs to Git.
 */
export function useWorkspaceSyncExport(workspaceId: string) {
  const {
    mutateAsync: exportWorkspace,
    isPending: exportWorkspaceIsPending,
    error: exportWorkspaceError,
  } = useMutation({
    mutationFn: async (
      requestBody: WorkspaceSyncExportRequest
    ): Promise<WorkspaceSyncExportResult> => {
      return await workflowsExportWorkspaceSync({
        workspaceId,
        requestBody,
      })
    },
  })

  return {
    exportWorkspace,
    exportWorkspaceIsPending,
    exportWorkspaceError,
  }
}

/**
 * Hook for fetching Git repository branches.
 */
export function useRepositoryBranches(
  workspaceId: string,
  options?: {
    limit?: number
    enabled?: boolean
  }
) {
  const {
    data: branches,
    isLoading: branchesIsLoading,
    error: branchesError,
  } = useQuery<GitBranchInfo[], ApiError>({
    queryKey: ["workflow-sync-branches", workspaceId, options?.limit ?? 200],
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
