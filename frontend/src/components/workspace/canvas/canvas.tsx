import React, { useCallback, useEffect, useRef, useState } from "react"
import ReactFlow, {
  addEdge,
  Background,
  Connection,
  Controls,
  Edge,
  MarkerType,
  NodeChange,
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

import { Workflow } from "@/types/schemas"
import {
  createAction,
  deleteAction,
  fetchWorkflow,
  updateDndFlow,
} from "@/lib/workflow"
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

function getInitialState(
  workflow: Workflow,
  containerRef: React.RefObject<HTMLDivElement>
) {
  const rect = containerRef.current?.getBoundingClientRect()
  const width = rect?.width || 0
  const height = rect?.height || 0
  const graphInitialState = {
    nodes: [
      {
        id: `trigger-${workflow.id}`,
        type: "trigger",
        position: { x: width / 2, y: height / 2 },
        data: {
          type: "trigger",
          title: "Trigger",
          status: "online",
          isConfigured: true,
          webhook: workflow.webhook,
          schedules: workflow.schedules,
        },
      },
    ],
    edges: [],
    viewport: {
      x: 0,
      y: 0,
      zoom: 1,
    },
  }
  return graphInitialState
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
      if (!workflowId) {
        return
      }
      try {
        const workflow = await fetchWorkflow(workflowId)
        const flow = workflow.object as ReactFlowJsonObject
        if (flow) {
          // If there is a saved React Flow configuration, load it
          setNodes(flow.nodes || [])
          setEdges(flow.edges || [])
          setViewport({
            x: flow.viewport.x,
            y: flow.viewport.y,
            zoom: flow.viewport.zoom,
          })
        } else {
          // Otherwise, load the default nodes
          const initialState = getInitialState(workflow, containerRef)
          setNodes(initialState.nodes)
          setEdges(initialState.edges)
          setViewport(initialState.viewport)
        }
      } catch (error) {
        console.error("Failed to fetch workflow data:", error)
      }
    }
    initializeReactFlowInstance()
  }, [workflowId])

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
        let triggerNode = nodes.find(
          (node) => node.type === "trigger"
        ) as TriggerNodeType
        // 2. Find the entrypoint node
        let entrypointNode = getNode(
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
      await updateDndFlow(workflowId, reactFlowInstance)
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
          let triggerNode = nodes.find(
            (node) => node.type === "trigger"
          ) as TriggerNodeType
          // 2. Find the entrypoint node
          let entrypointNode = getNode(
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

  // Saving react flow instance state
  useEffect(() => {
    if (workflowId && reactFlowInstance) {
      updateDndFlow(workflowId, reactFlowInstance)
    }
  }, [edges])

  const onNodesDragStop = (
    event: React.MouseEvent,
    node: UDFNodeType,
    nodes: UDFNodeType[]
  ) => {
    if (workflowId && reactFlowInstance) {
      updateDndFlow(workflowId, reactFlowInstance)
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
        <Controls />
      </ReactFlow>
    </div>
  )
}
