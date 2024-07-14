import React from "react"
import { useWorkflowBuilder } from "@/providers/builder"
import { useWorkflow } from "@/providers/workflow"
import { Node } from "reactflow"

import { Workflow } from "@/types/schemas"
import { usePanelAction } from "@/lib/hooks"
import { FormLoading } from "@/components/loading/form"
import { AlertNotification } from "@/components/notifications"
import { NodeType } from "@/components/workspace/canvas/canvas"
import { TriggerNodeData } from "@/components/workspace/canvas/trigger-node"
import { UDFNodeData } from "@/components/workspace/canvas/udf-node"
import { TriggerPanel } from "@/components/workspace/panel/trigger-panel"
import { UDFActionPanel } from "@/components/workspace/panel/udf-panel"
import { WorkflowControls } from "@/components/workspace/panel/workflow/controls"
import { WorkflowForm } from "@/components/workspace/panel/workflow/form"

export function WorkspacePanel() {
  const { selectedNodeId, getNode } = useWorkflowBuilder()
  const { workflow } = useWorkflow()
  const selectedNode = getNode(selectedNodeId ?? "")

  return (
    <div className="size-full overflow-auto">
      <Inner workflow={workflow} selectedNode={selectedNode} />
    </div>
  )
}
function Inner({
  workflow,
  selectedNode,
}: {
  workflow: Workflow | null
  selectedNode?: NodeType
}) {
  if (!workflow) {
    return <FormLoading />
  }
  // Workflow is loaded
  if (selectedNode) {
    return <NodePanel node={selectedNode} workflow={workflow} />
  }
  // No node is selected
  return (
    <div>
      <WorkflowForm workflow={workflow} />
      <WorkflowControls workflow={workflow} />
    </div>
  )
}

function NodePanel({ node, workflow }: { node: NodeType; workflow: Workflow }) {
  switch (node.type) {
    case "udf":
      return (
        <WrappedUDFPanel
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

  function WrappedUDFPanel({
    node,
    workflowId,
  }: {
    node: Node<UDFNodeData>
    workflowId: string
  }) {
    const panelAction = usePanelAction(node.id, workflowId)
    const nodeData = node.data

    return (
      <UDFActionPanel
        panelAction={panelAction}
        type={nodeData.type}
        actionId={node.id}
        workflowId={workflowId}
      />
    )
  }
}
