import React from "react"
import { useWorkflowBuilder } from "@/providers/builder"
import { useWorkflowMetadata } from "@/providers/workflow"
import { Node } from "reactflow"

import { ActionType, IntegrationType } from "@/types/schemas"
import { FormLoading } from "@/components/loading/form"
import { ActionNodeData } from "@/components/workspace/canvas/action-node"
import { IntegrationNodeData } from "@/components/workspace/canvas/integration-node"
import { ActionForm } from "@/components/workspace/panel/action/form"
import { IntegrationForm } from "@/components/workspace/panel/integration/form"
import { WorkflowControlsForm } from "@/components/workspace/panel/workflow/controls"
import { WorkflowForm } from "@/components/workspace/panel/workflow/form"
import { WorkflowRunsView } from "@/components/workspace/panel/workflow/runs"

export function WorkspacePanel() {
  const { selectedNodeId, getNode } = useWorkflowBuilder()
  const selectedNode = getNode(selectedNodeId ?? "")
  const { workflow, workflowId, isOnline } = useWorkflowMetadata()
  console.log("selectedNode", selectedNode)

  return (
    <div className="h-full w-full overflow-auto">
      {selectedNode ? (
        getNodeForm(selectedNode, workflowId)
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

function getNodeForm<T extends ActionNodeData | IntegrationNodeData>(
  selectedNode: Node<T>,
  workflowId: string | null
) {
  switch (selectedNode.type) {
    case "action":
      return (
        <ActionForm
          workflowId={workflowId}
          actionId={selectedNode.id}
          actionType={selectedNode.data.type as ActionType} // Type narrowing
        />
      )

    case "integrations":
      return (
        <IntegrationForm
          workflowId={workflowId}
          actionId={selectedNode.id}
          integrationType={selectedNode.data.type as IntegrationType} // Type narrowing
        />
      )
    default:
      return null
  }
}
