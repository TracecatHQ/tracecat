import React, {
  createContext,
  ReactNode,
  SetStateAction,
  useCallback,
  useContext,
} from "react"
import { useSession } from "@/providers/session"
import { useWorkflowMetadata } from "@/providers/workflow"
import { Node, useReactFlow } from "reactflow"

import { updateDndFlow } from "@/lib/flow"
import { ActionNodeType } from "@/components/action-node"

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
  const maybeSession = useSession()
  const reactFlowInstance = useReactFlow()
  const { workflowId } = useWorkflowMetadata()
  if (!workflowId) {
    throw new Error("No workflow ID provided")
  }

  const setReactFlowNodes = useCallback(
    (
      nodes: ActionNodeType[] | ((nodes: ActionNodeType[]) => ActionNodeType[])
    ) => {
      reactFlowInstance.setNodes(nodes)
      updateDndFlow(maybeSession, workflowId, reactFlowInstance)
    },
    [maybeSession, workflowId, reactFlowInstance]
  )

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
