import React, {
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
  type TouchEvent as ReactTouchEvent,
} from "react"
import ReactFlow, {
  addEdge,
  Background,
  Connection,
  ConnectionLineType,
  Controls,
  Edge,
  FitViewOptions,
  MarkerType,
  NodeChange,
  OnConnectStartParams,
  Panel,
  Position,
  ReactFlowInstance,
  useEdgesState,
  useNodesState,
  useReactFlow,
  XYPosition,
  type Node,
  type ReactFlowJsonObject,
} from "reactflow"
import { v4 as uuid4 } from "uuid"

import "reactflow/dist/style.css"

import { actionsDeleteAction } from "@/client"
import { useWorkflow } from "@/providers/workflow"
import Dagre from "@dagrejs/dagre"
import { MoveHorizontalIcon, MoveVerticalIcon, PlusIcon } from "lucide-react"

import { pruneGraphObject } from "@/lib/workflow"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { useToast } from "@/components/ui/use-toast"
import actionNode, {
  ActionNodeData,
  ActionNodeType,
} from "@/components/workbench/canvas/action-node"
import selectorNode, {
  SelectorNodeType,
  SelectorTypename,
} from "@/components/workbench/canvas/selector-node"
import triggerNode, {
  TriggerNodeData,
  TriggerNodeType,
  TriggerTypename,
} from "@/components/workbench/canvas/trigger-node"

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
  dagreGraph.setGraph({ rankdir: direction, nodesep: 100, ranksep: 150 })

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

export type NodeTypename = "udf" | "trigger"
export type NodeType = ActionNodeType | TriggerNodeType | SelectorNodeType
export type NodeData = ActionNodeData | TriggerNodeData

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

