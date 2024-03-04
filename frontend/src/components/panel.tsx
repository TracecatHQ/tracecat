import React, { useState } from "react";
import { useOnSelectionChange, Node } from "reactflow";

import { WorkflowForm } from "@/components/forms/workflow"
import { ActionForm } from "@/components/forms/action"

export function WorkflowPanel() {

  const [isActionNodeSelected, setIsActionNodeSelected] = useState(false);
  const [selectedActionNodeId, setSelectedActionNodeId] = useState<string | null>(null)
  const [selectedActionNodeData, setSelectedActionNodeData] = useState<any | null>(null)


  useOnSelectionChange({
    onChange: ({ nodes }: { nodes: Node[] }) => {
      const actionNodeSelected = nodes.find((node: Node) => node.type === 'action');
      if (actionNodeSelected) {
        setIsActionNodeSelected(true);
        setSelectedActionNodeId(actionNodeSelected.id);
        setSelectedActionNodeData(actionNodeSelected.data);
      } else {
        setIsActionNodeSelected(false);
        setSelectedActionNodeId(null);
        setSelectedActionNodeData(null);
      }
    }
  });

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 flex">
        <div className="flex-1">
        {isActionNodeSelected ? <ActionForm actionId={selectedActionNodeId} actionData={selectedActionNodeData} /> : <WorkflowForm />}
        </div>
      </div>
    </div>
  )
}
