import React, {
  useCallback,
  useEffect,
  useImperativeHandle,
  useReducer,
  useRef,
  useState,
} from "react"
import {
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  Background,
  Connection,
  ConnectionLineType,
  Controls,
  Edge,
  EdgeChange,
  EdgeRemoveChange,
  FitViewOptions,
  MarkerType,
  NodeChange,
  NodeRemoveChange,
  OnConnectStartParams,
  Panel,
  Position,
  ReactFlow,
  ReactFlowInstance,
  useReactFlow,
  XYPosition,
  type Node,
  type ReactFlowJsonObject,
} from "@xyflow/react"
import { useDebouncedCallback } from "use-debounce"
import { v4 as uuid4 } from "uuid"

import "@xyflow/react/dist/style.css"

import { useWorkflow } from "@/providers/workflow"
import Dagre from "@dagrejs/dagre"
import { MoveHorizontalIcon, MoveVerticalIcon, PlusIcon } from "lucide-react"

import { useDeleteAction } from "@/lib/hooks"
import { pruneGraphObject } from "@/lib/workflow"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { useToast } from "@/components/ui/use-toast"
import actionNode, {
  ActionNodeData,
  ActionNodeType,
} from "@/components/workbench/canvas/action-node"
import { DeleteActionNodeDialog } from "@/components/workbench/canvas/delete-node-dialog"
import selectorNode, {
  SelectorNodeData,
  SelectorNodeType,
  SelectorTypename,
} from "@/components/workbench/canvas/selector-node"
import triggerNode, {
  TriggerNodeData,
  TriggerNodeType,
  TriggerTypename,
} from "@/components/workbench/canvas/trigger-node"
import type {
  GraphAction,
  GraphState,
} from "@/components/workbench/canvas/types"

const initialGraphState: GraphState = {
  nodes: [],
  edges: [],
  viewport: { x: 0, y: 0, zoom: 1 },
}

// Graph reducer function
/**
 * Reducer for managing the graph state.
 * Adds debug logging for all action cases.
 * @param state - The current graph state
 * @param action - The action to apply
 * @returns The new graph state
 */
/**
 * Reducer for managing the graph state.
 * Logs the action for each case.
 * @param state - The current graph state
 * @param action - The action to apply
 * @returns The new graph state
 */
function graphReducer(state: GraphState, action: GraphAction): GraphState {
  // Log the action for debugging
  // eslint-disable-next-line no-console
  console.debug("[graphReducer] action:", action)
  switch (action.type) {
    case "SET_INITIAL_GRAPH": {
      return {
        nodes: action.payload.nodes || [],
        edges: action.payload.edges || [],
        viewport: action.payload.viewport || { x: 0, y: 0, zoom: 1 },
      }
    }

    case "NODES_CHANGE": {
      return {
        ...state,
        nodes: applyNodeChanges(action.changes, state.nodes),
      }
    }

    case "EDGES_CHANGE": {
      return {
        ...state,
        edges: applyEdgeChanges(action.changes, state.edges),
      }
    }

    case "VIEWPORT_CHANGE": {
      return {
        ...state,
        viewport: action.viewport,
      }
    }

    case "ADD_NODE": {
      return {
        ...state,
        nodes: [...state.nodes, action.node],
      }
    }

    case "ADD_EDGE": {
      return {
        ...state,
        edges: addEdge(action.edge, state.edges),
      }
    }

    case "SET_NODES_AND_EDGES": {
      return {
        ...state,
        nodes: action.nodes,
        edges: action.edges,
      }
    }

    case "CONFIRMED_DELETION": {
      return {
        ...state,
        nodes: state.nodes.filter((node) => !action.nodeIds.includes(node.id)),
        edges: state.edges.filter(
          (edge) =>
            !action.nodeIds.includes(edge.source) &&
            !action.nodeIds.includes(edge.target)
        ),
      }
    }

    default: {
      return state
    }
  }
}

const dagreGraph = new Dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}))
const defaultNodeWidth = 172
const defaultNodeHeight = 36

const fitViewOptions: FitViewOptions = {
  minZoom: 0.75,
  maxZoom: 1,
}

const getId = () => uuid4()

/**
 * Taken from https://reactflow.dev/examples/layout/dagre
 * @param nodes
 * @param edges
 * @param direction
 * @returns
 */
