import { WorkflowRead, workflowsGetWorkflow } from "@/client"
import { useWorkspace } from "@/providers/workspace"
import { useQuery } from "@tanstack/react-query"

import { useWorkflowManager } from "@/lib/hooks"

export function useSubflow({
  workflowId,
  workflowAlias,
}: {
  workflowId?: string
  workflowAlias?: string
}) {
  const { workspaceId } = useWorkspace()
  const { workflows } = useWorkflowManager()
  const resolvedId = workflows?.find((w) => w.alias === workflowAlias)?.id

  return useQuery<WorkflowRead | null>({
    queryKey: ["child-workflow", workspaceId, resolvedId],
    queryFn: async () => {
      if (!resolvedId) {
        return null
      }
      return await workflowsGetWorkflow({
        workspaceId,
        workflowId: resolvedId,
      })
    },
    enabled: Boolean(workspaceId && (workflowId || workflowAlias)),
  })
}
