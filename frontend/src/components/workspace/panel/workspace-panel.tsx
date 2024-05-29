import React from "react"
import { useWorkflowBuilder } from "@/providers/builder"
import { useWorkflowMetadata } from "@/providers/workflow"
import { Node } from "reactflow"

import { type ActionType } from "@/types/schemas"
import { usePanelAction } from "@/lib/hooks"
import { FormLoading } from "@/components/loading/form"
import { ActionNodeData } from "@/components/workspace/canvas/action-node"
import { UDFNodeData } from "@/components/workspace/canvas/udf-node"
import { ActionForm } from "@/components/workspace/panel/action/form"
import { UDFForm } from "@/components/workspace/panel/udf-form"
import { WorkflowControlsForm } from "@/components/workspace/panel/workflow/controls"
import { WorkflowForm } from "@/components/workspace/panel/workflow/form"
import { WorkflowRunsView } from "@/components/workspace/panel/workflow/runs"

export function WorkspacePanel() {
  // Ensure workflow exists
  const { selectedNodeId, getNode } = useWorkflowBuilder()
  const { workflow, isOnline } = useWorkflowMetadata()
  const selectedNode = getNode(selectedNodeId ?? "")

  return (
    <div className="h-full w-full overflow-auto">
      {!workflow ? (
        <FormLoading />
      ) : selectedNode ? (
        <WrappedUDFForm selectedNode={selectedNode} workflowId={workflow.id} />
      ) : workflow ? (
        <div>
          <WorkflowForm workflow={workflow} />
          <WorkflowControlsForm workflow={workflow} />
          <WorkflowRunsView workflowId={workflow.id} />
        </div>
      ) : (
        <FormLoading />
      )}
    </div>
  )
}

function WrappedUDFForm({
  selectedNode,
  workflowId,
}: {
  selectedNode: Node<ActionNodeData | UDFNodeData>
  workflowId: string
}) {
  const panelAction = usePanelAction(selectedNode.id, workflowId)
  const nodeData = selectedNode.data

  if (selectedNode.type === "action") {
    return (
      <ActionForm
        actionId={selectedNode.id}
        workflowId={workflowId}
        actionType={nodeData.type as ActionType}
      />
    )
  }

  return (
    <UDFForm
      panelAction={panelAction}
      type={nodeData.type}
      actionId={selectedNode.id}
      workflowId={workflowId}
    />
  )
}
