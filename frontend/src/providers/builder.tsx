"use client"

import {
  type Edge,
  type Node,
  type ReactFlowInstance,
  useOnSelectionChange,
  useReactFlow,
} from "@xyflow/react"
import React, {
  createContext,
  type ReactNode,
  type SetStateAction,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react"
import type {
  NodeType,
  WorkflowCanvasRef,
} from "@/components/builder/canvas/canvas"
import type { EventsSidebarRef } from "@/components/builder/events/events-sidebar"
import type { ActionPanelRef } from "@/components/builder/panel/action-panel"
import {
  DEFAULT_TRIGGER_PANEL_TAB,
  type TriggerPanelTab,
} from "@/components/builder/panel/trigger-panel-tabs"
import { useWorkflow } from "@/providers/workflow"

const SELECTED_NODE_STORAGE_PREFIX = "tracecat:builder:selected-node"

function selectedNodeStorageKey({
  workspaceId,
  workflowId,
}: {
  workspaceId: string
  workflowId: string | null
}): string {
  return `${SELECTED_NODE_STORAGE_PREFIX}:${workspaceId}:${workflowId}`
}

function readPersistedSelectedNode({
  workspaceId,
  workflowId,
}: {
  workspaceId: string
  workflowId: string | null
}): string | null {
  if (!workflowId) {
    return null
  }
  if (typeof window === "undefined") {
    return null
  }
  try {
    return sessionStorage.getItem(
      selectedNodeStorageKey({ workspaceId, workflowId })
    )
  } catch {
    return null
  }
}

function writePersistedSelectedNode({
  workspaceId,
  workflowId,
  selectedNodeId,
}: {
  workspaceId: string
  workflowId: string | null
  selectedNodeId: string | null
}): void {
  if (!workflowId) {
    return
  }
  if (typeof window === "undefined") {
    return
  }

  const key = selectedNodeStorageKey({ workspaceId, workflowId })
  try {
    if (selectedNodeId) {
      sessionStorage.setItem(key, selectedNodeId)
    } else {
      sessionStorage.removeItem(key)
    }
  } catch {
    // Ignore storage failures (e.g. blocked storage)
  }
}

interface ReactFlowContextType {
  reactFlow: ReactFlowInstance
  workflowId: string
  workspaceId: string
  selectedNodeId: string | null
  getNode: (id: string) => Node | undefined
  setNodes: React.Dispatch<SetStateAction<Node[]>>
  setEdges: React.Dispatch<SetStateAction<Edge[]>>
  setSelectedNodeId: React.Dispatch<SetStateAction<string | null>>
  canvasRef: React.RefObject<WorkflowCanvasRef>
  sidebarRef: React.RefObject<EventsSidebarRef>
  actionPanelRef: React.RefObject<ActionPanelRef>
  isSidebarCollapsed: boolean
  isActionPanelCollapsed: boolean
  toggleSidebar: () => void
  toggleActionPanel: () => void
  expandSidebarAndFocusEvents: () => void
  triggerPanelTab: TriggerPanelTab
  setTriggerPanelTab: React.Dispatch<SetStateAction<TriggerPanelTab>>
  selectedActionEventRef?: string
  setSelectedActionEventRef: React.Dispatch<SetStateAction<string | undefined>>
  currentExecutionId: string | null
  setCurrentExecutionId: React.Dispatch<SetStateAction<string | null>>
  actionDrafts: Record<string, unknown>
  setActionDraft: (actionId: string, draft: unknown) => void
  clearActionDraft: (actionId: string) => void
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
  const { workspaceId, workflowId, error } = useWorkflow()

  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [selectedActionEventRef, setSelectedActionEventRef] = useState<
    string | undefined
  >(undefined)
  const [isSidebarCollapsed, setIsSidebarCollapsed] = React.useState(false)
  const [isActionPanelCollapsed, setIsActionPanelCollapsed] =
    React.useState(false)
  const [triggerPanelTab, setTriggerPanelTab] = useState<TriggerPanelTab>(
    DEFAULT_TRIGGER_PANEL_TAB
  )
  const [currentExecutionId, setCurrentExecutionId] = useState<string | null>(
    null
  )
  // In-memory map of actionId -> last edited form values.
  // This lets the action panel restore unsaved edits when the user
  // switches between nodes without touching the backend.
  const [actionDrafts, setActionDrafts] = useState<Record<string, unknown>>({})
  const canvasRef = useRef<WorkflowCanvasRef>(null)
  const sidebarRef = useRef<EventsSidebarRef>(null)
  const actionPanelRef = useRef<ActionPanelRef>(null)
  const lastPersistedSelectionRef = useRef<{
    key: string
    value: string | null
  } | null>(null)

  useEffect(() => {
    // Restore the last selection for this workflow in the current browser
    // session, then clear transient per-workflow UI state.
    if (!workflowId) {
      setSelectedNodeId(null)
      return
    }
    const restoredSelection = readPersistedSelectedNode({
      workspaceId,
      workflowId,
    })
    const key = selectedNodeStorageKey({ workspaceId, workflowId })
    lastPersistedSelectionRef.current = {
      key,
      value: restoredSelection,
    }
    setSelectedNodeId(restoredSelection)
    setCurrentExecutionId(null)
    setActionDrafts({})
    setTriggerPanelTab(DEFAULT_TRIGGER_PANEL_TAB)
  }, [workspaceId, workflowId])

  useEffect(() => {
    if (!workflowId) {
      return
    }
    const key = selectedNodeStorageKey({ workspaceId, workflowId })
    const lastPersisted = lastPersistedSelectionRef.current
    if (
      lastPersisted &&
      lastPersisted.key === key &&
      lastPersisted.value === selectedNodeId
    ) {
      return
    }
    writePersistedSelectedNode({ workspaceId, workflowId, selectedNodeId })
    lastPersistedSelectionRef.current = {
      key,
      value: selectedNodeId,
    }
  }, [workspaceId, workflowId, selectedNodeId])

  const setReactFlowNodes = useCallback(
    (nodes: Node[] | ((nodes: Node[]) => Node[])) => {
      reactFlowInstance.setNodes(nodes)
    },
    [reactFlowInstance]
  )
  const setReactFlowEdges = useCallback(
    (edges: Edge[] | ((edges: Edge[]) => Edge[])) => {
      reactFlowInstance.setEdges(edges)
    },
    [reactFlowInstance]
  )

  const setActionDraft = useCallback((actionId: string, draft: unknown) => {
    // Store or update the draft for a given actionId.
    setActionDrafts((prev) => ({
      ...prev,
      [actionId]: draft,
    }))
  }, [])

  const clearActionDraft = useCallback((actionId: string) => {
    // Remove a single action's draft (typically after a successful save).
    setActionDrafts((prev) => {
      const { [actionId]: _removed, ...rest } = prev
      return rest
    })
  }, [])
  useOnSelectionChange({
    onChange: ({ nodes }: { nodes: NodeType[] }) => {
      const nodeSelected = nodes[0]
      if (nodeSelected?.type === "selector") {
        return
      }
      if (nodeSelected) {
        setSelectedNodeId(nodeSelected.id)
        return
      }

      // Keep selection when the canvas unmounts (e.g. switching to runs).
      // The node list is empty in that state and we should not clear the
      // persisted "last selected node" for this workflow.
      if (reactFlowInstance.getNodes().length === 0) {
        return
      }
      setSelectedNodeId(null)
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

  const toggleActionPanel = React.useCallback(() => {
    setIsActionPanelCollapsed((prev: boolean) => {
      const newState = !prev
      if (actionPanelRef.current) {
        if (newState) {
          actionPanelRef.current.collapse()
        } else {
          actionPanelRef.current.expand()
        }
      }
      return newState
    })
  }, [actionPanelRef])

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
      // safe: provider not rendered when !workflowId
      workflowId: workflowId!,
      workspaceId,
      selectedNodeId,
      selectedActionEventRef,
      getNode: reactFlowInstance.getNode,
      setNodes: setReactFlowNodes,
      setEdges: setReactFlowEdges,
      setSelectedNodeId,
      setSelectedActionEventRef,
      reactFlow: reactFlowInstance,
      canvasRef,
      sidebarRef,
      isSidebarCollapsed,
      toggleSidebar,
      expandSidebarAndFocusEvents,
      triggerPanelTab,
      setTriggerPanelTab,
      actionPanelRef,
      isActionPanelCollapsed,
      toggleActionPanel,
      currentExecutionId,
      setCurrentExecutionId,
      actionDrafts,
      setActionDraft,
      clearActionDraft,
    }),
    [
      workflowId,
      workspaceId,
      selectedNodeId,
      reactFlowInstance,
      setReactFlowNodes,
      setReactFlowEdges,
      setSelectedNodeId,
      selectedActionEventRef,
      setSelectedActionEventRef,
      canvasRef,
      sidebarRef,
      isSidebarCollapsed,
      toggleSidebar,
      expandSidebarAndFocusEvents,
      triggerPanelTab,
      setTriggerPanelTab,
      actionPanelRef,
      isActionPanelCollapsed,
      toggleActionPanel,
      currentExecutionId,
      setCurrentExecutionId,
      actionDrafts,
      setActionDraft,
      clearActionDraft,
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
