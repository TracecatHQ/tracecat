import React, { useMemo } from "react"
import { JoinStrategy } from "@/client"
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
import { Badge } from "@/components/ui/badge"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"

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

export function TriggerSourceHandle({
  className,
}: React.HTMLProps<HTMLDivElement>) {
  return (
    <CustomHandle
      type="source"
      position={Position.Bottom}
      isConnectable={1}
      className={className}
    />
  )
}

export function ActionTargetHandle({
  className,
  join_strategy,
  indegree,
}: React.HTMLProps<HTMLDivElement> & {
  join_strategy?: JoinStrategy
  indegree?: number
}) {
  return (
    <Handle
      type="target"
      position={Position.Top}
      className={cn(
        "group !-top-8 left-1/2 !size-8 !-translate-x-1/2 !border-none !bg-transparent",
        className
      )}
    >
      <div className="relative size-full">
        {/* Base dot that fades out */}
        <div
          className={cn(
            "pointer-events-none absolute left-1/2 top-1/2 rounded-full transition-all duration-200",
            "size-2 -translate-x-1/2 -translate-y-1/2 bg-muted-foreground/50",
            "group-hover:size-4 group-hover:bg-emerald-400 group-hover:shadow-lg",
            indegree && indegree > 1 && "opacity-0"
          )}
        />

        {/* Badge that fades in */}
        <Badge
          className={cn(
            "absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-lg px-2 text-xs",
            "transition-all duration-200",
            !indegree || indegree <= 1
              ? "scale-75 opacity-0"
              : "scale-100 opacity-100",
            join_strategy === "all" && "bg-blue-500/80 hover:bg-blue-600/80",
            join_strategy === "any" && "bg-amber-500/80 hover:bg-amber-600/80"
          )}
        >
          {join_strategy?.toLocaleUpperCase() || "ALL"}
        </Badge>
      </div>
    </Handle>
  )
}

export function ActionSoruceSuccessHandle({
  type,
  className,
}: Omit<HandleProps, "position"> & React.HTMLProps<HTMLDivElement>) {
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <Handle
            id="success"
            type={type}
            position={Position.Bottom}
            className={cn(
              "group !-bottom-8 !size-8 !-translate-x-1/2 !border-none !bg-transparent",
              className
            )}
          >
            <div className="pointer-events-none absolute left-1/2 top-1/2 size-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-emerald-400 shadow-sm transition-all duration-150 group-hover:size-4 group-hover:bg-emerald-400 group-hover:shadow-lg" />
          </Handle>
        </TooltipTrigger>
        <TooltipContent>Success</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

export function ActionSourceErrorHandle({
  type,
  className,
}: Omit<HandleProps, "position"> & React.HTMLProps<HTMLDivElement>) {
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <Handle
            id="error"
            type={type}
            position={Position.Bottom}
            className={cn(
              "group !-bottom-8 !left-[56%] !size-8 !-translate-x-1/2 !border-none !bg-transparent",
              className
            )}
          >
            <div className="pointer-events-none absolute left-1/2 top-1/2 size-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-rose-400 shadow-sm transition-all duration-150 group-hover:size-4 group-hover:bg-rose-400 group-hover:shadow-lg" />
          </Handle>
        </TooltipTrigger>
        <TooltipContent>Error</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