function getLayoutedElements(
  nodes: Node[],
  edges: Edge[],
  direction = "TB"
): {
  nodes: Node[]
  edges: Edge[]
} {
  const isHorizontal = direction === "LR"
  dagreGraph.setGraph({ rankdir: direction, nodesep: 250, ranksep: 300 })

  nodes.forEach((node) => {
    dagreGraph.setNode(node.id, {
      width: node.width ?? defaultNodeWidth,
      height: node.height ?? defaultNodeHeight,
    })
  })

  edges.forEach((edge) => {
    dagreGraph.setEdge(edge.source, edge.target)
  })

  Dagre.layout(dagreGraph)

  const newNodes = nodes.map((node) => {
    const nodeWithPosition = dagreGraph.node(node.id)
    const height = node.height ?? defaultNodeHeight
    const width = node.width ?? defaultNodeWidth
    const newNode = {
      ...node,
      targetPosition: isHorizontal ? Position.Left : Position.Top,
      sourcePosition: isHorizontal ? Position.Right : Position.Bottom,
      // We are shifting the dagre node position (anchor=center center) to the top left
      // so it matches the React Flow node anchor point (top left).
      position: {
        x: nodeWithPosition.x - width / 2,
        y: nodeWithPosition.y - height / 2,
      },
    }

    return newNode
  })

  return { nodes: newNodes, edges }
}

export type NodeTypename = "udf" | "trigger" | "selector"
export type NodeType = ActionNodeType | TriggerNodeType | SelectorNodeType
export type NodeData = ActionNodeData | TriggerNodeData | SelectorNodeData

export const invincibleNodeTypes: readonly string[] = [TriggerTypename]
export const ephemeralNodeTypes: readonly string[] = [SelectorTypename]

const nodeTypes = {
  udf: actionNode,
  trigger: triggerNode,
  selector: selectorNode,
}

const defaultEdgeOptions = {
  type: "smoothstep",
  markerEnd: {
    type: MarkerType.ArrowClosed,
  },
  style: { strokeWidth: 2 },
  pathOptions: {
    borderRadius: 20,
  },
}

export function isInvincible(node: Node | Node<NodeData>): boolean {
  return invincibleNodeTypes.includes(node?.type as string)
}
export function isEphemeral(node: Node | Node<NodeData>): boolean {
  return ephemeralNodeTypes.includes(node?.type as string)
}

export interface WorkflowCanvasRef {
  centerOnNode: (nodeId: string) => void
}

export const WorkflowCanvas = React.forwardRef<
  WorkflowCanvasRef,
  React.ComponentPropsWithoutRef<typeof ReactFlow>
