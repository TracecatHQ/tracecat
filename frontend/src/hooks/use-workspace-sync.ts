import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  type GitCommitInfo,
  type PullResult,
  type WorkflowSyncPullRequest,
  workflowsListWorkflowCommits,
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
