import React, { useMemo } from "react"
import {
  Edge,
  getConnectedEdges,
  Handle,
  Node,
  NodeInternals,
  useNodeId,
  useStore,
  type HandleProps,
} from "reactflow"

interface CustomHandleProps
  extends Omit<HandleProps, "isConnectable">,
    React.HTMLAttributes<HTMLDivElement> {
  isConnectable?:
    | number
    | boolean
    | ((args: { node: Node; connectedEdges: Edge[] }) => boolean)
}
export function CustomHandle(props: CustomHandleProps) {
  const { nodeInternals, edges } = useStore<{
    nodeInternals: NodeInternals
    edges: Edge[]
  }>((s) => ({
    nodeInternals: s.nodeInternals,
    edges: s.edges,
  }))
  const nodeId = useNodeId()

  const isHandleConnectable = useMemo(() => {
    if (!nodeId) {
      return false
    }

    if (typeof props.isConnectable === "function") {
      const node = nodeInternals.get(nodeId)
      if (!node) {
        console.error(
          `Node with id ${nodeId} not found in nodeInternals. Make sure you are using the latest version of react-flow.`
        )
        return false
      }
      const connectedEdges = getConnectedEdges([node], edges)

      return props.isConnectable({ node, connectedEdges })
    }

    if (typeof props.isConnectable === "number") {
      const node = nodeInternals.get(nodeId)
      if (!node) {
        console.error(
          `Node with id ${nodeId} not found in nodeInternals. Make sure you are using the latest version of react-flow.`
        )
        return false
      }
      const connectedEdges = getConnectedEdges([node], edges)

      return connectedEdges.length < props.isConnectable
    }

    return props.isConnectable
  }, [nodeInternals, edges, nodeId, props.isConnectable])

  if (!nodeInternals || !edges || !nodeId) {
    return null
  }

  return <Handle {...props} isConnectable={isHandleConnectable}></Handle>
}
