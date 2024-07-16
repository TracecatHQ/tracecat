import React, { useCallback, useEffect, useRef, useState } from "react"
import ReactFlow, {
  addEdge,
  Background,
  Connection,
  Controls,
  Edge,
  MarkerType,
  NodeChange,
  Panel,
  ReactFlowInstance,
  useEdgesState,
  useNodesState,
  useReactFlow,
  XYPosition,
  type Node,
  type ReactFlowJsonObject,
} from "reactflow"

import "reactflow/dist/style.css"

import { useParams } from "next/navigation"
import { useWorkflow } from "@/providers/workflow"
import Dagre, { type GraphLabel, type Label } from "@dagrejs/dagre"
import { MoveHorizontalIcon, MoveVerticalIcon } from "lucide-react"

import {
  createAction,
  deleteAction,
  fetchWorkflow,
  updateWorkflowGraphObject,
} from "@/lib/workflow"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { useToast } from "@/components/ui/use-toast"
import triggerNode, {
  TriggerNodeData,
  TriggerNodeType,
  TriggerTypename,
} from "@/components/workspace/canvas/trigger-node"
import udfNode, {
  UDFNodeData,
  UDFNodeType,
} from "@/components/workspace/canvas/udf-node"

const g = new Dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}))

/**
 * Taken from https://reactflow.dev/learn/layouting/layouting#dagre
 * @param nodes
 * @param edges
 * @param options
 * @returns
 */
const getLayoutedElements = (
  nodes: Node[],
  edges: Edge[],
  options: GraphLabel
) => {
  g.setGraph({ ...options, ranksep: 75, nodesep: 100 })

  edges.forEach((edge) => g.setEdge(edge.source, edge.target))
  nodes.forEach((node) => g.setNode(node.id, node as Label))

  Dagre.layout(g)
  console.log(nodes)

  return {
    nodes: nodes.map((node) => {
      const position = g.node(node.id)
      // We are shifting the dagre node position (anchor=center center) to the top left
      // so it matches the React Flow node anchor point (top left).
      const x = position.x - node.width! / 2
      const y = position.y - node.height! / 2

      return { ...node, position: { x, y } }
    }),
    edges,
  }
}

export type NodeTypename = "udf" | "trigger"
export type NodeType = UDFNodeType | TriggerNodeType
export type NodeData = UDFNodeData | TriggerNodeData

export const invincibleNodeTypes: readonly string[] = [TriggerTypename]

const nodeTypes = {
  udf: udfNode,
  trigger: triggerNode,
}

const defaultEdgeOptions = {
  markerEnd: {
    type: MarkerType.ArrowClosed,
  },
  style: { strokeWidth: 2 },
}

function isInvincible<T>(node: Node<T>): boolean {
  return invincibleNodeTypes.includes(node?.type as string)
}

async function createNewNode(
  type: NodeTypename,
  workflowId: string,
  nodeData: NodeData,
  newPosition: XYPosition
): Promise<NodeType> {
  const common = {
    type,
    position: newPosition,
    data: nodeData,
  }
  let newNode: NodeType
  switch (type) {
    case "udf":
      const actionId = await createAction(
        nodeData.type,
        nodeData.title,
        workflowId
      )
      // Then create Action node in React Flow
      newNode = {
        id: actionId,
        ...common,
      } as UDFNodeType

      return newNode
    default:
      console.error("Invalid node type")
      throw new Error("Invalid node type")
  }
}

