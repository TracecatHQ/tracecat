import React, { useState } from "react";
import { useOnSelectionChange, Node } from "reactflow";
import { Skeleton } from "@/components/ui/skeleton"

import { WorkflowForm } from "@/components/forms/workflow"
import { ActionForm } from "@/components/forms/action"

export function WorkflowPanel() {

  const [isActionNodeSelected, setIsActionNodeSelected] = useState(false);
  const [selectedActionNodeId, setSelectedActionNodeId] = useState<string | null>(null)


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

  if (isActionNodeSelected && !selectedActionNodeId) {
    return (
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
    )
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 flex">
        <div className="flex-1">
          {isActionNodeSelected && selectedActionNodeId ? (
            // Make sure selectedActionNodeId is a string when passed as a prop
            <ActionForm actionId={selectedActionNodeId} />
          ) : (
            <WorkflowForm />
          )}
        </div>
      </div>
    </div>
  );
}
