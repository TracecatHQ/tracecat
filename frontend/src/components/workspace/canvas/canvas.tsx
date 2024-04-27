import React, { useCallback, useEffect, useRef, useState } from "react"
import ReactFlow, {
  addEdge,
  Background,
  Connection,
  Controls,
  Edge,
  MarkerType,
  Node,
  ReactFlowInstance,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type ReactFlowJsonObject,
} from "reactflow"

import "reactflow/dist/style.css"

import { useParams } from "next/navigation"

import { NodeType } from "@/types/schemas"
import {
  createAction,
  deleteAction,
  fetchWorkflow,
  updateDndFlow,
} from "@/lib/flow"
import { useToast } from "@/components/ui/use-toast"
import ActionNode, {
  ActionNodeType,
} from "@/components/workspace/canvas/action-node"
import IntegrationNode from "@/components/workspace/canvas/integration-node"

const nodeTypes = {
  action: ActionNode,
  integrations: IntegrationNode,
}

const defaultEdgeOptions = {
  markerEnd: {
    type: MarkerType.ArrowClosed,
  },
  style: { strokeWidth: 2 },
}
export type NodeDataType = Node<NodeData>
export interface NodeData {
  type: NodeType
  title: string
  status: "online" | "offline"
  isConfigured: boolean
  numberOfEvents: number
  // Generic metadata
}
const WorkflowCanvas: React.FC = () => {
  const reactFlowWrapper = useRef<HTMLDivElement>(null)
  const [nodes, setNodes, onNodesChange] = useNodesState([])
  const [edges, setEdges, onEdgesChange] = useEdgesState([])
  const [reactFlowInstance, setReactFlowInstance] =
    useState<ReactFlowInstance | null>(null)
  const { setViewport } = useReactFlow()
  const { workflowId } = useParams<{ workflowId: string }>()
  const { toast } = useToast()

  useEffect(() => {
    async function initializeReactFlowInstance() {
      if (!workflowId) {
        return
      }
      try {
        const response = await fetchWorkflow(workflowId)
        const flow = response.object as ReactFlowJsonObject
        if (flow) {
          // If there is a saved React Flow configuration, load it
          setNodes(flow.nodes || [])
          setEdges(flow.edges || [])
          setViewport({
            x: flow.viewport.x,
            y: flow.viewport.y,
            zoom: flow.viewport.zoom,
          })
        }
      } catch (error) {
        console.error("Failed to fetch workflow data:", error)
      }
    }
    initializeReactFlowInstance()
  }, [workflowId])

  // React Flow callbacks
  const onConnect = useCallback(
    (params: Edge | Connection) => {
      setEdges((eds) => addEdge(params, eds))
    },
    [edges, setEdges]
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

    const reactFlowNodeType = event.dataTransfer.getData(
      "application/reactflow"
    )
    console.log("React Flow Node Type:", reactFlowNodeType)

    const nodeData = JSON.parse(
      event.dataTransfer.getData("application/json")
    ) as NodeData

    console.log("Action Node Data:", nodeData)

    // Create Action in database
    const actionId = await createAction(
      nodeData.type,
      nodeData.title,
      workflowId
    )
    const reactFlowNodePosition = reactFlowInstance.screenToFlowPosition({
      x: event.clientX,
      y: event.clientY,
    })
    // Then create Action node in React Flow
    const newNode = {
      id: actionId,
      type: reactFlowNodeType,
      position: reactFlowNodePosition,
      data: nodeData,
    } as NodeDataType

    setNodes((prevNodes) =>
      prevNodes
        .map((n) => ({ ...n, selected: false }))
        .concat({ ...newNode, selected: true })
    )
  }

  const onNodesDelete = async (nodesToDelete: ActionNodeType[]) => {
    try {
      await Promise.all(nodesToDelete.map((node) => deleteAction(node.id)))
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
    (edgesToDelete: Edge[]) => {
      setEdges((eds) =>
        eds.filter((e) => !edgesToDelete.map((ed) => ed.id).includes(e.id))
      )
    },
    [edges, setEdges]
  )

  // Saving react flow instance state
  useEffect(() => {
    if (workflowId && reactFlowInstance) {
      updateDndFlow(workflowId, reactFlowInstance)
    }
  }, [edges])

  const onNodesDragStop = (
    event: React.MouseEvent,
    node: ActionNodeType,
    nodes: ActionNodeType[]
  ) => {
    if (workflowId && reactFlowInstance) {
      updateDndFlow(workflowId, reactFlowInstance)
    }
  }

  return (
    <div ref={reactFlowWrapper} style={{ height: "100%" }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onConnect={onConnect}
        onDragOver={onDragOver}
        onDrop={onDrop}
        onEdgesChange={onEdgesChange}
        onEdgesDelete={onEdgesDelete}
        onInit={setReactFlowInstance}
        onNodesChange={onNodesChange}
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

export { WorkflowCanvas }
