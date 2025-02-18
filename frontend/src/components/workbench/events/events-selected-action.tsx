"use client"

import React, { useMemo } from "react"
import {
  EventFailure,
  WorkflowExecutionEventCompact,
  WorkflowExecutionReadCompact,
  WorkflowRead,
} from "@/client"
import { useWorkflowBuilder } from "@/providers/builder"
import { useWorkflow } from "@/providers/workflow"
import { CheckCheckIcon, CircleDot, CopyIcon, LoaderIcon } from "lucide-react"
import JsonView from "react18-json-view"
import { NodeMeta } from "react18-json-view/dist/types"

import { useAction } from "@/lib/hooks"
import { cn, slugify } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { CodeBlock } from "@/components/code-block"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { getWorkflowEventIcon } from "@/components/workbench/events/events-workflow"

function ref2id(
  ref: string,
  workflow: WorkflowRead | null
): string | undefined {
  const action = Object.values(workflow?.actions || {}).find(
    (act) => slugify(act.title) === ref
  )
  return action?.id
}
export function ActionEvent({
  execution,
  type,
}: {
  execution: WorkflowExecutionReadCompact
  type: "input" | "result"
}) {
  const {
    workflowId,
    selectedNodeEventId,
    setSelectedNodeEventId,
    workspaceId,
  } = useWorkflowBuilder()
  const { workflow } = useWorkflow()
  const selectedNodeEventRef = useMemo(() => {
    return selectedNodeEventId
      ? slugify(workflow?.actions[selectedNodeEventId]?.title || "")
      : undefined
  }, [selectedNodeEventId, workflow])

  if (!workflowId)
    return <AlertNotification level="error" message="No workflow in context" />

  return (
    <div className="flex flex-col gap-4 p-4">
      <Select
        value={selectedNodeEventRef}
        onValueChange={(actionRef: string | undefined) => {
          if (!actionRef) {
            setSelectedNodeEventId(undefined)
          } else {
            const id = ref2id(actionRef, workflow)
            if (id) {
              setSelectedNodeEventId(id)
            }
          }
        }}
      >
        <SelectTrigger className="h-8 text-xs text-foreground/70 focus:ring-0 focus:ring-offset-0">
          <SelectValue placeholder="Select an event" />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            {execution.events.map((event) => (
              <SelectItem
                key={event.action_ref}
                value={event.action_ref}
                className="max-h-8 py-1 text-xs"
              >
                {event.action_ref}
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>
      <div>
        {selectedNodeEventId && (
          <ActionEventDetails
            actionId={selectedNodeEventId}
            workflowId={workflowId}
            workspaceId={workspaceId}
            status={execution.status}
            events={execution.events}
            type={type}
          />
        )}
      </div>
    </div>
  )
}
export function ActionEventDetails({
  actionId,
  workflowId,
  workspaceId,
  status,
  events,
  type,
}: {
  actionId: string
  workflowId: string
  workspaceId: string
  status: WorkflowExecutionReadCompact["status"]
  events: WorkflowExecutionEventCompact[]
  type: "input" | "result"
}) {
  const { action, actionIsLoading, actionError } = useAction(
    actionId,
    workspaceId,
    workflowId!
  )
  // Filter only the events for this action
  if (actionIsLoading) return <CenteredSpinner />
  if (actionError || !action)
    return (
      <AlertNotification
        level="error"
        message={`Error loading action: ${actionError?.message || "Action undefined"}`}
      />
    )

  const actionRef = slugify(action.title)
  const actionEventsForRef = events.filter((e) => e.action_ref === actionRef)
  // No events for ref, either the action has not executed or there was no event for the action.
  if (actionEventsForRef.length === 0) {
    return (
      <div className="flex items-center justify-center gap-2 p-4 text-xs text-muted-foreground">
        {status === "RUNNING" ? (
          <>
            <LoaderIcon className="size-3 animate-spin text-muted-foreground" />
            <span>Waiting for events...</span>
          </>
        ) : (
          <>
            <CircleDot className="size-3 text-muted-foreground" />
            <span>No events</span>
          </>
        )}
      </div>
    )
  }
  // More than 1, error
  if (actionEventsForRef.length > 1) {
    console.error(
      `More than 1 event for action ${action.title}: ${JSON.stringify(
        actionEventsForRef
      )}`
    )
  }
  const actionEvent = actionEventsForRef[0]
  if (["SCHEDULED", "STARTED"].includes(actionEvent.status)) {
    return (
      <div className="flex items-center justify-center gap-2 p-4 text-xs text-muted-foreground">
        <LoaderIcon className="size-3 animate-spin text-muted-foreground" />
        <span>Action is {actionEvent.status.toLowerCase()}...</span>
      </div>
    )
  }
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-start">
        <Badge variant="secondary" className="items-center gap-2">
          {getWorkflowEventIcon(actionEvent.status, "size-4")}
          <span className="text-xs font-semibold text-foreground/70">
            Action {actionEvent.status.toLowerCase()}
          </span>
        </Badge>
      </div>
      {type === "result" && actionEvent.action_error ? (
        <ErrorEvent failure={actionEvent.action_error} />
      ) : (
        <JsonViewWithControls
          src={
            type === "input"
              ? actionEvent.action_input
              : actionEvent.action_result
          }
          defaultExpanded={true}
          copyPrefix={`ACTIONS.${actionRef}.result`}
        />
      )}
    </div>
  )
}

function ErrorEvent({ failure }: { failure: EventFailure }) {
  return (
    <div className="flex flex-col space-y-8 text-xs">
      <CodeBlock title="Error Message">{failure.message}</CodeBlock>
    </div>
  )
}

function flattenObject(
  obj: Record<string, unknown> | unknown[],
  prefix = ""
): Record<string, unknown> {
  // Handle root level array
  if (Array.isArray(obj)) {
    return obj.reduce((acc: Record<string, unknown>, item, index) => {
      const arrayPath = `[${index}]`
      if (typeof item === "object" && item !== null) {
        Object.assign(
          acc,
          flattenObject(
            item as Record<string, unknown>,
            prefix ? `${prefix}${arrayPath}` : arrayPath
          )
        )
      } else {
        acc[prefix ? `${prefix}${arrayPath}` : arrayPath] = item
      }
      return acc
    }, {})
  }

  // Original object handling
  return Object.keys(obj).reduce((acc: Record<string, unknown>, k: string) => {
    const pre = prefix.length ? `${prefix}.` : ""

    if (typeof obj[k] === "object" && obj[k] !== null) {
      if (Array.isArray(obj[k])) {
        ;(obj[k] as unknown[]).forEach((item, index) => {
          const arrayPath = `${k}[${index}]`
          if (typeof item === "object" && item !== null) {
            Object.assign(
              acc,
              flattenObject(
                item as Record<string, unknown>,
                pre ? `${pre}${arrayPath}` : arrayPath
              )
            )
          } else {
            acc[pre ? `${pre}${arrayPath}` : arrayPath] = item
          }
        })
      } else {
        Object.assign(
          acc,
          flattenObject(
            obj[k] as Record<string, unknown>,
            pre ? `${pre}${k}` : k
          )
        )
      }
    } else {
      acc[pre ? `${pre}${k}` : k] = obj[k]
    }
    return acc
  }, {})
}

export function JsonViewWithControls({
  src,
  defaultExpanded = false,
  copyPrefix,
}: {
  src: unknown
  defaultExpanded?: boolean
  copyPrefix?: string
}): JSX.Element {
  const [isExpanded, setIsExpanded] = React.useState(defaultExpanded)

  // Function to flatten JSON object
  // Safely flatten the source if it's an object
  const isCollapsible = ["object", "array"].includes(typeof src)
  const flattenedSrc =
    typeof src === "object" && src !== null
      ? flattenObject(src as Record<string, unknown>)
      : src

  const tabItems = [
    { value: "flat", label: "Flat", src: flattenedSrc },
    { value: "nested", label: "Nested", src: src },
  ]
  return (
    <div className="w-full space-y-2 overflow-x-auto">
      <Tabs defaultValue="flat">
        {isCollapsible && (
          <div className="flex items-center justify-between gap-4">
            <div>
              <div className="flex items-center gap-2">
                <Switch
                  checked={isExpanded}
                  onCheckedChange={setIsExpanded}
                  className="data-[state=checked]:bg-muted-foreground"
                />
                <p className="text-xs text-foreground/70">
                  {isExpanded ? "Collapse" : "Expand"}
                </p>
              </div>
            </div>
            <TabsList className="h-7 text-xs">
              {tabItems.map(({ value, label }) => (
                <TabsTrigger key={value} value={value} className="text-xs">
                  {label}
                </TabsTrigger>
              ))}
            </TabsList>
          </div>
        )}
        {tabItems.map(({ value, src: source }) => (
          <TabsContent
            key={value}
            value={value}
            className="rounded-md border bg-muted-foreground/5 p-4"
          >
            <JsonView
              collapsed={!isExpanded}
              displaySize
              enableClipboard
              src={source}
              className="w-full overflow-x-scroll text-wrap text-xs"
              theme="atom"
              CopyComponent={({ onClick, className }) => (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <CopyIcon
                      className={cn(
                        "m-0 size-3 p-0 text-muted-foreground",
                        className
                      )}
                      onClick={onClick}
                    />
                  </TooltipTrigger>
                  <TooltipContent>Copy JSONPath</TooltipContent>
                </Tooltip>
              )}
              CopiedComponent={({ className, style }) => (
                <CheckCheckIcon
                  className={cn("text-muted-foreground", className)}
                  style={style}
                />
              )}
              customizeCopy={(
                node: unknown,
                nodeMeta: NodeMeta | undefined
              ) => {
                const { currentPath } = nodeMeta || {}
                const copyValue = buildJsonPath(currentPath || [], copyPrefix)

                toast({
                  title: "Copied JSONPath to clipboard",
                  description: (
                    <Badge
                      variant="secondary"
                      className="bg-muted-foreground/10 font-mono text-xs font-normal tracking-tight"
                    >
                      {copyValue}
                    </Badge>
                  ),
                })
                return copyValue
              }}
            />
          </TabsContent>
        ))}
      </Tabs>
    </div>
  )
}

function isNumeric(str: string): boolean {
  return /^\d+$/.test(str)
}
function buildJsonPath(path: string[], prefix?: string): string | undefined {
  // Combine the arrays
  if (path.length === 0 && !prefix) {
    return undefined
  }
  const allSegments = []
  if (prefix) {
    allSegments.push(prefix)
  }
  if (path.length > 0) {
    allSegments.push(...path)
  }
  return allSegments.reduce((path, segment, index) => {
    // Convert segment to string for type safety
    const currentSegment = String(segment)

    // Handle different cases
    if (isNumeric(currentSegment)) {
      // For numeric segments, use bracket notation
      return `${path}[${currentSegment}]`
    } else if (currentSegment.startsWith("[")) {
      // For array segments, use bracket notation
      return `${path}${currentSegment}`
    } else {
      // For string segments, use dot notation unless it's the first segment
      return index === 0 ? currentSegment : `${path}.${currentSegment}`
    }
  }, "")
}
