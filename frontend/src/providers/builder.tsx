"use client"

import React, {
  createContext,
  ReactNode,
  SetStateAction,
  useCallback,
  useContext,
  useState,
} from "react"
import { useWorkflow } from "@/providers/workflow"
import {
  Edge,
  Node,
  ReactFlowInstance,
  useOnSelectionChange,
  useReactFlow,
} from "reactflow"

import { pruneGraphObject } from "@/lib/workflow"
import { NodeType } from "@/components/workbench/canvas/canvas"

interface ReactFlowContextType {
  reactFlow: ReactFlowInstance
  workflowId: string | null
  workspaceId: string
  selectedNodeId: string | null
  getNode: (id: string) => NodeType | undefined
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
  const reactFlowInstance = useReactFlow()
  const { workspaceId, workflowId, error, updateWorkflow } = useWorkflow()

  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  if (!workflowId) {
    throw new Error("No workflow ID provided")
  }

  const setReactFlowNodes = useCallback(
    (nodes: NodeType[] | ((nodes: NodeType[]) => NodeType[])) => {
      reactFlowInstance.setNodes(nodes)
      updateWorkflow({ object: pruneGraphObject(reactFlowInstance) })
    },
    [workflowId, reactFlowInstance]
  )
  const setReactFlowEdges = useCallback(
    (edges: Edge[] | ((edges: Edge[]) => Edge[])) => {
      reactFlowInstance.setEdges(edges)
      updateWorkflow({ object: pruneGraphObject(reactFlowInstance) })
    },
    [workflowId, reactFlowInstance]
  )
  useOnSelectionChange({
    onChange: ({ nodes }: { nodes: NodeType[] }) => {
      const nodeSelected = nodes[0]
      if (nodeSelected?.type === "selector") {
        return
      }
      setSelectedNodeId(nodeSelected?.id ?? null)
    },
  })
  if (error) {
    console.error("Builder: Error fetching workflow metadata:", error)
    throw error
  }

  return (
    <ReactFlowInteractionsContext.Provider
      value={{
        workflowId,
        workspaceId,
        selectedNodeId,
        getNode: reactFlowInstance.getNode,
        setNodes: setReactFlowNodes,
        setEdges: setReactFlowEdges,
        reactFlow: reactFlowInstance,
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
