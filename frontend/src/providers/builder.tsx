import React, {
  createContext,
  ReactNode,
  SetStateAction,
  useCallback,
  useContext,
} from "react"
import { useParams } from "next/navigation"
import { useSession } from "@/providers/session"
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
  const params = useParams<{ id: string }>()
  const workflowId = params.id

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
