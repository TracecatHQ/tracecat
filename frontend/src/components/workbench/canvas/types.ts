import {
  Connection,
  Edge,
  EdgeChange,
  NodeChange,
  type Node,
  type ReactFlowJsonObject,
} from "@xyflow/react"

// State and reducer types

export type GraphState = {
  nodes: Node[]
  edges: Edge[]
  viewport: { x: number; y: number; zoom: number }
}

export type GraphAction =
  | { type: "SET_INITIAL_GRAPH"; payload: ReactFlowJsonObject<any> }
  | { type: "NODES_CHANGE"; changes: NodeChange[] }
  | { type: "EDGES_CHANGE"; changes: EdgeChange[] }
  | {
      type: "VIEWPORT_CHANGE"
      viewport: { x: number; y: number; zoom: number }
    }
  | { type: "ADD_NODE"; node: Node }
  | { type: "ADD_EDGE"; edge: Edge | Connection }
  | { type: "SET_NODES_AND_EDGES"; nodes: Node[]; edges: Edge[] }
  | { type: "CONFIRMED_DELETION"; nodeIds: string[] }
