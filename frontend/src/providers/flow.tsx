import React, { createContext, useContext, ReactNode, useState, Dispatch, SetStateAction, useCallback } from "react";
import { ReactFlowProvider, ReactFlowInstance, Node, useNodesState } from "reactflow";

interface ReactFlowContextType {
  reactFlowInstance: ReactFlowInstance | null;
  setReactFlowInstance: Dispatch<SetStateAction<ReactFlowInstance | null>>;
  setNodes: (updateFn: (nodes: Node[]) => Node[]) => void; // Define setNodes in context
}

const ReactFlowInteractionsContext = createContext<ReactFlowContextType | undefined>(undefined);

interface ReactFlowInteractionsProviderProps {
  children: ReactNode;
}

export const WorkflowBuilderProvider: React.FC<ReactFlowInteractionsProviderProps> = ({ children }) => {
  const [nodes, setNodes, onNodesChange] = useNodesState([])
  const [reactFlowInstance, setReactFlowInstance] = useState<ReactFlowInstance | null>(null)

  console.log(reactFlowInstance)
  return (
    <ReactFlowProvider>
      <ReactFlowInteractionsContext.Provider value={{ reactFlowInstance, setReactFlowInstance, setNodes }}>
        {children}
      </ReactFlowInteractionsContext.Provider>
    </ReactFlowProvider>
  );
};

export const useWorkflowBuilder = (): ReactFlowContextType => {
  const context = useContext(ReactFlowInteractionsContext);
  if (context === undefined) {
    throw new Error("useReactFlowInteractions must be used within a ReactFlowInteractionsProvider");
  }
  return context;
};
