import { useMutation, useQueryClient } from "@tanstack/react-query"
import type { DSLInput_Input } from "@/client"
import { workflowsApplyDslToWorkflow } from "@/client"

export function useApplyDslToWorkflow(workspaceId: string, workflowId: string) {
  const queryClient = useQueryClient()

  const { mutateAsync: applyDslToWorkflow } = useMutation({
    mutationFn: async (params: DSLInput_Input) =>
      await workflowsApplyDslToWorkflow({
        workspaceId,
        workflowId,
        requestBody: params,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workflow", workflowId] })
    },
  })
  return { applyDslToWorkflow }
}