>((props, ref) => {
  const containerRef = useRef<HTMLDivElement>(null)
  const connectingNodeId = useRef<string | null>(null)
  const connectingHandleId = useRef<string | null>(null)
  const [graphState, dispatch] = useReducer(graphReducer, initialGraphState)
  const { nodes, edges } = graphState

  const [reactFlowInstance, setReactFlowInstance] =
    useState<ReactFlowInstance | null>(null)
  const { setViewport, getNode, screenToFlowPosition } = useReactFlow()
  const { toast } = useToast()
  const { workspaceId, workflowId, workflow, updateWorkflow } = useWorkflow()
  const [silhouettePosition, setSilhouettePosition] =
    useState<XYPosition | null>(null)
  const [isConnecting, setIsConnecting] = useState(false)
  const [pendingDeleteNodes, setPendingDeleteNodes] = useState<
    NodeRemoveChange[]
  >([])
  const [pendingDeleteEdges, setPendingDeleteEdges] = useState<EdgeChange[]>([])
  const [showDeleteDialog, setShowDeleteDialog] = useState(false)
  const { deleteAction } = useDeleteAction()

  const debouncedUpdateWorkflow = useDebouncedCallback(
    async (data: {
      object?: ReactFlowJsonObject<NodeType>
      entrypoint?: string
    }) => {
      if (workflowId && reactFlowInstance) {
        await updateWorkflow(data)
        console.log("Workflow updated (debounced)")
      }
    },
    1000, // 1 second debounce time
    { maxWait: 5000 } // Maximum 5 seconds between updates
  )

  /**
   * Load the saved workflow
   */
  useEffect(() => {
    async function initializeReactFlowInstance() {
      if (!workflow?.id || !reactFlowInstance) {
        return
      }
      try {
        const graph = workflow.object as ReactFlowJsonObject<NodeType>
        if (!graph) {
          throw new Error("No workflow data found")
        }

        dispatch({
          type: "SET_INITIAL_GRAPH",
          payload: {
            nodes: graph.nodes,
            edges: graph.edges,
            viewport: graph.viewport,
          },
        })

        setViewport({
          x: graph.viewport?.x ?? 0,
          y: graph.viewport?.y ?? 0,
          zoom: graph.viewport?.zoom ?? 1,
        })
      } catch (error) {
        console.error("Failed to fetch workflow data:", error)
        toast({
          title: "Failed to fetch workflow data",
          description: "Please try refreshing the page.",
        })
      }
    }
    initializeReactFlowInstance()
  }, [workflow?.id, reactFlowInstance]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (
      workflowId &&
      reactFlowInstance &&
      (graphState.nodes.length > 0 || graphState.edges.length > 0)
    ) {
      debouncedUpdateWorkflow({
        object: pruneGraphObject(
          reactFlowInstance
        ) as ReactFlowJsonObject<NodeType>,
      })
    }
  }, [graphState, workflowId, reactFlowInstance]) // eslint-disable-line react-hooks/exhaustive-deps

  // Connections
  const onConnect = useCallback(
    async (params: Edge | Connection) => {
      console.log("Edge connected:", params)
      if (params.source?.startsWith("trigger")) {
        params = {
          ...params,
          label: "⚡ Trigger",
        }
        // 1. Find the trigger node
        const triggerNode = nodes.find(
          (node) => node.type === "trigger"
        ) as TriggerNodeType
        // 2. Find the entrypoint node
        const entrypointNode = getNode(
          params.target! /* Target is non-null as we are in a connect callback */
        )
        if (!triggerNode || !entrypointNode) {
          throw new Error("Could not find trigger or entrypoint node")
        }

        // 3. Set the workflow entrypoint
        await updateWorkflow({ entrypoint: entrypointNode.id })
      }
      dispatch({ type: "ADD_EDGE", edge: params })
    },
    [nodes, getNode, updateWorkflow] // eslint-disable-line react-hooks/exhaustive-deps
  )

  const onConnectStart = useCallback(
    (event: MouseEvent | TouchEvent, params: OnConnectStartParams) => {
      connectingNodeId.current = params.nodeId
      connectingHandleId.current = params.handleId
      setIsConnecting(true)
    },
    []
  )

  const dropSelectorNode = useCallback(
    (event: MouseEvent | TouchEvent) => {
      const x = (event as MouseEvent).clientX - defaultNodeWidth / 2
      const y = (event as MouseEvent).clientY - defaultNodeHeight / 2
      const id = getId()
      const newNode: SelectorNodeType = {
        id,
        type: SelectorTypename,
        position: screenToFlowPosition({ x, y }),
        data: { type: "selector" },
        origin: [0.5, 0.0],
      }

      dispatch({ type: "ADD_NODE", node: newNode })
      return newNode
    },
    [screenToFlowPosition]
  ) // eslint-disable-line react-hooks/exhaustive-deps

  const onConnectEnd = useCallback(
    (event: MouseEvent | TouchEvent) => {
      event.preventDefault()
      event.stopPropagation()
      try {
        if (!connectingNodeId.current) return

        const targetIsPane = (event?.target as HTMLElement)?.classList.contains(
          "react-flow__pane"
        )

        if (targetIsPane) {
          const newNode = dropSelectorNode(event)
          const id = newNode.id

          const edge = {
            id,
            source: connectingNodeId.current,
            target: id,
            ...(connectingHandleId.current && {
              sourceHandle: connectingHandleId.current,
            }),
          } as Edge

          dispatch({ type: "ADD_EDGE", edge })
        }
      } finally {
        console.log("Cleaning up connect end")
        connectingHandleId.current = null
        setSilhouettePosition(null)
        setIsConnecting(false)
      }
    },
    [dropSelectorNode] // eslint-disable-line react-hooks/exhaustive-deps
  )

  const onPaneMouseMove = useCallback(
    (event: React.MouseEvent) => {
      if (!isConnecting || !containerRef.current) return

      const bounds = containerRef.current.getBoundingClientRect()
      const x = event.clientX - bounds.left
      const y = event.clientY - bounds.top

      setSilhouettePosition({ x, y })
    },
    [isConnecting]
  )

  // Drag and drop
  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault()
    event.dataTransfer.dropEffect = "move"
  }, [])

  useEffect(() => {
    setShowDeleteDialog(pendingDeleteNodes.length > 0)
  }, [pendingDeleteNodes])

  // Handle confirmed deletion
  const handleConfirmedDeletion = useCallback(async () => {
    if (!workflowId || !reactFlowInstance) return
    console.log("HANDLE CONFIRMED DELETION", {
      pendingDeleteNodes,
      pendingDeleteEdges,
    })

    try {
      await Promise.all(
        pendingDeleteNodes.map((node) =>
          deleteAction({ actionId: node.id, workspaceId })
        )
      )

      // Dispatch deletion action to the reducer
      const nodeIds = pendingDeleteNodes.map((node) => node.id)
      dispatch({ type: "CONFIRMED_DELETION", nodeIds })

      // Update workflow with the latest graph state
      debouncedUpdateWorkflow({
        object: pruneGraphObject(
          reactFlowInstance
        ) as ReactFlowJsonObject<NodeType>,
      })

      console.log("Workflow updated successfully")
    } catch (error) {
      console.error("An error occurred while deleting Action nodes:", error)
      toast({
        title: "Failed to delete nodes",
        description:
          "Could not delete nodes. Please check the console logs for more information.",
      })
    } finally {
      setShowDeleteDialog(false)
      setPendingDeleteNodes([])
      setPendingDeleteEdges([])
    }
  }, [
    pendingDeleteNodes,
    pendingDeleteEdges,
    workflowId,
    reactFlowInstance,
    workspaceId,
    debouncedUpdateWorkflow,
    toast,
  ])

  // Handle edge changes
  const handleEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      const pendingDeletes: EdgeRemoveChange[] = []
      const nextChanges = changes.reduce((acc, change) => {
        if (change.type === "remove") {
          // Add pending deletes
          const edge = reactFlowInstance?.getEdge(change.id)
          if (!edge) {
            console.warn("Couldn't load edge, skipping")
            return acc
          }
          // Only delete the edge if it was selected
          if (edge.selected) {
            return [...acc, change]
          }
          // Intercept the edge removal
          return acc
        }
        return [...acc, change]
      }, [] as EdgeChange[])
      if (pendingDeletes.length > 0) {
        console.log("Pending delete edges:", pendingDeletes)
        setPendingDeleteEdges(pendingDeletes)
      }

      // Dispatch changes to the reducer
      if (nextChanges.length > 0) {
        dispatch({ type: "EDGES_CHANGE", changes: nextChanges })
      }
    },
    [reactFlowInstance]
  )

  // Handle node changes
  const handleNodesChange = useCallback(
    (changes: NodeChange[]) => {
      const pendingDeletes: NodeRemoveChange[] = []
      const nextChanges = changes.reduce((acc, change) => {
        // if this change is supposed to remove a node we want to validate it first
        if (change.type === "remove") {
          const node = getNode(change.id)
          if (!node) {
            console.warn("Couldn't load node, skipping")
            return acc
          }

          if (isInvincible(node)) {
            // Node is invincible, skip deletion
            return acc
          }
          if (isEphemeral(node)) {
            // Node is ephemeral, apply deletion
            return [...acc, change]
          }
          // All other nodes should be deleted
          pendingDeletes.push(change)
          return acc
        }

        // all other change types are just put into the next changes array
        return [...acc, change]
      }, [] as NodeChange[])

      if (pendingDeletes.length > 0) {
        console.log("Pending delete nodes:", pendingDeletes)
        setPendingDeleteNodes(pendingDeletes)
      }

      // Apply changes to the reducer
      if (nextChanges.length > 0) {
        dispatch({ type: "NODES_CHANGE", changes: nextChanges })
      }
    },
    [getNode]
  )

  const onLayout = useCallback(
    (direction: "TB" | "LR") => {
      const { nodes: newNodes, edges: newEdges } = getLayoutedElements(
        nodes,
        edges,
        direction
      )

      dispatch({
        type: "SET_NODES_AND_EDGES",
        nodes: newNodes,
        edges: newEdges,
      })
    },
    [nodes, edges] // eslint-disable-line react-hooks/exhaustive-deps
  )

  // Handle viewport changes
  const onViewportChange = useCallback(
    (event: any) => {
      if (reactFlowInstance) {
        const { x, y, zoom } = reactFlowInstance.getViewport()
        dispatch({ type: "VIEWPORT_CHANGE", viewport: { x, y, zoom } })
      }
    },
    [reactFlowInstance]
  )

  // Add this function to center on a node
  const centerOnNode = useCallback(
    (nodeId: string) => {
      if (!reactFlowInstance) return

      const node = reactFlowInstance.getNode(nodeId)
      console.log("center on node", node)
      if (!node) return

      // Get the node's position and dimensions
      const x = node.position.x + (node.width ?? defaultNodeWidth) / 2
      const y = node.position.y + (node.height ?? defaultNodeHeight) / 2

      // Animate to the node's center position
      reactFlowInstance.setCenter(x, y, { duration: 800 })
    },
    [reactFlowInstance]
  )

  // Export the centerOnNode function through useImperativeHandle
  useImperativeHandle(
    ref,
    () => ({
      centerOnNode,
    }),
    [centerOnNode]
  )

  // Right click context menu
  const onPaneContextMenu = useCallback(
    (event: React.MouseEvent | MouseEvent) => {
      event.preventDefault()
      if (!reactFlowInstance) return
      // For React.MouseEvent, use nativeEvent, for MouseEvent use the event directly
      const nativeEvent = "nativeEvent" in event ? event.nativeEvent : event
      dropSelectorNode(nativeEvent)
    },
    [reactFlowInstance, dropSelectorNode]
  )

  return (
    <div ref={containerRef} style={{ height: "100%", width: "100%" }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onConnect={onConnect}
        onConnectStart={onConnectStart}
        onConnectEnd={onConnectEnd}
        onPaneMouseMove={onPaneMouseMove}
        onDragOver={onDragOver}
        onInit={setReactFlowInstance}
        onNodesChange={handleNodesChange}
        onEdgesChange={handleEdgesChange}
        onNodeDragStop={debouncedUpdateWorkflow.flush}
        onMove={onViewportChange}
        defaultEdgeOptions={defaultEdgeOptions}
        nodeTypes={nodeTypes}
        proOptions={{ hideAttribution: true }}
        deleteKeyCode={["Backspace", "Delete"]}
        fitView
        fitViewOptions={fitViewOptions}
        nodeDragThreshold={4}
        maxZoom={1}
        minZoom={0.25}
        panOnScroll
        connectionLineType={ConnectionLineType.SmoothStep}
        onPaneContextMenu={onPaneContextMenu}
      >
        <Background bgColor="#fcfcfc" />
        <Controls className="rounded-sm" fitViewOptions={fitViewOptions} />
        <Panel position="bottom-right" className="flex items-center gap-1">
          <Badge
            variant="outline"
            className="select-none bg-background text-xs font-normal hover:cursor-default"
          >
            Layout
          </Badge>
          <Button
            variant="outline"
            className="m-0 size-6 p-0 text-xs"
            onClick={() => onLayout("TB")}
          >
            <MoveVerticalIcon className="size-3" strokeWidth={2} />
          </Button>
          <Button
            variant="outline"
            className="m-0 hidden size-6 p-0 text-xs"
            onClick={() => onLayout("LR")}
            disabled
          >
            <MoveHorizontalIcon className="size-3" strokeWidth={2} />
          </Button>
        </Panel>
        <NodeSilhouette
          position={silhouettePosition}
          isConnecting={isConnecting}
        />
        <DeleteActionNodeDialog
          open={showDeleteDialog}
          onOpenChange={(open) => {
            if (!open) {
              setPendingDeleteNodes([])
              setPendingDeleteEdges([])
            }
          }}
          onConfirm={handleConfirmedDeletion}
        />
      </ReactFlow>
    </div>
  )
})

WorkflowCanvas.displayName = "WorkflowCanvas"

function NodeSilhouette({
  position,
  isConnecting,
}: {
  position: XYPosition | null
  isConnecting: boolean
}) {
  return (
    isConnecting &&
    position && (
      <div
        className="pointer-events-none absolute flex min-h-24 min-w-72 items-center justify-center rounded-md border border-emerald-500 bg-emerald-500/30 opacity-50"
        style={{
          left: position.x,
          top: position.y,
          transform: "translate(-50%, 20%)",
        }}
      >
        <PlusIcon className="size-6 text-muted-foreground/70" />
      </div>
    )
  )
}