export function WorkflowCanvas() {
  const containerRef = useRef<HTMLDivElement>(null)
  const [nodes, setNodes, onNodesChange] = useNodesState([])
  const [edges, setEdges, onEdgesChange] = useEdgesState([])
  const [reactFlowInstance, setReactFlowInstance] =
    useState<ReactFlowInstance | null>(null)
  const { setViewport, getNode } = useReactFlow()
  const { workflowId } = useParams<{ workflowId: string }>()
  const { toast } = useToast()
  const { update } = useWorkflow()

  /**
   * Load the saved workflow
   */
  useEffect(() => {
    async function initializeReactFlowInstance() {
      if (!workflowId || !reactFlowInstance) {
        return
      }
      try {
        const workflow = await fetchWorkflow(workflowId)
        const flow = workflow.object as ReactFlowJsonObject
        if (!flow) throw new Error("No workflow data found")
        // Deselect all nodes
        const layouted = getLayoutedElements(
          flow.nodes || [],
          flow.edges || [],
          {
            rankdir: "TB",
          }
        )
        setNodes([
          ...layouted.nodes.map((node) => ({ ...node, selected: false })),
        ])
        setEdges([...layouted.edges])
        setViewport({
          x: flow.viewport.x,
          y: flow.viewport.y,
          zoom: flow.viewport.zoom,
        })
      } catch (error) {
        console.error("Failed to fetch workflow data:", error)
      }
    }
    initializeReactFlowInstance()
  }, [workflowId, reactFlowInstance])

  // React Flow callbacks
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
        await update({ entrypoint: entrypointNode.id })
      }
      setEdges((eds) => addEdge(params, eds))
    },
    [edges, setEdges, getNode, setNodes]
  )

  const onDragOver = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault()
      event.dataTransfer.dropEffect = "move"
    },
    [nodes]
  )

  // Adding a new node
  const onDrop = async (event: React.DragEvent) => {
    event.preventDefault()
    if (!reactFlowInstance) return

    // Limit total number of nodes
    if (nodes.length >= 50) {
      toast({
        title: "Invalid action",
        description: "Maximum 50 nodes allowed.",
      })
      return
    }

    const nodeTypename = event.dataTransfer.getData(
      "application/reactflow"
    ) as NodeTypename
    console.log("Node Typename:", nodeTypename)

    const rawNodeData = event.dataTransfer.getData("application/json")
    const nodeData = JSON.parse(rawNodeData) as NodeData

    console.log("Action Node Data:", nodeData)

    const reactFlowNodePosition = reactFlowInstance.screenToFlowPosition({
      x: event.clientX,
      y: event.clientY,
    })
    try {
      const newNode = await createNewNode(
        nodeTypename,
        workflowId,
        nodeData,
        reactFlowNodePosition
      )
      // Create Action in database
      setNodes((prevNodes) =>
        prevNodes
          .map((n) => ({ ...n, selected: false }))
          .concat({ ...newNode, selected: true })
      )
    } catch (error) {
      console.error("An error occurred while creating a new node:", error)
      toast({
        title: "Failed to create new node",
        description: "Could not create new node.",
      })
    }
  }

  const onNodesDelete = async <T,>(nodesToDelete: Node<T>[]) => {
    try {
      const filteredNodes = nodesToDelete.filter((node) => !isInvincible(node))
      if (filteredNodes.length === 0) {
        toast({
          title: "Invalid action",
          description: "Cannot delete invincible node",
        })
        return
      }
      await Promise.all(filteredNodes.map((node) => deleteAction(node.id)))
      setNodes((nds) =>
        nds.filter((n) => !nodesToDelete.map((nd) => nd.id).includes(n.id))
      )
      await updateWorkflowGraphObject(workflowId, reactFlowInstance)
      console.log("Nodes deleted successfully")
    } catch (error) {
      console.error("An error occurred while deleting Action nodes:", error)
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
            throw new Error("Could not find trigger or entrypoint node")
          }

          // 3. Update the trigger node UI state with the entrypoint id
          // We'll persist this through the trigger panel
          await update({ entrypoint: null })
        }
      })
      setEdges((eds) =>
        eds.filter((e) => !edgesToDelete.map((ed) => ed.id).includes(e.id))
      )
    },
    [edges, setEdges, getNode, setNodes]
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
    [nodes, setNodes]
  )

  const onLayout = useCallback(
    (direction: "TB" | "LR") => {
      const layouted = getLayoutedElements(nodes, edges, { rankdir: direction })
      setNodes([...layouted.nodes])
      setEdges([...layouted.edges])
    },
    [nodes, edges]
  )

  // Saving react flow instance state
  useEffect(() => {
    if (workflowId && reactFlowInstance) {
      updateWorkflowGraphObject(workflowId, reactFlowInstance)
    }
  }, [edges])

  const onNodesDragStop = () => {
    if (workflowId && reactFlowInstance) {
      updateWorkflowGraphObject(workflowId, reactFlowInstance)
    }
  }

  return (
    <div ref={containerRef} style={{ height: "100%" }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onConnect={onConnect}
        onDragOver={onDragOver}
        onDrop={onDrop}
        onEdgesChange={onEdgesChange}
        onEdgesDelete={onEdgesDelete}
        onInit={setReactFlowInstance}
        onNodesChange={handleNodesChange}
        onNodesDelete={onNodesDelete}
        onNodeDragStop={onNodesDragStop}
        defaultEdgeOptions={defaultEdgeOptions}
        nodeTypes={nodeTypes}
        fitViewOptions={{ maxZoom: 1 }}
        proOptions={{ hideAttribution: true }}
        deleteKeyCode={["Backspace", "Delete"]}
      >
        <Background />
        <Controls className="rounded-sm" />
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
            className="m-0 size-6 p-0 text-xs"
            onClick={() => onLayout("LR")}
          >
            <MoveHorizontalIcon className="size-3" strokeWidth={2} />
          </Button>
        </Panel>
      </ReactFlow>
    </div>
  )
}
