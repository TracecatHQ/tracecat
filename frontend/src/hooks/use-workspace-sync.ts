import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  type ConflictStrategy,
  type GitCommitInfo,
  type PullResult,
  type WorkflowSyncPullRequest,
  workflowsListWorkflowCommits,
  workflowsPullWorkflows,
} from "@/client"

interface WorkflowPullOptions {
  commit_sha: string
  conflict_strategy: ConflictStrategy
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
    mutationFn: async (
      options: WorkflowPullOptions & { repository_url: string }
    ): Promise<PullResult> => {
      const requestBody: WorkflowSyncPullRequest = {
        repository_url: options.repository_url,
        commit_sha: options.commit_sha,
        conflict_strategy: options.conflict_strategy,
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
  gitRepoUrl: string | null,
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
      gitRepoUrl,
      workspaceId,
      options?.branch ?? "main",
      options?.limit ?? 10,
    ],
    queryFn: async (): Promise<GitCommitInfo[]> => {
      if (!gitRepoUrl || !workspaceId) {
        throw new Error("Git repository URL and workspace ID are required")
      }

      const response = await workflowsListWorkflowCommits({
        repositoryUrl: gitRepoUrl,
        branch: options?.branch ?? "main",
        limit: options?.limit ?? 10,
        workspaceId,
      })

      return response
    },
    enabled: !!(gitRepoUrl && workspaceId && options?.enabled !== false),
    staleTime: 5 * 60 * 1000, // 5 minutes
  })

  return {
    commits,
    commitsIsLoading,
    commitsError,
  }
}

// Re-export types from the generated client
export type {
  GitCommitInfo,
  PullResult,
  WorkflowSyncPullRequest,
  ConflictStrategy,
}
export type { WorkflowPullOptions }
