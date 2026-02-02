import {
  Background,
  type Connection,
  ConnectionLineType,
  Controls,
  type Edge,
  type EdgeChange,
  type FitViewOptions,
  MarkerType,
  type Node,
  type NodeChange,
  type NodeRemoveChange,
  type OnConnectStartParams,
  Panel,
  Position,
  ReactFlow,
  type ReactFlowInstance,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Viewport,
  type XYPosition,
} from "@xyflow/react"
import React, {
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react"
import { v4 as uuid4 } from "uuid"

import "@xyflow/react/dist/style.css"

import Dagre from "@dagrejs/dagre"
import { MoveHorizontalIcon, MoveVerticalIcon, PlusIcon } from "lucide-react"
import type {
  GraphOperation,
  GraphResponse,
  RegistryActionReadMinimal,
} from "@/client"
import actionNode, {
  type ActionNodeData,
  type ActionNodeType,
} from "@/components/builder/canvas/action-node"
import { CanvasToolbar } from "@/components/builder/canvas/canvas-toolbar"
import { DeleteActionNodeDialog } from "@/components/builder/canvas/delete-node-dialog"
import selectorNode, {
  type SelectorNodeData,
  type SelectorNodeType,
  SelectorTypename,
} from "@/components/builder/canvas/selector-node"
import triggerNode, {
  type TriggerNodeData,
  type TriggerNodeType,
  TriggerTypename,
} from "@/components/builder/canvas/trigger-node"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { useToast } from "@/components/ui/use-toast"
import { useGraph, useGraphOperations } from "@/lib/hooks"
import { pruneGraphObject } from "@/lib/workflow"
import { useWorkflow } from "@/providers/workflow"

const dagreGraph = new Dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}))
const defaultNodeWidth = 172
const defaultNodeHeight = 36
const triggerNodeAutoLayoutGap = 64

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

  if (!isHorizontal) {
    const triggerNode = newNodes.find((node) => node.type === TriggerTypename)
    if (triggerNode) {
      const triggerY = triggerNode.position.y
      const adjustedNodes = newNodes.map((node) => {
        if (node.id === triggerNode.id) {
          return node
        }
        if (node.position.y <= triggerY) {
          return node
        }
        return {
          ...node,
          position: {
            ...node.position,
            y: node.position.y + triggerNodeAutoLayoutGap,
          },
        }
      })

      return { nodes: adjustedNodes, edges }
    }
  }

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
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])
  const [reactFlowInstance, setReactFlowInstance] =
    useState<ReactFlowInstance | null>(null)
  const { setViewport, getNode, screenToFlowPosition } = useReactFlow()
  const { toast } = useToast()
  const { workspaceId, workflowId } = useWorkflow()
  const { data: graphData } = useGraph(workspaceId, workflowId ?? "")
  const { applyGraphOperations, refetchGraph } = useGraphOperations(
    workspaceId,
    workflowId ?? ""
  )
  const [graphVersion, setGraphVersion] = useState<number>(1)
  const [silhouettePosition, setSilhouettePosition] =
    useState<XYPosition | null>(null)
  const [isConnecting, setIsConnecting] = useState(false)
  const [pendingDeleteNodes, setPendingDeleteNodes] = useState<
    NodeRemoveChange[]
  >([])
  const [showDeleteDialog, setShowDeleteDialog] = useState(false)
  const openContextMenuId = useRef<string | null>(null)

  /**
   * Convert graph response to React Flow nodes and edges.
   */
  const buildNodesAndEdgesFromGraph = useCallback(
    (graph: GraphResponse): { nodes: Node[]; edges: Edge[] } => {
      const rfNodes: Node[] = graph.nodes.map((node) => {
        const nodeData = node as {
          id: string
          type: string
          position?: { x: number; y: number }
          data?: Record<string, unknown>
        }
        return {
          id: nodeData.id,
          type: nodeData.type === "trigger" ? TriggerTypename : "udf",
          position: nodeData.position ?? { x: 0, y: 0 },
          data: nodeData.data ?? {},
        } as Node
      })

      const rfEdges: Edge[] = graph.edges.map((edge) => {
        const edgeData = edge as {
          id?: string
          source: string
          target: string
          sourceHandle?: string
          label?: string
        }
        return {
          id:
            edgeData.id ??
            `reactflow__edge-${edgeData.source}-${edgeData.target}`,
          source: edgeData.source,
          target: edgeData.target,
          sourceHandle: edgeData.sourceHandle ?? undefined,
          label: edgeData.label === "Trigger" ? undefined : edgeData.label,
        }
      })

      return { nodes: rfNodes, edges: rfEdges }
    },
    []
  )

  /**
   * Update canvas state from graph response (used after graph operations).
   */
  const updateStateFromGraph = useCallback(
    (graph: GraphResponse) => {
      setGraphVersion(graph.version)
      const { nodes: rfNodes, edges: rfEdges } =
        buildNodesAndEdgesFromGraph(graph)
      setNodes((nodes) => {
        const selectedNodeIds = new Set(
          nodes.filter((node) => node.selected).map((node) => node.id)
        )
        return rfNodes.map((node) => ({
          ...node,
          selected: selectedNodeIds.has(node.id),
        }))
      })
      setEdges(rfEdges)
    },
    [buildNodesAndEdgesFromGraph, setNodes, setEdges]
  )

  /**
   * Load graph data when it becomes available.
   */
  useEffect(() => {
    if (!graphData || !reactFlowInstance) {
      return
    }

    try {
      // Build nodes and edges from graph API response
      const { nodes: graphNodes, edges: graphEdges } =
        buildNodesAndEdgesFromGraph(graphData)
      setNodes((nodes) => {
        const selectedNodeIds = new Set(
          nodes.filter((node) => node.selected).map((node) => node.id)
        )
        return graphNodes.map((node) => ({
          ...node,
          selected: selectedNodeIds.has(node.id),
        }))
      })
      setEdges(graphEdges)
      setGraphVersion(graphData.version)

      // Set viewport from graph if available
      const viewport = graphData.viewport as
        | { x?: number; y?: number; zoom?: number }
        | undefined
      setViewport({
        x: viewport?.x ?? 0,
        y: viewport?.y ?? 0,
        zoom: viewport?.zoom ?? 1,
      })
    } catch (error) {
      console.error("Failed to initialize workflow graph:", error)
    }
  }, [
    graphData,
    reactFlowInstance,
    buildNodesAndEdgesFromGraph,
    setNodes,
    setEdges,
    setViewport,
  ])

  // Connections
  const onConnect = useCallback(
    async (params: Edge | Connection) => {
      console.log("Edge connected:", params)

      const targetId = params.target
      const sourceId = params.source
      if (!targetId || !sourceId) return

      // Use graph operations for all edges (trigger and action)
      const isTrigger = sourceId.startsWith("trigger")
      try {
        const operation: GraphOperation = {
          type: "add_edge",
          payload: {
            source_id: sourceId,
            source_type: isTrigger ? "trigger" : "udf",
            target_id: targetId,
            source_handle: isTrigger
              ? undefined
              : ((params.sourceHandle as "success" | "error") ?? "success"),
          },
        }

        const result = await applyGraphOperations({
          baseVersion: graphVersion,
          operations: [operation],
        })

        // Update state from the response
        updateStateFromGraph(result)
      } catch (error) {
        // Handle 409 conflict by refetching and retrying
        const apiError = error as { status?: number }
        if (apiError.status === 409) {
          console.log("Version conflict, refetching graph...")
          const latestGraph = await refetchGraph()
          setGraphVersion(latestGraph.version)
          // Retry with the latest version
          const operation: GraphOperation = {
            type: "add_edge",
            payload: {
              source_id: sourceId,
              source_type: isTrigger ? "trigger" : "udf",
              target_id: targetId,
              source_handle: isTrigger
                ? undefined
                : ((params.sourceHandle as "success" | "error") ?? "success"),
            },
          }
          const result = await applyGraphOperations({
            baseVersion: latestGraph.version,
            operations: [operation],
          })
          updateStateFromGraph(result)
        } else {
          console.error("Failed to add edge:", error)
          toast({
            title: "Failed to connect nodes",
            description: "Could not create the connection. Please try again.",
          })
        }
      }
    },
    [
      graphVersion,
      applyGraphOperations,
      updateStateFromGraph,
      refetchGraph,
      toast,
    ]
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
      const prevContextMenuId = openContextMenuId.current
      setNodes((nds) =>
        nds.concat(newNode).filter((n) => n.id !== prevContextMenuId)
      )
      openContextMenuId.current = id
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
          setEdges((eds) => [...eds, edge])
        }
      } finally {
        console.log("Cleaning up connect end")
        connectingHandleId.current = null
        setSilhouettePosition(null)
        setIsConnecting(false)
      }
    },
    [screenToFlowPosition] // eslint-disable-line react-hooks/exhaustive-deps
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
    if (!workflowId) return
    console.log("HANDLE CONFIRMED DELETION", {
      pendingDeleteNodes,
    })

    try {
      const deleteOperations: GraphOperation[] = pendingDeleteNodes.map(
        (node) => ({
          type: "delete_node",
          payload: { action_id: node.id },
        })
      )

      const result = await applyGraphOperations({
        baseVersion: graphVersion,
        operations: deleteOperations,
      })

      updateStateFromGraph(result)
      console.log("Actions deleted successfully")
    } catch (error) {
      const apiError = error as { status?: number }
      if (apiError.status === 409) {
        console.log("Version conflict on delete, refetching and retrying...")
        try {
          const latestGraph = await refetchGraph()
          const deleteOperations: GraphOperation[] = pendingDeleteNodes.map(
            (node) => ({
              type: "delete_node",
              payload: { action_id: node.id },
            })
          )
          const retryResult = await applyGraphOperations({
            baseVersion: latestGraph.version,
            operations: deleteOperations,
          })
          updateStateFromGraph(retryResult)
        } catch (retryError) {
          console.error("Failed to delete nodes after retry:", retryError)
        }
      } else {
        console.error("An error occurred while deleting Action nodes:", error)
        toast({
          title: "Failed to delete nodes",
          description:
            "Could not delete nodes. Please check the console logs for more information.",
        })
      }
    } finally {
      setShowDeleteDialog(false)
      setPendingDeleteNodes([])
    }
  }, [
    pendingDeleteNodes,
    workflowId,
    workspaceId,
    toast,
    applyGraphOperations,
    graphVersion,
    updateStateFromGraph,
    refetchGraph,
  ])

  const handleEdgesChange = useCallback(
    async (changes: EdgeChange[]) => {
      const edgesToRemove: Edge[] = []
      const nextChanges = changes.reduce((acc, change) => {
        if (change.type === "remove") {
          const edge = reactFlowInstance?.getEdge(change.id)
          if (!edge) {
            console.warn("Couldn't load edge, skipping")
            return acc
          }
          // Only delete the edge if it was selected
          if (edge.selected) {
            edgesToRemove.push(edge)
            return [...acc, change]
          }
          // Intercept the edge removal
          return acc
        }
        return [...acc, change]
      }, [] as EdgeChange[])

      // Persist edge deletions to backend via graph operations
      const deleteOperations: GraphOperation[] = edgesToRemove.map((edge) => {
        const isTrigger = edge.source.startsWith("trigger")
        return {
          type: "delete_edge" as const,
          payload: {
            source_id: edge.source,
            source_type: isTrigger ? "trigger" : "udf",
            target_id: edge.target,
            source_handle: isTrigger
              ? undefined
              : ((edge.sourceHandle as "success" | "error") ?? "success"),
          },
        }
      })

      if (deleteOperations.length > 0) {
        try {
          const result = await applyGraphOperations({
            baseVersion: graphVersion,
            operations: deleteOperations,
          })
          updateStateFromGraph(result)
        } catch (error) {
          const apiError = error as { status?: number }
          if (apiError.status === 409) {
            console.log(
              "Version conflict on edge deletion, refetching and retrying..."
            )
            try {
              const latestGraph = await refetchGraph()
              // Retry with the latest version
              const retryResult = await applyGraphOperations({
                baseVersion: latestGraph.version,
                operations: deleteOperations,
              })
              updateStateFromGraph(retryResult)
            } catch (retryError) {
              console.error("Failed to delete edges after retry:", retryError)
            }
          } else {
            console.error("Failed to persist edge deletion:", error)
          }
        }
      } else {
        // Just apply local changes for non-persistent changes
        onEdgesChange(nextChanges)
      }
    },
    [
      reactFlowInstance,
      onEdgesChange,
      graphVersion,
      applyGraphOperations,
      updateStateFromGraph,
      refetchGraph,
    ]
  )
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
      // apply the changes we kept
      onNodesChange(nextChanges)
    },
    [nodes, setNodes, pendingDeleteNodes]
  )

  /**
   * Save node positions using graph operations.
   */
  const saveNodePositions = useCallback(
    async (nodesToSave: Node[]) => {
      const triggerNode = nodesToSave.find((n) => n.type === "trigger")
      const actionNodes = nodesToSave.filter(
        (n) => n.type === "udf" && !isEphemeral(n)
      )

      const operations: GraphOperation[] = []

      // Add move_nodes operation for action nodes
      if (actionNodes.length > 0) {
        operations.push({
          type: "move_nodes",
          payload: {
            positions: actionNodes.map((node) => ({
              action_id: node.id,
              x: node.position.x,
              y: node.position.y,
            })),
          },
        })
      }

      // Add update_trigger_position operation
      if (triggerNode) {
        operations.push({
          type: "update_trigger_position",
          payload: {
            x: triggerNode.position.x,
            y: triggerNode.position.y,
          },
        })
      }

      if (operations.length === 0) return

      try {
        const result = await applyGraphOperations({
          baseVersion: graphVersion,
          operations,
        })
        // Only update version, not the full state (positions are already correct locally)
        setGraphVersion(result.version)
      } catch (error) {
        const apiError = error as { status?: number }
        if (apiError.status === 409) {
          console.log(
            "Version conflict on position save, refetching graph and retrying..."
          )
          try {
            const latestGraph = await refetchGraph()
            // Retry with the latest version
            const retryResult = await applyGraphOperations({
              baseVersion: latestGraph.version,
              operations,
            })
            setGraphVersion(retryResult.version)
          } catch (retryError) {
            console.error("Failed to save positions after retry:", retryError)
          }
        } else {
          console.error("Failed to save positions:", error)
        }
      }
    },
    [graphVersion, applyGraphOperations, refetchGraph]
  )

  const onLayout = useCallback(
    (direction: "TB" | "LR") => {
      const prunedGraph = pruneGraphObject({
        nodes,
        edges,
      })
      const { nodes: newNodes, edges: newEdges } = getLayoutedElements(
        prunedGraph.nodes,
        prunedGraph.edges,
        direction
      )
      setNodes(newNodes)
      setEdges(newEdges)

      // Save positions after layout
      if (workflowId) {
        saveNodePositions(newNodes)
      }
    },
    [nodes, edges, workflowId, setNodes, setEdges, saveNodePositions]
  )

  // Batch update positions when nodes are dragged
  const onNodesDragStop = useCallback(() => {
    if (!workflowId || !reactFlowInstance) return

    const currentNodes = reactFlowInstance.getNodes()
    saveNodePositions(currentNodes)
  }, [workflowId, reactFlowInstance, saveNodePositions])

  // Save viewport when panning/zooming stops
  const onMoveEnd = useCallback(
    async (_event: unknown, viewport: Viewport) => {
      if (!workflowId) return

      const operation: GraphOperation = {
        type: "update_viewport",
        payload: {
          x: viewport.x,
          y: viewport.y,
          zoom: viewport.zoom,
        },
      }

      try {
        const result = await applyGraphOperations({
          baseVersion: graphVersion,
          operations: [operation],
        })
        // Only update version (viewport is already correct locally)
        setGraphVersion(result.version)
      } catch (error) {
        const apiError = error as { status?: number }
        if (apiError.status === 409) {
          console.log(
            "Version conflict on viewport save, refetching and retrying..."
          )
          try {
            const latestGraph = await refetchGraph()
            // Retry with the latest version
            const retryResult = await applyGraphOperations({
              baseVersion: latestGraph.version,
              operations: [operation],
            })
            setGraphVersion(retryResult.version)
          } catch (retryError) {
            console.error("Failed to save viewport after retry:", retryError)
          }
        } else {
          console.error("Failed to save viewport:", error)
        }
      }
    },
    [workflowId, graphVersion, applyGraphOperations, refetchGraph]
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
    [reactFlowInstance] // eslint-disable-line react-hooks/exhaustive-deps
  )

  // Handle adding action from toolbar
  const handleToolbarAddAction = useCallback(
    async (action: RegistryActionReadMinimal) => {
      if (!workflowId || !reactFlowInstance) return

      // Get the center of the current viewport in screen coordinates
      const containerBounds = containerRef.current?.getBoundingClientRect()
      if (!containerBounds) return

      // screenToFlowPosition expects actual screen coordinates (clientX/clientY)
      // so we need to add the container's position to get the center in screen space
      const screenCenterX = containerBounds.left + containerBounds.width / 2
      const screenCenterY = containerBounds.top + containerBounds.height / 2

      // Convert screen position to flow position
      const position = screenToFlowPosition({
        x: screenCenterX,
        y: screenCenterY,
      })

      try {
        const addNodeOp: GraphOperation = {
          type: "add_node",
          payload: {
            type: action.action,
            title: action.default_title ?? action.action,
            position_x: position.x,
            position_y: position.y,
          },
        }

        const result = await applyGraphOperations({
          baseVersion: graphVersion,
          operations: [addNodeOp],
        })

        updateStateFromGraph(result)
        toast({
          title: "Action added",
          description: `Added "${action.default_title ?? action.action}" to the workflow.`,
        })
      } catch (error) {
        const apiError = error as { status?: number }
        if (apiError.status === 409) {
          try {
            const latestGraph = await refetchGraph()
            const addNodeOp: GraphOperation = {
              type: "add_node",
              payload: {
                type: action.action,
                title: action.default_title ?? action.action,
                position_x: position.x,
                position_y: position.y,
              },
            }
            const result = await applyGraphOperations({
              baseVersion: latestGraph.version,
              operations: [addNodeOp],
            })
            updateStateFromGraph(result)
          } catch (retryError) {
            console.error("Failed to add action after retry:", retryError)
            toast({
              title: "Failed to add action",
              description: "Could not add action. Please try again.",
            })
          }
        } else {
          console.error("Failed to add action:", error)
          toast({
            title: "Failed to add action",
            description: "Could not add action. Please try again.",
          })
        }
      }
    },
    [
      workflowId,
      reactFlowInstance,
      screenToFlowPosition,
      graphVersion,
      applyGraphOperations,
      updateStateFromGraph,
      refetchGraph,
      toast,
    ]
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
        onNodeDragStop={onNodesDragStop}
        onMoveEnd={onMoveEnd}
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
        <Panel position="bottom-center" className="mb-4">
          <CanvasToolbar onAddAction={handleToolbarAddAction} />
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
