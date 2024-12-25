import React from "react"
import { WorkflowRead } from "@/client"
import { useWorkflowBuilder } from "@/providers/builder"
import { useWorkflow } from "@/providers/workflow"

import { FormLoading } from "@/components/loading/form"
import { AlertNotification } from "@/components/notifications"
import { NodeType } from "@/components/workbench/canvas/canvas"
import { ActionPanel } from "@/components/workbench/panel/action-panel"
import { TriggerPanel } from "@/components/workbench/panel/trigger-panel"
import { WorkflowPanel } from "@/components/workbench/panel/workflow-panel"

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
        <WorkflowPanel workflow={workflow} />
      )}
    </div>
  )
}
function NodePanel({
  node,
  workflow,
}: {
  node: NodeType
  workflow: WorkflowRead
}) {
  switch (node.type) {
    case "udf":
      return <ActionPanel actionId={node.id} workflowId={workflow.id} />
    case "trigger":
      return <TriggerPanel workflow={workflow} />
    case "selector":
      // XXX: Unreachable, as we never select the selector node
      return <></>
    default:
      return <AlertNotification level="error" message="Unknown node type" />
  }
}
