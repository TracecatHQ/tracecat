import React, { useMemo } from "react"
import { ActionRead } from "@/client"
import {
  Edge,
  getConnectedEdges,
  Handle,
  Node,
  Position,
  useNodeId,
  useStore,
  type HandleProps,
} from "@xyflow/react"
import { GitBranch, Merge, Repeat } from "lucide-react"

import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"

interface CustomHandleProps
  extends Omit<HandleProps, "isConnectable" | "id">,
    React.HTMLAttributes<HTMLDivElement> {
  isConnectable?:
    | number
    | boolean
    | ((args: { node: Node; connectedEdges: Edge[] }) => boolean)
}
export function CustomHandle(props: CustomHandleProps) {
  const { nodeLookup, edges } = useStore((s) => ({
    nodeLookup: s.nodeLookup,
    edges: s.edges,
  }))
  const nodeId = useNodeId()

  const isHandleConnectable = useMemo(() => {
    if (!nodeId) {
      return false
    }

    if (typeof props.isConnectable === "function") {
      const node = nodeLookup.get(nodeId)
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
      const node = nodeLookup.get(nodeId)
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
  }, [nodeLookup, edges, nodeId, props])

  if (!nodeLookup || !edges || !nodeId) {
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
  action,
  indegree,
}: {
  action?: ActionRead
  indegree?: number
}) {
  const {
    join_strategy: joinStrategy,
    for_each: forEach,
    run_if: runIf,
  } = action?.control_flow || {}
  const hasJoin = indegree && indegree > 1
  const hasForEach = !!forEach || (Array.isArray(forEach) && forEach.length > 0)
  const hasRunIf = !!runIf

  // Determine if there are no effects based on the conditions
  const hasEffects = hasForEach || hasRunIf || hasJoin
  return (
    <Handle
      type="target"
      position={Position.Top}
      draggable={false}
      className={
        "group !-top-8 left-1/2 !size-8 !-translate-x-1/2 !border-none !bg-transparent"
      }
    >
      <div className="relative size-full group-hover:cursor-default">
        {/* Base dot that fades out */}
        <div
          className={cn(
            "pointer-events-none absolute left-1/2 top-1/2 rounded-full transition-all duration-200",
            "size-2 -translate-x-1/2 -translate-y-1/2 bg-muted-foreground/50",
            "group-hover:size-4 group-hover:bg-emerald-400 group-hover:shadow-lg",
            hasEffects && "opacity-0"
          )}
        />

        {/* Badge that fades in. This will contain all the other effects. */}
        <Badge
          className={cn(
            "absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-lg px-2 text-xs",
            "transition-all duration-200",
            "group-hover:cursor-pointer",
            hasEffects ? "scale-100 opacity-100" : "scale-75 opacity-0",
            joinStrategy === "all" && "bg-blue-500/80 hover:bg-blue-600/80",
            joinStrategy === "any" && "bg-amber-500/80 hover:bg-amber-600/80",
            forEach && "bg-indigo-400/80 hover:bg-indigo-500/80",
            runIf && "bg-rose-500/80 hover:bg-rose-600/80"
          )}
        >
          <div className="flex items-center space-x-2">
            {hasJoin && (
              <span className="flex items-center space-x-1">
                <Merge className="size-3 rotate-180" strokeWidth={2.5} />
                <span>{joinStrategy?.toLocaleUpperCase() || "ALL"}</span>
              </span>
            )}
            {forEach && (
              <span className="flex items-center space-x-1">
                <Repeat className="size-3" strokeWidth={2.5} />
                <span>LOOP</span>
              </span>
            )}
            {runIf && (
              <span className="flex items-center space-x-1">
                <GitBranch className="size-3" strokeWidth={2.5} />
                <pre className="text-xs">{runIf.slice(3, -2).trim()}</pre>
              </span>
            )}
          </div>
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
