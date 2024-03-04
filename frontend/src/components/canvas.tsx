import React, { useCallback, useRef, useState } from "react"
import axios from "axios"

import ReactFlow, {
  Background,
  Connection,
  Controls,
  Edge,
  MarkerType,
  Node,
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

import { useSelectedWorkflow } from "@/providers/selected-workflow"

const nodeTypes = {
  action: ActionNode,
}

const defaultEdgeOptions = {
  markerEnd: {
    type: MarkerType.ArrowClosed,
  },
  style: { strokeWidth: 3 },
}

type ActionMetadata = {
  id: string;
  workflow_id: string;
  title: string;
  description: string;
};


const WorkflowCanvas: React.FC = () => {
  const reactFlowWrapper = useRef<HTMLDivElement>(null)
  const [nodes, setNodes, onNodesChange] = useNodesState([])
  const [edges, setEdges, onEdgesChange] = useEdgesState([])
  const [reactFlowInstance, setReactFlowInstance] = useState<ReactFlowInstance | null>(null)
  const { selectedWorkflowId } = useSelectedWorkflow();

  const { toast } = useToast()

  // CRUD operations
  const saveFlow = useCallback(async () => {
    if (!selectedWorkflowId || !reactFlowInstance) return;
    try {
      const flowObject = reactFlowInstance.toObject();
      const updateFlowObjectParams = JSON.stringify({ object: JSON.stringify(flowObject) });
      await axios.post(`http://localhost:8000/workflows/${selectedWorkflowId}`, updateFlowObjectParams, {
        headers: {
          "Content-Type": "application/json",
        },
      });

      console.log("Flow saved successfully");
    } catch (error) {
      console.error("Error saving flow:", error);
    }
  }, [selectedWorkflowId, reactFlowInstance]);

  const createAction = async (type: string, title: string) => {
    if (!selectedWorkflowId || !reactFlowInstance) return;
    try {
      const createActionMetadata = JSON.stringify({
        workflow_id: selectedWorkflowId,
        type: type,
        title: title
      });
      const response = await axios.post<ActionMetadata>("http://localhost:8000/actions", createActionMetadata, {
        headers: {
          "Content-Type": "application/json",
        },
      });
      console.log("Action created successfully:", response.data);
      return response.data.id;
    } catch (error) {
      console.error("Error creating action:", error);
    }
  }

  // React Flow callbacks
  const onConnect = useCallback(
    (params: Edge | Connection) => {
      setEdges((eds) => addEdge(params, eds));
      saveFlow();
    },
    [toast, edges, setEdges]
  )

  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
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

    // Create Action in database
    createAction(actionNodeData.type, actionNodeData.title).then((actionId) => {
      if (!actionId) return;
      // Then create Action node in React Flow
      const newNode = {
        id: actionId,
        type: reactFlowNodeType,
        position: reactFlowNodePosition,
        data: actionNodeData,
      } as Node<ActionNodeData>

      setNodes((nds) => nds.concat(newNode));
      saveFlow();
    });
  }

  const onEdgesDelete = useCallback((edgesToDelete: Edge[]) => {
    setEdges((eds) => eds.filter((e) => !edgesToDelete.map((ed) => ed.id).includes(e.id)));
    saveFlow();
  }, [setEdges, saveFlow]);

  const onNodesDelete = useCallback((nodesToDelete: Node[]) => {
    setNodes((nds) => nds.filter((n) => !nodesToDelete.map((nd) => nd.id).includes(n.id)));
    saveFlow();
  }, [setNodes, saveFlow]);

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

const WorkflowBuilder = ReactFlowProvider
const useWorkflowBuilder = useReactFlow

export { WorkflowCanvas, WorkflowBuilder, useWorkflowBuilder }
