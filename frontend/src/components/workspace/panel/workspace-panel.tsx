import React from "react"
import { useWorkflowBuilder } from "@/providers/builder"
import { useWorkflowMetadata } from "@/providers/workflow"
import { Node } from "reactflow"

import { usePanelAction } from "@/lib/hooks"
import { FormLoading } from "@/components/loading/form"
import { UDFNodeData } from "@/components/workspace/canvas/udf-node"
import { UDFActionPanel } from "@/components/workspace/panel/udf-panel"
import { WorkflowControlsForm } from "@/components/workspace/panel/workflow/controls"
import { WorkflowForm } from "@/components/workspace/panel/workflow/form"
import { WorkflowRunsView } from "@/components/workspace/panel/workflow/runs"

export function WorkspacePanel() {
  // Ensure workflow exists
  const { selectedNodeId, getNode } = useWorkflowBuilder()
  const { workflow } = useWorkflowMetadata()
  const selectedNode = getNode(selectedNodeId ?? "")

  return (
    <div className="h-full w-full overflow-auto">
      {!workflow ? (
        <FormLoading />
      ) : selectedNode ? (
        <WrappedUDFPanel selectedNode={selectedNode} workflowId={workflow.id} />
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

function WrappedUDFPanel({
  selectedNode,
  workflowId,
}: {
  selectedNode: Node<UDFNodeData>
  workflowId: string
}) {
  const panelAction = usePanelAction(selectedNode.id, workflowId)
  const nodeData = selectedNode.data

  return (
    <UDFActionPanel
      panelAction={panelAction}
      type={nodeData.type}
      actionId={selectedNode.id}
      workflowId={workflowId}
    />
  )
}
