"use client"

import React, {
  createContext,
  ReactNode,
  SetStateAction,
  useCallback,
  useContext,
  useEffect,
  useRef,
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
import {
  NodeType,
  WorkflowCanvasRef,
} from "@/components/workbench/canvas/canvas"
import { EventsSidebarRef } from "@/components/workbench/events/events-sidebar"

interface ReactFlowContextType {
  reactFlow: ReactFlowInstance
  workflowId: string | null
  workspaceId: string
  selectedNodeId: string | null
  getNode: (id: string) => NodeType | undefined
  setNodes: React.Dispatch<SetStateAction<Node[]>>
  setEdges: React.Dispatch<SetStateAction<Edge[]>>
  setSelectedNodeId: React.Dispatch<SetStateAction<string | null>>
  canvasRef: React.RefObject<WorkflowCanvasRef>
  sidebarRef: React.RefObject<EventsSidebarRef>
  isSidebarCollapsed: boolean
  toggleSidebar: () => void
  expandSidebarAndFocusEvents: () => void
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
  const [isSidebarCollapsed, setIsSidebarCollapsed] = React.useState(false)
  const canvasRef = useRef<WorkflowCanvasRef>(null)
  const sidebarRef = useRef<EventsSidebarRef>(null)

  useEffect(() => {
    setSelectedNodeId(null)
  }, [workflowId])

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

  const toggleSidebar = React.useCallback(() => {
    setIsSidebarCollapsed((prev: boolean) => {
      const newState = !prev
      if (sidebarRef.current) {
        if (newState) {
          sidebarRef.current.collapse()
        } else {
          sidebarRef.current.expand()
        }
      }
      return newState
    })
  }, [sidebarRef])

  const expandSidebarAndFocusEvents = React.useCallback(() => {
    setIsSidebarCollapsed(() => {
      const newState = false
      if (sidebarRef.current) {
        sidebarRef.current.expand()
        // sidebarRef.current.setActiveTab("workflow-events")
      }
      return newState
    })
  }, [sidebarRef])

  const value = React.useMemo(
    () => ({
      workflowId,
      workspaceId,
      selectedNodeId,
      getNode: reactFlowInstance.getNode,
      setNodes: setReactFlowNodes,
      setEdges: setReactFlowEdges,
      setSelectedNodeId: setSelectedNodeId,
      reactFlow: reactFlowInstance,
      canvasRef,
      sidebarRef,
      isSidebarCollapsed,
      toggleSidebar,
      expandSidebarAndFocusEvents,
    }),
    [
      workflowId,
      workspaceId,
      selectedNodeId,
      reactFlowInstance,
      setReactFlowNodes,
      setReactFlowEdges,
      setSelectedNodeId,
      canvasRef,
      sidebarRef,
      isSidebarCollapsed,
      toggleSidebar,
      expandSidebarAndFocusEvents,
    ]
  )

  // Don't render anything if no workflow is selected
  if (!workflowId) {
    return children
  }
  if (error) {
    console.error("Builder: Error fetching workflow metadata:", error)
    throw error
  }

  return (
    <ReactFlowInteractionsContext.Provider value={value}>
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