export function isInvincible<T>(node: Node<T>): boolean {
  return invincibleNodeTypes.includes(node?.type as string)
}
export function isEphemeral<T>(node: Node<T>): boolean {
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
  const [nodes, setNodes, onNodesChange] = useNodesState([])
  const [edges, setEdges, onEdgesChange] = useEdgesState([])
  const [reactFlowInstance, setReactFlowInstance] =
    useState<ReactFlowInstance | null>(null)
  const { setViewport, getNode, screenToFlowPosition } = useReactFlow()
  const { toast } = useToast()
  const { workspaceId, workflowId, workflow, updateWorkflow } = useWorkflow()
  const [silhouettePosition, setSilhouettePosition] =
    useState<XYPosition | null>(null)
  const [isConnecting, setIsConnecting] = useState(false)

  /**
   * Load the saved workflow
   */
  useEffect(() => {
    async function initializeReactFlowInstance() {
      if (!workflow?.id || !reactFlowInstance) {
        return
      }
      try {
        const graph = workflow.object as ReactFlowJsonObject
        if (!graph) {
          throw new Error("No workflow data found")
        }
        const { nodes: layoutNodes, edges: layoutEdges } = getLayoutedElements(
          graph.nodes,
          graph.edges,
          "TB"
        )
        setNodes(layoutNodes)
        setEdges(layoutEdges)
        setViewport({
          x: graph.viewport.x,
          y: graph.viewport.y,
          zoom: graph.viewport.zoom,
        })
      } catch (error) {
        console.error("Failed to fetch workflow data:", error)
      }
    }
    initializeReactFlowInstance()
  }, [workflow?.id, reactFlowInstance]) // eslint-disable-line react-hooks/exhaustive-deps

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
      setEdges((eds) => addEdge(params, eds))
    },
    [edges, setEdges, getNode, setNodes] // eslint-disable-line react-hooks/exhaustive-deps
  )

  const onConnectStart = useCallback(
    (
      event: ReactMouseEvent | ReactTouchEvent,
      params: OnConnectStartParams
    ) => {
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
      const newNode = {
        id,
        type: SelectorTypename,
        position: screenToFlowPosition({ x, y }),
        data: {},
        origin: [0.5, 0.0],
      } as Node

      setNodes((nds) => nds.concat(newNode))
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
          setEdges((eds) => eds.concat(edge))
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
    (event: ReactMouseEvent) => {
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

  const onNodesDelete = async <T,>(nodesToDelete: Node<T>[]) => {
    if (!workflowId || !reactFlowInstance) {
      return
    }
    const filteredNodes = nodesToDelete.filter((node) => !isInvincible(node))
    if (filteredNodes.length === 0) {
      toast({
        title: "Invalid action",
        description: "Cannot delete invincible node",
      })
      return
    }
    try {
      await Promise.all(
        filteredNodes.map((node) =>
          actionsDeleteAction({ actionId: node.id, workspaceId })
        )
      )
      setNodes((nds) =>
        nds.filter((n) => !nodesToDelete.map((nd) => nd.id).includes(n.id))
      )
      await updateWorkflow({
        object: pruneGraphObject(reactFlowInstance),
      })
      console.log("Nodes deleted successfully")
    } catch (error) {
      console.error("An error occurred while deleting Action nodes:", error)
      toast({
        title: "Failed to delete nodes",
        description:
          "Could not delete nodes. Please check the console logs for more information.",
      })
    }
  }

  const onEdgesDelete = useCallback(
    async (edgesToDelete: Edge[]) => {
      edgesToDelete.forEach(async (params: Edge) => {
        if (params.source?.startsWith("trigger")) {
          // 1. Find the trigger node
          const triggerNode = nodes.find(
            (node) => node.type === "trigger"
          ) as TriggerNodeType
          // 2. Find the entrypoint node
          const entrypointNode = getNode(
            params.target! /* Target is non-null as we are in a connect callback */
          )
          if (!triggerNode || !entrypointNode) {
            console.warn("Could not find trigger or entrypoint node")
            // Delete the edge anyways
            setEdges((eds) => eds.filter((ed) => ed.id !== params.id))
          }

          // 3. Update the trigger node UI state with the entrypoint id
          // We'll persist this through the trigger panel
          await updateWorkflow({ entrypoint: null })
        }
      })
      setEdges((eds) =>
        eds.filter((e) => !edgesToDelete.map((ed) => ed.id).includes(e.id))
      )
    },
    [edges, setEdges, getNode, setNodes] // eslint-disable-line react-hooks/exhaustive-deps
  )

  const handleNodesChange = useCallback(
    (changes: NodeChange[]) => {
      const nextChanges = changes.reduce((acc, change) => {
        // if this change is supposed to remove a node we want to validate it first
        if (change.type === "remove") {
          const node = getNode(change.id)

          // if the node can be removed, keep the change, otherwise we skip the change and keep the node
          if (node && !isInvincible(node)) {
            console.log("Node is not invincible, removing it")
            return [...acc, change]
          }

          // change is skipped, node is kept
          console.log("Node is invincible, keeping it")
          return acc
        }

        // all other change types are just put into the next changes arr
        return [...acc, change]
      }, [] as NodeChange[])

      // apply the changes we kept
      onNodesChange(nextChanges)
    },
    [nodes, setNodes] // eslint-disable-line react-hooks/exhaustive-deps
  )

  const onLayout = useCallback(
    (direction: "TB" | "LR") => {
      const { nodes: newNodes, edges: newEdges } = getLayoutedElements(
        nodes,
        edges,
        direction
      )
      setNodes(newNodes)
      setEdges(newEdges)
    },
    [nodes, edges] // eslint-disable-line react-hooks/exhaustive-deps
  )

  // Saving react flow instance state
  useEffect(() => {
    if (workflowId && reactFlowInstance) {
      updateWorkflow({ object: pruneGraphObject(reactFlowInstance) })
    }
  }, [edges])

  const onNodesDragStop = () => {
    if (workflowId && reactFlowInstance) {
      updateWorkflow({ object: pruneGraphObject(reactFlowInstance) })
    }
  }

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
    (event: ReactMouseEvent | ReactTouchEvent) => {
      event.preventDefault()
      if (!reactFlowInstance) return
      dropSelectorNode(event.nativeEvent)
    },
    [reactFlowInstance] // eslint-disable-line react-hooks/exhaustive-deps
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
        onEdgesChange={onEdgesChange}
        onEdgesDelete={onEdgesDelete}
        onInit={setReactFlowInstance}
        onNodesChange={handleNodesChange}
        onNodesDelete={onNodesDelete}
        onNodeDragStop={onNodesDragStop}
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
        // onContextMenu={onPaneContextMenu}
        onPaneContextMenu={onPaneContextMenu}
      >
        <Background />
        <Controls className="rounded-sm" fitViewOptions={fitViewOptions} />
        <Panel position="bottom-right" className="flex items-center gap-1">
          <Badge
            variant="outline"
            className="select-none bg-background text-xs font-extralight hover:cursor-default"
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
