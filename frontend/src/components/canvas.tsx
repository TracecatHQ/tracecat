import React, { useCallback, useEffect, useRef, useState } from "react"
import axios from "axios"
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
  useOnSelectionChange,
  useReactFlow,
} from "reactflow"

import "reactflow/dist/style.css"

import { useParams } from "next/navigation"

import { saveFlow } from "@/lib/flow"
import { Skeleton } from "@/components/ui/skeleton"
import { useToast } from "@/components/ui/use-toast"
import ActionNode, { ActionNodeData } from "@/components/action-node"

const nodeTypes = {
  action: ActionNode,
}

const defaultEdgeOptions = {
  markerEnd: {
    type: MarkerType.ArrowClosed,
  },
  style: { strokeWidth: 2 },
}

type ActionMetadata = {
  id: string
  workflow_id: string
  title: string
  description: string
}

interface ActionResponse {
  id: string
  title: string
  description: string
  status: string
  inputs: { [key: string]: any } | null
}

interface WorkflowResponse {
  id: string
  title: string
  description: string
  status: string
  actions: { [key: string]: ActionResponse[] }
  object: { [key: string]: any } | null
}

const WorkflowCanvas: React.FC = () => {
  const reactFlowWrapper = useRef<HTMLDivElement>(null)
  const [nodes, setNodes, onNodesChange] = useNodesState([])
  const [edges, setEdges, onEdgesChange] = useEdgesState([])
  const [reactFlowInstance, setReactFlowInstance] =
    useState<ReactFlowInstance | null>(null)

  const { setViewport } = useReactFlow()
  const params = useParams<{ id: string }>()
  const workflowId = params.id

  const { toast } = useToast()

  // CRUD operations

  useEffect(() => {
    const initializeReactFlowInstance = () => {
      if (workflowId) {
        axios
          .get<WorkflowResponse>(
            `http://localhost:8000/workflows/${workflowId}`
          )
          .then((response) => {
            const flow = response.data.object
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
          })
          .catch((error) => {
            console.error("Failed to fetch workflow data:", error)
          })
      }
    }

    initializeReactFlowInstance()
  }, [workflowId, setNodes, setEdges, setViewport])

  const createAction = async (type: string, title: string) => {
    if (!workflowId || !reactFlowInstance) return
    try {
      const createActionMetadata = JSON.stringify({
        workflow_id: workflowId,
        type: type,
        title: title,
      })
      const response = await axios.post<ActionMetadata>(
        "http://localhost:8000/actions",
        createActionMetadata,
        {
          headers: {
            "Content-Type": "application/json",
          },
        }
      )
      console.log("Action created successfully:", response.data)
      return response.data.id
    } catch (error) {
      console.error("Error creating action:", error)
    }
  }

  const deleteAction = async (actionId: string) => {
    try {
      const url = `http://localhost:8000/actions/${actionId}`
      await axios.delete(url)
      console.log(`Action with ID ${actionId} deleted successfully.`)
    } catch (error) {
      console.error(`Error deleting action with ID ${actionId}:`, error)
    }
  }

  // React Flow callbacks
  const onConnect = useCallback(
    (params: Edge | Connection) => {
      setEdges((eds) => addEdge(params, eds))
    },
    [toast, edges, setEdges]
  )

  const onDragOver = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault()
      event.dataTransfer.dropEffect = "move"
    },
    [nodes]
  )

  const onDrop = useCallback(
    async (event: React.DragEvent) => {
      event.preventDefault()

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
      const actionNodeData = JSON.parse(
        event.dataTransfer.getData("application/json")
      ) as ActionNodeData

      if (!actionNodeData || !reactFlowNodeType || !reactFlowInstance) return

      const reactFlowNodePosition = reactFlowInstance.screenToFlowPosition({
        x: event.clientX,
        y: event.clientY,
      })

      // Create Action in database
      const actionId = await createAction(
        actionNodeData.type,
        actionNodeData.title
      )
      if (!actionId) return
      // Then create Action node in React Flow
      const newNode = {
        id: actionId,
        type: reactFlowNodeType,
        position: reactFlowNodePosition,
        data: actionNodeData,
      } as Node<ActionNodeData>

      setNodes((nds) => nds.concat(newNode))
    },
    [nodes, createAction]
  )

  const onNodesDelete = useCallback(
    (nodesToDelete: Node[]) => {
      Promise.all(nodesToDelete.map((node) => deleteAction(node.id)))
        .then(() => {
          setNodes((nds) =>
            nds.filter((n) => !nodesToDelete.map((nd) => nd.id).includes(n.id))
          )
        })
        .catch((error) => {
          console.error("An error occurred while deleting Action nodes:", error)
        })
    },
    [nodes, setNodes, deleteAction]
  )

  const onEdgesDelete = useCallback(
    (edgesToDelete: Edge[]) => {
      setEdges((eds) =>
        eds.filter((e) => !edgesToDelete.map((ed) => ed.id).includes(e.id))
      )
    },
    [edges, setEdges]
  )

  useEffect(() => {
    if (workflowId && reactFlowInstance) {
      saveFlow(workflowId, reactFlowInstance)
    }
  }, [nodes, edges])

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
        defaultEdgeOptions={defaultEdgeOptions}
        nodeTypes={nodeTypes}
        fitViewOptions={{ maxZoom: 1 }}
        proOptions={{ hideAttribution: true }}
      >
        <Background />
        <Controls />
      </ReactFlow>
    </div>
  )
}

export { WorkflowCanvas }
