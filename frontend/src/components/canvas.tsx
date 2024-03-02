import React, { useCallback, useRef, useState } from "react"
import ReactFlow, {
  Background,
  Connection,
  Controls,
  Edge,
  MarkerType,
  Node,
  OnConnect,
  ReactFlowInstance,
  ReactFlowProvider,
  addEdge,
  useEdgesState,
  useNodesState,
  useReactFlow,
} from "reactflow"

import "reactflow/dist/style.css"
import { useToast } from "@/components/ui/use-toast"
import ActionNode, { ActionNodeData } from "@/components/action-node"

let id = 0
const getActionNodeId = (): string => `node_${id++}`

const nodeTypes = {
  action: ActionNode,
}

const defaultEdgeOptions = {
  markerEnd: {
    type: MarkerType.ArrowClosed,
  },
  style: { strokeWidth: 3 },
}

const Workflow: React.FC = () => {
  const reactFlowWrapper = useRef<HTMLDivElement>(null)
  const [nodes, setNodes, onNodesChange] = useNodesState([])
  const [edges, setEdges, onEdgesChange] = useEdgesState([])
  const [reactFlowInstance, setReactFlowInstance] =
    useState<ReactFlowInstance | null>(null)
  const { toast } = useToast()

  const onConnect = useCallback(
    (params: Edge | Connection) => {
      setEdges((eds) => addEdge(params, eds))
    },
    [toast, edges, setEdges]
  )

  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault()
    event.dataTransfer.dropEffect = "move"
  }, [])

  const onDrop = (event: React.DragEvent) => {
    event.preventDefault()

    // Limit total number of nodes
    if (nodes.length >= 50) {
      toast({
        title: "Invalid action",
        description: "Maximum 50 nodes allowed.",
      })
      return
    }

    const reactFlowNodeType = event.dataTransfer.getData("application/reactflow");
    const actionNodeData = JSON.parse(
      event.dataTransfer.getData("application/json")
    ) as ActionNodeData

    if (!actionNodeData || !reactFlowNodeType || !reactFlowInstance) return

    const reactFlowNodePosition = reactFlowInstance.screenToFlowPosition({
      x: event.clientX,
      y: event.clientY,
    })

    const newNode = {
      id: getActionNodeId(),
      type: reactFlowNodeType,
      position: reactFlowNodePosition,
      data: actionNodeData,
    } as Node<ActionNodeData>

    setNodes((nds) => nds.concat(newNode))
  }

  return (
    <div ref={reactFlowWrapper} style={{ height: "100%" }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        defaultEdgeOptions={defaultEdgeOptions}
        onConnect={onConnect as OnConnect}
        onInit={setReactFlowInstance}
        onDrop={onDrop}
        onDragOver={onDragOver}
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

const WorkflowBuilder = ReactFlowProvider
const useWorkflowBuilder = useReactFlow

export { WorkflowBuilder, useWorkflowBuilder }
export default Workflow
