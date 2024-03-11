import React, { useState } from "react"
import { useWorkflowMetadata } from "@/providers/workflow"
import { useOnSelectionChange } from "reactflow"

import { Skeleton } from "@/components/ui/skeleton"
import { ActionNodeType } from "@/components/action-node"
import { ActionForm } from "@/components/forms/action"
import { WorkflowForm } from "@/components/forms/workflow"

export function WorkflowPanel() {
  const [selectedNode, setSelectedNode] = useState<ActionNodeType | null>(null)
  const { workflowMetadata } = useWorkflowMetadata()

  useOnSelectionChange({
    onChange: ({ nodes }: { nodes: ActionNodeType[] }) => {
      const actionNodeSelected = nodes.find(
        (node: ActionNodeType) => node.type === "action"
      )
      setSelectedNode(actionNodeSelected ?? null)
    },
  })

  return (
    <>
      {selectedNode ? (
        <ActionForm
          actionId={selectedNode.id}
          actionType={selectedNode.data.type}
        />
      ) : workflowMetadata ? (
        <WorkflowForm
          workflowId={workflowMetadata.id}
          workflowTitle={workflowMetadata.title}
          workflowDescription={workflowMetadata.description}
          workflowStatus={workflowMetadata.status}
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
