import React from "react"
import { useWorkflowBuilder } from "@/providers/builder"
import { useWorkflowMetadata } from "@/providers/workflow"

import { Skeleton } from "@/components/ui/skeleton"
import { ActionForm } from "@/components/forms/action"
import { WorkflowForm } from "@/components/forms/workflow"

export function WorkspacePanel() {
  const { selectedNodeId, getNode } = useWorkflowBuilder()
  const selectedNode = getNode(selectedNodeId ?? "")
  const { workflow, workflowId, isOnline } = useWorkflowMetadata()

  return (
    <div className="h-full w-full">
      {selectedNode ? (
        <ActionForm
          workflowId={workflowId}
          actionId={selectedNode.id}
          actionType={selectedNode.data.type}
        />
      ) : workflow ? (
        <WorkflowForm
          workflowId={workflow.id}
          workflowTitle={workflow.title}
          workflowDescription={workflow.description}
          isOnline={isOnline}
        />
      ) : (
        <div className="w-full space-x-2 p-4">
          <div className="space-y-2">
            <Skeleton className="h-4 w-[250px]" />
            <Skeleton className="h-4 w-[200px]" />
          </div>
        </div>
      )}
    </div>
  )
}
