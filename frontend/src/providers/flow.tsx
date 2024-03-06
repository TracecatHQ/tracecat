import React, {
  createContext,
  ReactNode,
  SetStateAction,
  useContext,
} from "react"
import { useSelectedWorkflowMetadata } from "@/providers/selected-workflow"
import { Node, useReactFlow } from "reactflow"

import { saveFlow } from "@/lib/flow"

interface ReactFlowContextType {
  setNodes: React.Dispatch<SetStateAction<Node[]>>
}

const ReactFlowInteractionsContext = createContext<
  ReactFlowContextType | undefined
>(undefined)

interface ReactFlowInteractionsProviderProps {
  children: ReactNode
}

export const WorkflowBuilderProvider: React.FC<
  ReactFlowInteractionsProviderProps
> = ({ children }) => {
  const reactFlowInstance = useReactFlow()
  const { selectedWorkflowMetadata } = useSelectedWorkflowMetadata()
  const workflowId = selectedWorkflowMetadata.id

  const setReactFlowNodes = (nodes: Node[] | ((nodes: Node[]) => Node[])) => {
    reactFlowInstance.setNodes(nodes)
    saveFlow(workflowId, reactFlowInstance)
  }

  return (
    <ReactFlowInteractionsContext.Provider
      value={{ setNodes: setReactFlowNodes }}
    >
      {children}
    </ReactFlowInteractionsContext.Provider>
  )
}

export const useWorkflowBuilder = (): ReactFlowContextType => {
  const context = useContext(ReactFlowInteractionsContext)
  if (context === undefined) {
    throw new Error(
      "useReactFlowInteractions must be used within a ReactFlowInteractionsProvider"
    )
  }
  return context
}
