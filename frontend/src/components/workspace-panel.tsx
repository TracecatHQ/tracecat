import React from "react"
import { useWorkflowBuilder } from "@/providers/builder"
import { useWorkflowMetadata } from "@/providers/workflow"

import { ActionForm } from "@/components/forms/action"
import { WorkflowForm } from "@/components/forms/workflow"
import { CenteredSpinner } from "@/components/loading/spinner"

export function WorkspacePanel() {
  const { selectedNodeId, getNode } = useWorkflowBuilder()
  const selectedNode = getNode(selectedNodeId ?? "")
  const { workflow, workflowId, isOnline } = useWorkflowMetadata()

  return (
    <div className="h-full w-full overflow-auto">
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
        <CenteredSpinner />
      )}
    </div>
  )
}
