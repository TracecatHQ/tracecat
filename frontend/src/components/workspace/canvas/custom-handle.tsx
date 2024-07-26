import React, { useMemo } from "react"
import {
  Edge,
  getConnectedEdges,
  Handle,
  Node,
  NodeInternals,
  Position,
  useNodeId,
  useStore,
  type HandleProps,
} from "reactflow"

import { cn } from "@/lib/utils"

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

  return (
    <CustomFloatingHandle
      {...props}
      isConnectable={isHandleConnectable}
    ></CustomFloatingHandle>
  )
}

export function CustomFloatingHandle({
  type,
  position,
  className,
}: HandleProps & React.HTMLProps<HTMLDivElement>) {
  return (
    <Handle
      type={type}
      position={position}
      className={cn(
        "group left-1/2 !size-8 !-translate-x-1/2 !border-none !bg-transparent",
        position === Position.Top && "!-top-8",
        position === Position.Bottom && "!-bottom-8",
        position === Position.Left && "!-left-8",
        position === Position.Right && "!-right-8",
        className
      )}
    >
      <div className="pointer-events-none absolute left-1/2 top-1/2 size-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-muted-foreground/50 transition-all group-hover:size-4 group-hover:bg-emerald-400 group-hover:shadow-lg" />
    </Handle>
  )
}
