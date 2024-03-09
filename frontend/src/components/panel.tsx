import React, { useState } from "react"
import { useWorkflowMetadata } from "@/providers/workflow"
import { ActionType } from "@/types"
import { Node, useOnSelectionChange } from "reactflow"

import { Skeleton } from "@/components/ui/skeleton"
import { ActionForm } from "@/components/forms/action"
import { WorkflowForm } from "@/components/forms/workflow"

export function WorkflowPanel() {
  const [selectedNode, setSelectedNode] = useState<Node | null>(null)
  const { workflowMetadata } = useWorkflowMetadata()

  useOnSelectionChange({
    onChange: ({ nodes }: { nodes: Node[] }) => {
      const actionNodeSelected = nodes.find(
        (node: Node) => node.type === "action"
      )
      setSelectedNode(actionNodeSelected ?? null)
    },
  })

  return (
    <>
      {selectedNode ? (
        <ActionForm
          actionId={selectedNode.id}
          actionType={selectedNode.data.type as ActionType}
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
