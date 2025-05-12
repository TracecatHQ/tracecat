import React, { useMemo } from "react"
import { ActionRead } from "@/client"
import { useWorkflowBuilder } from "@/providers/builder"
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
import {
  AlertTriangle,
  ArrowRight,
  Edit,
  GitBranch,
  Merge,
  Repeat,
} from "lucide-react"

import { cn, splitConditionalExpression } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Label } from "@/components/ui/label"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
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

function parseForEach(forEach: string): {
  variable: string
  array: string
} | null {
  // match ${{ for <variable> in <array> }}
  const match = /^\$\{\{.*for\s+(.*)\s+in\s+(.*)\}\}$/.exec(forEach)
  if (!match) {
    return null
  }
  const [, variable, array] = match
  return { variable, array }
}

function ForEachTooltip({ forEach }: { forEach: string }) {
  const parsed = parseForEach(forEach)
  if (parsed) {
    return (
      <div className="flex items-center space-x-1 text-xs">
        <span className="inline-block rounded-sm border border-input bg-muted-foreground/10 px-0.5 py-0 font-mono tracking-tight text-foreground/70">
          {parsed.array}
        </span>
        <ArrowRight className="size-3" />
        <span className="inline-block rounded-sm border border-input bg-muted-foreground/10 px-0.5 py-0 font-mono tracking-tight text-foreground/70">
          {parsed.variable}
        </span>
      </div>
    )
  }
  return (
    <span className="flex items-center space-x-1 text-xs text-foreground/70">
      <AlertTriangle className="size-3 fill-red-500 stroke-white" />
      <span className="inline-block font-mono tracking-tight text-foreground/70">
        Invalid For Loop
      </span>
    </span>
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

  // Determine if there are no effects based on the conditions - exclude forEach since it's moved
  const hasEffects = hasRunIf || hasJoin
  const { actionPanelRef } = useWorkflowBuilder()

  return (
    <>
      {hasForEach && (
        <ForEachEffect
          forEach={forEach}
          onClick={() => {
            const ref = actionPanelRef.current
            if (ref) {
              if (ref.isCollapsed()) {
                ref.expand()
              }
              ref.setActiveTab("control-flow")
            }
          }}
        />
      )}
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

          {/* Badges container that fades in */}
          <div
            className={cn(
              "absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-md shadow-sm",
              "flex items-center justify-center transition-all duration-200",
              "group-hover:cursor-pointer",
              hasEffects ? "scale-100 opacity-100" : "scale-75 opacity-0"
            )}
            onClick={() => {
              const ref = actionPanelRef.current
              if (ref) {
                if (ref.isCollapsed()) {
                  ref.expand()
                }
                ref.setActiveTab("control-flow")
              }
            }}
          >
            {hasJoin && (
              <Badge
                className={cn(
                  "border-0 px-2 text-xs shadow-none",
                  joinStrategy === "all" &&
                    "bg-blue-500/80 hover:bg-blue-600/80",
                  joinStrategy === "any" &&
                    "bg-amber-500/80 hover:bg-amber-600/80",
                  runIf && "mr-0 rounded-r-none"
                )}
              >
                <span className="flex items-center space-x-1">
                  <Merge className="size-3 rotate-180" strokeWidth={2.5} />
                  <span>{joinStrategy?.toLocaleUpperCase() || "ALL"}</span>
                </span>
              </Badge>
            )}
            {runIf && (
              <Badge
                className={cn(
                  "border-0 px-2 text-xs shadow-none",
                  "bg-teal-500/80 hover:bg-teal-600/80",
                  hasJoin && "ml-0 rounded-l-none"
                )}
              >
                <span className="flex items-center space-x-1">
                  <GitBranch className="size-3" strokeWidth={2.5} />
                  <pre className="text-xs tracking-tighter">
                    {splitConditionalExpression(runIf.slice(3, -2).trim(), 50)}
                  </pre>
                </span>
              </Badge>
            )}
          </div>
        </div>
      </Handle>
    </>
  )
}

function ForEachEffect({
  forEach,
  onClick,
}: {
  forEach?: string | string[]
  onClick: () => void
}) {
  const [open, setOpen] = React.useState(false)

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <div
          className="group absolute left-0 top-0 -translate-x-[150%]"
          onClick={() => setOpen(!open)}
        >
          <div className="flex size-6 items-center justify-center rounded-lg bg-indigo-400 shadow-sm hover:bg-indigo-400/80 group-hover:cursor-pointer">
            <Repeat className="size-3 stroke-muted" strokeWidth={2.5} />
          </div>
        </div>
      </PopoverTrigger>
      <PopoverContent
        className="w-auto rounded-lg p-0 shadow-sm"
        side="left"
        align="start"
        alignOffset={0}
        avoidCollisions={false}
        onInteractOutside={(e) => {
          // Prevent the popover from closing when clicking outside
          e.preventDefault()
        }}
      >
        <div className="w-full border-b bg-muted-foreground/5 px-3 py-[2px]">
          <div className="flex items-center justify-between">
            <Label className="flex items-center text-xs text-muted-foreground">
              <span className="font-medium">For Loop</span>
            </Label>
            <span className="my-px ml-auto flex items-center space-x-2">
              <Tooltip>
                <TooltipTrigger asChild>
                  <Edit
                    className="size-3 stroke-muted-foreground/70 hover:cursor-pointer"
                    onClick={(e) => {
                      e.stopPropagation()
                      onClick()
                    }}
                  />
                </TooltipTrigger>
                <TooltipContent>Open editor</TooltipContent>
              </Tooltip>
            </span>
          </div>
        </div>
        <div className="flex flex-col gap-2 p-2">
          <div className="flex items-center space-x-1 text-xs text-foreground/70">
            <span>Collection</span>
            <ArrowRight className="size-3" />
            <span>Loop variable</span>
          </div>

          {typeof forEach === "string" ? (
            <ForEachTooltip forEach={forEach} />
          ) : Array.isArray(forEach) && forEach.length > 0 ? (
            <div className="flex flex-col space-y-1">
              {forEach.map((statement) => (
                <ForEachTooltip key={statement} forEach={statement} />
              ))}
            </div>
          ) : (
            <span className="flex items-center space-x-1 text-xs text-foreground/70">
              <AlertTriangle className="size-3" />
              <span className="inline-block font-mono tracking-tight text-foreground/70">
                Invalid For Loop Type
              </span>
            </span>
          )}
        </div>
      </PopoverContent>
    </Popover>
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
