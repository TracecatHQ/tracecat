import React from "react"
import { useWorkflowBuilder } from "@/providers/builder"
import { useWorkflow } from "@/providers/workflow"
import { Node } from "reactflow"

import { Workflow } from "@/types/schemas"
import { FormLoading } from "@/components/loading/form"
import { AlertNotification } from "@/components/notifications"
import { NodeType } from "@/components/workspace/canvas/canvas"
import { TriggerNodeData } from "@/components/workspace/canvas/trigger-node"
import { UDFNodeData } from "@/components/workspace/canvas/udf-node"
import { TriggerPanel } from "@/components/workspace/panel/trigger-panel"
import { UDFActionPanel } from "@/components/workspace/panel/udf-panel"
import { WorkflowForm } from "@/components/workspace/panel/workflow/form"

export function WorkspacePanel() {
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
function NodePanel({ node, workflow }: { node: NodeType; workflow: Workflow }) {
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
    default:
      return <AlertNotification level="error" message="Unknown node type" />
  }
}
