"use client"

import React, {
  createContext,
  ReactNode,
  SetStateAction,
  useCallback,
  useContext,
  useState,
} from "react"
import { useSession } from "@/providers/session"
import { useWorkflowMetadata } from "@/providers/workflow"
import { Edge, Node, useOnSelectionChange, useReactFlow } from "reactflow"

import { updateDndFlow } from "@/lib/flow"
import { ActionNodeType } from "@/components/action-node"

interface ReactFlowContextType {
  selectedNodeId: string | null
  getNode: (id: string) => ActionNodeType | undefined
  setNodes: React.Dispatch<SetStateAction<Node[]>>
  setEdges: React.Dispatch<SetStateAction<Edge[]>>
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

  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
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
  const setReactFlowEdges = useCallback(
    (edges: Edge[] | ((edges: Edge[]) => Edge[])) => {
      reactFlowInstance.setEdges(edges)
      updateDndFlow(maybeSession, workflowId, reactFlowInstance)
    },
    [maybeSession, workflowId, reactFlowInstance]
  )
  useOnSelectionChange({
    onChange: ({ nodes }: { nodes: ActionNodeType[] }) => {
      const actionNodeSelected = nodes.find(
        (node: ActionNodeType) => node.type === "action"
      )
      setSelectedNodeId(actionNodeSelected?.id ?? null)
    },
  })

  return (
    <ReactFlowInteractionsContext.Provider
      value={{
        selectedNodeId,
        getNode: reactFlowInstance.getNode,
        setNodes: setReactFlowNodes,
        setEdges: setReactFlowEdges,
      }}
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
