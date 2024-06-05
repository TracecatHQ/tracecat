import React, { useMemo } from "react"
import {
  Edge,
  getConnectedEdges,
  Handle,
  Node,
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
  const { nodeInternals, edges } = useStore((s: any) => ({
    nodeInternals: s.nodeInternals,
    edges: s.edges,
  }))
  const nodeId = useNodeId()

  const isHandleConnectable = useMemo(() => {
    if (typeof props.isConnectable === "function") {
      const node = nodeInternals.get(nodeId)
      const connectedEdges = getConnectedEdges([node], edges)

      return props.isConnectable({ node, connectedEdges })
    }

    if (typeof props.isConnectable === "number") {
      const node = nodeInternals.get(nodeId)
      const connectedEdges = getConnectedEdges([node], edges)

      return connectedEdges.length < props.isConnectable
    }

    return props.isConnectable
  }, [nodeInternals, edges, nodeId, props.isConnectable])

  return <Handle {...props} isConnectable={isHandleConnectable}></Handle>
}
