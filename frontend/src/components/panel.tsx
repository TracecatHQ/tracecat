import React, { useState } from "react"
import { useSelectedWorkflowMetadata } from "@/providers/selected-workflow"
import { Node, useOnSelectionChange } from "reactflow"

import { Skeleton } from "@/components/ui/skeleton"
import { ActionForm } from "@/components/forms/action"
import { WorkflowForm } from "@/components/forms/workflow"

export function WorkflowPanel() {
  const [isActionNodeSelected, setIsActionNodeSelected] = useState(false)
  const [selectedActionNodeId, setSelectedActionNodeId] = useState<
    string | null
  >(null)
  const [selectedActionNodeType, setSelectedActionNodeType] = useState<
    string | undefined
  >(undefined)
  const { selectedWorkflowMetadata } = useSelectedWorkflowMetadata()

  // Workflow metadata
  const workflowId = selectedWorkflowMetadata.id
  const workflowTitle = selectedWorkflowMetadata.title
  const workflowDescription = selectedWorkflowMetadata.description
  const workflowStatus = selectedWorkflowMetadata.status

  useOnSelectionChange({
    onChange: ({ nodes }: { nodes: Node[] }) => {
      const actionNodeSelected = nodes.find(
        (node: Node) => node.type === "action"
      )
      if (actionNodeSelected) {
        setIsActionNodeSelected(true)
        setSelectedActionNodeId(actionNodeSelected.id)
        setSelectedActionNodeType(actionNodeSelected.data.type)
      } else {
        setIsActionNodeSelected(false)
        setSelectedActionNodeId(null)
        setSelectedActionNodeType(undefined)
      }
    },
  })

  return (
    <>
      {isActionNodeSelected &&
      selectedActionNodeId &&
      selectedActionNodeType ? (
        <ActionForm
          actionId={selectedActionNodeId}
          actionType={selectedActionNodeType}
        />
      ) : !isActionNodeSelected &&
        workflowId &&
        workflowTitle &&
        workflowDescription &&
        workflowStatus ? (
        <WorkflowForm
          workflowId={workflowId}
          workflowTitle={workflowTitle}
          workflowDescription={workflowDescription}
          workflowStatus={workflowStatus}
        />
      ) : (
        <div className="w-full space-x-2 p-4">
          <div className="space-y-2">
            <Skeleton className="h-4 w-[250px]" />
            <Skeleton className="h-4 w-[200px]" />
          </div>
        </div>
      )}
    </>
  )
}
