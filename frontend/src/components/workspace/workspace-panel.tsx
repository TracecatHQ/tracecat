import React from "react"
import { useWorkflowBuilder } from "@/providers/builder"
import { useWorkflowMetadata } from "@/providers/workflow"

import { FormLoading } from "@/components/loading/form"
import { ActionForm } from "@/components/workspace/action-panel/form"
import { WorkflowControlsForm } from "@/components/workspace/workflow-panel/controls"
import { WorkflowForm } from "@/components/workspace/workflow-panel/form"
import { WorkflowRunsView } from "@/components/workspace/workflow-panel/runs"

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
        <div className="space-y-4 p-4">
          <WorkflowForm workflow={workflow} isOnline={isOnline} />
          <WorkflowControlsForm workflow={workflow} />
          <WorkflowRunsView workflowId={workflow.id} />
        </div>
      ) : (
        <FormLoading />
      )}
    </div>
  )
}
