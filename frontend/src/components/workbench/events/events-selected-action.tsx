"use client"

import React from "react"
import {
  WorkflowExecutionEventCompact,
  WorkflowExecutionReadCompact,
} from "@/client"
import { useWorkflowBuilder } from "@/providers/builder"
import { CircleDot, LoaderIcon } from "lucide-react"
import JsonView from "react18-json-view"

import { useAction } from "@/lib/hooks"
import { slugify } from "@/lib/utils"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"

export function ActionEvent({
  execution,
  type,
}: {
  execution: WorkflowExecutionReadCompact
  type: "input" | "result"
}) {
  const { workflowId, selectedNodeId, getNode, workspaceId } =
    useWorkflowBuilder()
  const node = getNode(selectedNodeId ?? "")
  if (!workflowId)
    return <AlertNotification level="error" message="No workflow in context" />
  if (selectedNodeId && !node) {
    return (
      <AlertNotification
        level="error"
        message={`Node ${selectedNodeId} not found`}
      />
    )
  }
  if (!node) {
    return (
      <div className="flex items-center justify-center p-4 text-xs text-muted-foreground">
        No action node selected.
      </div>
    )
  }
  if (node.type !== "udf") {
    const capitalizedType = node.type
      ? node.type[0].toUpperCase() + node.type.slice(1)
      : "Unknown"
    return (
      <div className="flex flex-col  items-center justify-center gap-2 p-4 text-xs text-muted-foreground">
        <span>{capitalizedType} node does not support viewing events.</span>
        <span>Please select an action node instead.</span>
      </div>
    )
  }
  return (
    <ActionEventDetails
      actionId={node.id}
      workflowId={workflowId}
      workspaceId={workspaceId}
      status={execution.status}
      events={execution.events}
      type={type}
    />
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
  return (
    <div className="space-y-4">
      <div className="space-y-2 p-4">
        <JsonViewWithControls
          src={
            type === "input"
              ? actionEvent.action_input
              : actionEvent.action_result
          }
          defaultExpanded={true}
        />
      </div>
    </div>
  )
}

function flattenObject(
  obj: Record<string, unknown>,
  prefix = ""
): Record<string, unknown> {
  return Object.keys(obj).reduce((acc: Record<string, unknown>, k: string) => {
    const pre = prefix.length ? prefix + "." : ""
    if (typeof obj[k] === "object" && obj[k] !== null) {
      if (Array.isArray(obj[k])) {
        // Handle arrays by flattening each element with index
        ;(obj[k] as unknown[]).forEach((item, index) => {
          if (typeof item === "object" && item !== null) {
            Object.assign(
              acc,
              flattenObject(
                item as Record<string, unknown>,
                `${pre}${k}[${index}]`
              )
            )
          } else {
            acc[`${pre}${k}[${index}]`] = item
          }
        })
      } else {
        // Handle nested objects
        Object.assign(
          acc,
          flattenObject(obj[k] as Record<string, unknown>, pre + k)
        )
      }
    } else {
      acc[pre + k] = obj[k]
    }
    return acc
  }, {})
}

export function JsonViewWithControls({
  src,
  title = "JSON",
  defaultExpanded = false,
}: {
  src: unknown
  title?: string
  defaultExpanded?: boolean
}): JSX.Element {
  const [isExpanded, setIsExpanded] = React.useState(defaultExpanded)

  // Function to flatten JSON object
  // Safely flatten the source if it's an object
  const flattenedSrc =
    typeof src === "object" && src !== null
      ? flattenObject(src as Record<string, unknown>)
      : src

  console.log(flattenedSrc)
  console.log(src)
  return (
    <div className="space-y-2">
      <div className="flex w-full items-center gap-4">
        <span className="text-xs font-semibold text-foreground/50">
          {title}
        </span>
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
      <Tabs defaultValue="flat">
        <TabsList className="h-7 text-xs">
          <TabsTrigger value="flat" className="text-xs">
            Flat
          </TabsTrigger>
          <TabsTrigger value="nested" className="text-xs">
            Nested
          </TabsTrigger>
        </TabsList>

        <TabsContent
          value="flat"
          className="rounded-md border bg-muted-foreground/5 p-4"
        >
          <JsonView
            collapsed={!isExpanded}
            displaySize
            enableClipboard
            src={flattenedSrc}
            className="text-sm"
            theme="atom"
            customizeCopy={(node) => {
              console.log("COPY", node)
              return node
            }}
          />
        </TabsContent>
        <TabsContent
          value="nested"
          className="rounded-md border bg-muted-foreground/5 p-4"
        >
          <JsonView
            collapsed={!isExpanded}
            displaySize
            enableClipboard
            src={src}
            className="text-sm"
            theme="atom"
          />
        </TabsContent>
      </Tabs>
    </div>
  )
}
