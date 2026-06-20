import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  type ApiError,
  type GitBranchInfo,
  type GitCommitInfo,
  type PullResult,
  type ResourceRef,
  type VcsProvider,
  type WorkflowSyncPullRequest,
  type WorkspaceSyncExportPreview,
  type WorkspaceSyncExportRequest,
  type WorkspaceSyncExportResult,
  workflowsExportWorkspaceSync,
  workflowsListWorkflowBranches,
  workflowsListWorkflowCommits,
  workflowsPreviewExportWorkspaceSync,
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

interface ExportPreviewOptions {
  resources?: ResourceRef[] | null
  includeSchedules?: boolean
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
  { resources, includeSchedules = false, enabled = true }: ExportPreviewOptions
) {
  const isFullWorkspacePreview = resources === undefined || resources === null
  const normalizedResources = resources ?? []
  const resourceKey = isFullWorkspacePreview
    ? ["__all__"]
    : normalizedResources
        .map(
          ({ resource_type, source_id, local_id }) =>
            `${resource_type}:${source_id ?? ""}:${local_id ?? ""}`
        )
        .sort()
  const {
    data: preview,
    isLoading: previewIsLoading,
    error: previewError,
  } = useQuery<WorkspaceSyncExportPreview, ApiError>({
    queryKey: [
      "workspace-sync-export-preview",
      workspaceId,
      resourceKey,
      includeSchedules,
    ],
    queryFn: async (): Promise<WorkspaceSyncExportPreview> => {
      return await workflowsPreviewExportWorkspaceSync({
        workspaceId,
        requestBody: {
          resources: isFullWorkspacePreview ? undefined : normalizedResources,
          include_schedules: includeSchedules,
        },
      })
    },
    enabled:
      Boolean(workspaceId) &&
      enabled &&
      (isFullWorkspacePreview || normalizedResources.length > 0),
    staleTime: 30 * 1000,
  })

  return {
    preview,
    previewIsLoading,
    previewError,
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
