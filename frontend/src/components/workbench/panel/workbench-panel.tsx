import React from "react"
import { WorkflowResponse } from "@/client"
import { useWorkflowBuilder } from "@/providers/builder"
import { useWorkflow } from "@/providers/workflow"
import { Node } from "reactflow"

import { FormLoading } from "@/components/loading/form"
import { AlertNotification } from "@/components/notifications"
import { NodeType } from "@/components/workbench/canvas/canvas"
import { TriggerNodeData } from "@/components/workbench/canvas/trigger-node"
import { UDFNodeData } from "@/components/workbench/canvas/udf-node"
import { TriggerPanel } from "@/components/workbench/panel/trigger-panel"
import { UDFActionPanel } from "@/components/workbench/panel/udf-panel"
import { WorkflowForm } from "@/components/workbench/panel/workflow/form"

export function WorkbenchPanel() {
  const { selectedNodeId, getNode } = useWorkflowBuilder()
  const { workflow, isLoading, error } = useWorkflow()
  const selectedNode = getNode(selectedNodeId ?? "")

  if (isLoading || !workflow) {
    return <FormLoading />
  }
  if (error) {
    return (
      <div className="flex size-full items-center justify-center">
        <AlertNotification level="error" message={error.message} />
      </div>
    )
  }
  if (selectedNodeId && !selectedNode) {
    return (
      <div className="flex size-full items-center justify-center">
        <AlertNotification
          level="error"
          message={`Node ${selectedNodeId} not found`}
        />
      </div>
    )
  }

  return (
    <div className="size-full overflow-auto">
      {selectedNode ? (
        <NodePanel node={selectedNode} workflow={workflow} />
      ) : (
        <WorkflowForm workflow={workflow} />
      )}
    </div>
  )
}
function NodePanel({
  node,
  workflow,
}: {
  node: NodeType
  workflow: WorkflowResponse
}) {
  switch (node.type) {
    case "udf":
      return (
        <UDFActionPanel
          node={node as Node<UDFNodeData>}
          workflowId={workflow.id}
        />
      )
    case "trigger":
      return (
        <TriggerPanel
          nodeData={node.data as TriggerNodeData}
          workflow={workflow}
        />
      )
    case "selector":
      // XXX: Unreachable, as we never select the selector node
      return <></>
    default:
      return <AlertNotification level="error" message="Unknown node type" />
  }
}
