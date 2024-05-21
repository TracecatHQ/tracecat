import React from "react"
import { useWorkflowBuilder } from "@/providers/builder"
import { useWorkflowMetadata } from "@/providers/workflow"
import { Node } from "reactflow"

import { FormLoading } from "@/components/loading/form"
import { ActionNodeData } from "@/components/workspace/canvas/action-node"
import { IntegrationNodeData } from "@/components/workspace/canvas/integration-node"
import { UDFNodeData } from "@/components/workspace/canvas/udf-node"
import { ActionForm } from "@/components/workspace/panel/action/form"
import { IntegrationForm } from "@/components/workspace/panel/integration/form"
import { UDFForm } from "@/components/workspace/panel/udf-form"
import { WorkflowControlsForm } from "@/components/workspace/panel/workflow/controls"
import { WorkflowForm } from "@/components/workspace/panel/workflow/form"
import { WorkflowRunsView } from "@/components/workspace/panel/workflow/runs"

export function WorkspacePanel() {
  const { selectedNodeId, getNode } = useWorkflowBuilder()
  const selectedNode = getNode(selectedNodeId ?? "")
  const { workflow, workflowId, isOnline } = useWorkflowMetadata()

  return (
    <div className="h-full w-full overflow-auto">
      {selectedNode && !!workflowId ? (
        getNodeForm(selectedNode, workflowId)
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

function getNodeForm<
  T extends ActionNodeData | IntegrationNodeData | UDFNodeData,
>(selectedNode: Node<T>, workflowId: string) {
  const common = {
    actionId: selectedNode.id,
    workflowId,
  }
  switch (selectedNode.type) {
    case "action":
      const actionNode = selectedNode as Node<ActionNodeData>
      return <ActionForm actionType={actionNode.data.type} {...common} />

    case "integrations":
      const integrationNode = selectedNode as Node<IntegrationNodeData>
      return (
        <IntegrationForm
          integrationType={integrationNode.data.type}
          {...common}
        />
      )
    case "udf":
      const udfNode = selectedNode as Node<UDFNodeData>
      return (
        <UDFForm
          type={udfNode.data.type}
          namespace={udfNode.data.namespace}
          {...common}
        />
      )
    default:
      return null
  }
}
