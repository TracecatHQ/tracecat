import React, { useState } from "react";
import { useOnSelectionChange, Node } from "reactflow";
import { Skeleton } from "@/components/ui/skeleton"

import { WorkflowForm } from "@/components/forms/workflow"
import { ActionForm } from "@/components/forms/action"
import { useSelectedWorkflowMetadata } from "@/providers/selected-workflow"

export function WorkflowPanel() {

  const [isActionNodeSelected, setIsActionNodeSelected] = useState(false);
  const [selectedActionNodeId, setSelectedActionNodeId] = useState<string | null>(null)
  const { selectedWorkflowMetadata } = useSelectedWorkflowMetadata()

  // Workflow metadata
  const workflowId = selectedWorkflowMetadata.id
  const workflowTitle = selectedWorkflowMetadata.title
  const workflowDescription = selectedWorkflowMetadata.description
  const workflowStatus = selectedWorkflowMetadata.status

  useOnSelectionChange({
    onChange: ({ nodes }: { nodes: Node[] }) => {
      const actionNodeSelected = nodes.find((node: Node) => node.type === "action");
      if (actionNodeSelected) {
        setIsActionNodeSelected(true);
        setSelectedActionNodeId(actionNodeSelected.id);
      } else {
        setIsActionNodeSelected(false);
        setSelectedActionNodeId(null);
      }
    }
  });

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 flex">
        <div className="flex-1">
          {isActionNodeSelected && selectedActionNodeId ? (
            <ActionForm actionId={selectedActionNodeId} />
          ) : (!isActionNodeSelected && workflowId && workflowTitle && workflowDescription && workflowStatus) ? (
            <WorkflowForm
              workflowId={workflowId}
              workflowTitle={workflowTitle}
              workflowDescription={workflowDescription}
              workflowStatus={workflowStatus}
            />
          ) : (
            <div className="flex flex-col h-full">
              <div className="flex-1 flex">
                <div className="flex-1">
                  <div className="flex items-center space-x-2 p-4">
                    <div className="space-y-2">
                      <Skeleton className="h-4 w-[250px]" />
                      <Skeleton className="h-4 w-[200px]" />
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
