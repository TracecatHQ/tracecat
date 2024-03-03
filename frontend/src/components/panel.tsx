import React, { useState } from "react";
import ReactFlow, { useOnSelectionChange, Node } from "reactflow";

import { WorkflowForm } from "@/components/forms/workflow"
import { ActionForm } from "@/components/forms/action"

export function WorkflowPanel() {

  const [isActionNodeSelected, setIsActionNodeSelected] = useState(false);

  useOnSelectionChange({
    onChange: ({ nodes }: { nodes: Node[] }) => {
      // Assumes ActionNode can be identified by a type property equal to 'action'
      const actionNodeSelected = nodes.some((node: Node) => node.type === 'action');
      setIsActionNodeSelected(actionNodeSelected);
    }
  });

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 flex">
        <div className="flex-1">
          {isActionNodeSelected ? <ActionForm /> : <WorkflowForm />}
        </div>
      </div>
    </div>
  )
}
