import {
  type WorkflowRerunPlan,
  type WorkflowRerunRequest,
  type WorkflowRerunResponse,
  workflowExecutionsPreviewWorkflowExecutionRerun,
  workflowExecutionsRerunWorkflowExecution,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import { retryHandler, type TracecatApiError } from "@/lib/errors"
import { useMutation, useQuery, useQueryClient } from "@/lib/query"

/**
 * Preview how a past run's successful action results match a rerun target.
 */
export function useWorkflowRerunPreview(
  workspaceId: string,
  executionId: string,
  target: Pick<
    WorkflowRerunRequest,
    "use_draft" | "version" | "rerun_from_ref"
  >,
  { enabled = true }: { enabled?: boolean } = {}
) {
  const { data, isLoading, error } = useQuery<
    WorkflowRerunPlan,
    TracecatApiError
  >({
    queryKey: [
      "workflow-rerun-preview",
      workspaceId,
      executionId,
      target.use_draft,
      target.version ?? null,
      target.rerun_from_ref ?? null,
    ],
    queryFn: async () =>
      await workflowExecutionsPreviewWorkflowExecutionRerun({
        workspaceId,
        executionId,
        requestBody: {
          use_draft: target.use_draft,
          version: target.version ?? null,
          fixed_action_refs: null,
          rerun_from_ref: target.rerun_from_ref ?? null,
        },
      }),
    enabled: enabled && Boolean(workspaceId) && Boolean(executionId),
    retry: retryHandler,
    placeholderData: (previous) => previous,
  })

  return {
    rerunPlan: data,
    rerunPlanIsLoading: isLoading,
    rerunPlanError: error,
  }
}

/**
 * Rerun a past execution, reusing results of the selected fixed actions.
 */
export function useRerunWorkflowExecution(
  workspaceId: string,
  executionId: string
) {
  const queryClient = useQueryClient()
  const { mutateAsync, isPending } = useMutation<
    WorkflowRerunResponse,
    TracecatApiError,
    WorkflowRerunRequest
  >({
    mutationFn: async (requestBody) =>
      await workflowExecutionsRerunWorkflowExecution({
        workspaceId,
        executionId,
        requestBody,
      }),
    onSuccess: async ({ wf_exec_id, plan }) => {
      toast({
        title: "Rerun started",
        description: `${plan.fixed_action_refs.length} fixed action(s). ${wf_exec_id}`,
      })
      await queryClient.refetchQueries({
        queryKey: ["last-manual-execution"],
      })
      await queryClient.refetchQueries({
        queryKey: ["compact-workflow-execution", wf_exec_id],
      })
    },
    onError: (error) => {
      const body = error.body as { detail?: unknown } | undefined
      const detail = body?.detail
      let description = "Check the rerun plan and try again."
      if (typeof detail === "string") {
        description = detail
      } else if (
        detail &&
        typeof detail === "object" &&
        "message" in detail &&
        typeof detail.message === "string"
      ) {
        description = detail.message
      }
      toast({ title: "Could not start rerun", description })
    },
  })

  return {
    rerunExecution: mutateAsync,
    rerunExecutionIsPending: isPending,
  }
}
