import React from "react"
import { useWorkflowBuilder } from "@/providers/builder"
import { useWorkflowMetadata } from "@/providers/workflow"

import { Workflow } from "@/types/schemas"
import { ActionForm } from "@/components/forms/action"
import {
  WorkflowControlsForm,
  WorkflowForm,
  WorkflowRunsView,
} from "@/components/forms/workflow"
import { FormLoading } from "@/components/loading/form"

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
        <WorkflowPanel workflow={workflow} isOnline={isOnline} />
      ) : (
        <FormLoading className="bg-slate-100" />
      )}
    </div>
  )
}

function WorkflowPanel({
  workflow,
  isOnline,
}: {
  workflow: Workflow
  isOnline: boolean
}) {
  return (
    <div className="space-y-2">
      <WorkflowForm workflow={workflow} isOnline={isOnline} />
      <WorkflowControlsForm workflow={workflow} />
      <WorkflowRunsView workflowId={workflow.id} />
    </div>
  )
}
