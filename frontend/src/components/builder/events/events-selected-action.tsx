"use client"

import React from "react"
import { EventFailure, InteractionRead } from "@/client"
import { useWorkflowBuilder } from "@/providers/builder"
import { ChevronRightIcon, CircleDot, LoaderIcon } from "lucide-react"

import {
  groupEventsByActionRef,
  parseStreamId,
  WorkflowExecutionEventCompact,
  WorkflowExecutionReadCompact,
} from "@/lib/event-history"
import { Badge } from "@/components/ui/badge"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { getWorkflowEventIcon } from "@/components/builder/events/events-workflow"
import { CodeBlock } from "@/components/code-block"
import { JsonViewWithControls } from "@/components/json-viewer"
import { AlertNotification } from "@/components/notifications"
import { InlineDotSeparator } from "@/components/separator"

export function ActionEvent({
  execution,
  type,
}: {
  execution: WorkflowExecutionReadCompact
  type: "input" | "result" | "interaction"
}) {
  const { workflowId, selectedActionEventRef, setSelectedActionEventRef } =
    useWorkflowBuilder()

  if (!workflowId)
    return <AlertNotification level="error" message="No workflow in context" />

  let events = execution.events
  if (type === "interaction") {
    // Filter events to only include interaction events
    const interactionEvents = new Set(
      execution.interactions?.map((s) => s.action_ref) ?? []
    )
    events = events.filter((e) => interactionEvents.has(e.action_ref))
  }
  const groupedEvents = groupEventsByActionRef(events)
  return (
    <div className="flex flex-col gap-4 p-4">
      <Select
        value={selectedActionEventRef}
        onValueChange={setSelectedActionEventRef}
      >
        <SelectTrigger className="h-8 text-xs text-foreground/70 focus:ring-0 focus:ring-offset-0">
          <SelectValue placeholder="Select an event" />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            {Object.entries(groupedEvents).map(([actionRef, relatedEvents]) => (
              <SelectItem
                key={actionRef}
                value={actionRef}
                className="max-h-8 py-1 text-xs"
              >
                {actionRef}
                {relatedEvents.length !== 1 && ` (${relatedEvents.length})`}
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>

      <ActionEventView
        selectedRef={selectedActionEventRef}
        execution={execution}
        type={type}
      />
    </div>
  )
}
function ActionEventView({
  selectedRef,
  execution,
  type,
}: {
  selectedRef?: string
  execution: WorkflowExecutionReadCompact
  type: "input" | "result" | "interaction"
}) {
  const noEvent = (
    <div className="flex items-center justify-center gap-2 p-4 text-xs text-muted-foreground">
      <CircleDot className="size-3 text-muted-foreground" />
      <span>Please select an event</span>
    </div>
  )
  if (!selectedRef) {
    return noEvent
  }
  if (type === "interaction") {
    const interaction = execution.interactions?.find(
      (s) => s.action_ref === selectedRef
    )
    if (!interaction) {
      // We reach this if we switch tabs or select an event that has no interaction state
      return noEvent
    }
    return (
      <ActionInteractionEventDetails
        eventRef={selectedRef}
        interaction={interaction}
      />
    )
  }
  return (
    <ActionEventDetails
      eventRef={selectedRef}
      status={execution.status}
      events={execution.events}
      type={type}
    />
  )
}

function ActionInteractionEventDetails({
  eventRef,
  interaction,
}: {
  eventRef: string
  interaction: InteractionRead
}) {
  if (interaction.response_payload === null) {
    return (
      <div className="flex items-center justify-center gap-2 p-4 text-xs text-muted-foreground">
        <CircleDot className="size-3 text-muted-foreground" />
        <span>No interaction data</span>
      </div>
    )
  }
  return (
    <div className="flex flex-col gap-4">
      <JsonViewWithControls
        src={interaction.response_payload}
        defaultExpanded={true}
        copyPrefix={`ACTIONS.${eventRef}.interaction`}
      />
    </div>
  )
}

export function ActionEventDetails({
  eventRef,
  status,
  events,
  type,
}: {
  eventRef: string
  status: WorkflowExecutionReadCompact["status"]
  events: WorkflowExecutionEventCompact[]
  type: "input" | "result"
}) {
  const actionEventsForRef = events.filter((e) => e.action_ref === eventRef)
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
  const renderEvent = (
    actionEvent: WorkflowExecutionEventCompact,
    streamIdPlaceholder?: string
  ) => {
    if (["SCHEDULED", "STARTED"].includes(actionEvent.status)) {
      return (
        <div className="flex items-center justify-center gap-2 p-4 text-xs text-muted-foreground">
          <LoaderIcon className="size-3 animate-spin text-muted-foreground" />
          <span>Action is {actionEvent.status.toLowerCase()}...</span>
        </div>
      )
    }
    return (
      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <Badge variant="secondary" className="items-center gap-2">
            {getWorkflowEventIcon(actionEvent.status, "size-4")}
            <span className="text-xs font-semibold text-foreground/70">
              Action {actionEvent.status.toLowerCase()}
            </span>
          </Badge>
          {actionEvent.stream_id && !streamIdPlaceholder && (
            <div className="flex items-center gap-1 text-xs text-muted-foreground/80">
              {parseStreamId(actionEvent.stream_id)
                .filter((part) => part.scope !== "<root>")
                // Only sort if scope matches, otherwise preserve original order
                .sort((a, b) => {
                  if (a.scope === b.scope) {
                    return Number(a.index) - Number(b.index)
                  }
                  // If scopes do not match, preserve original order (no sorting)
                  return 0
                })
                // Insert a ">" separator between mapped elements, but not after the last one
                .map((part, idx, arr) => (
                  <div key={part.scope} className="flex items-center gap-1">
                    <span className="flex items-center gap-1">
                      <span>{part.scope}</span>
                      <InlineDotSeparator />
                      <span>{part.index}</span>
                    </span>
                    {idx < arr.length - 1 && (
                      <ChevronRightIcon className="size-3" />
                    )}
                  </div>
                ))}
            </div>
          )}
          {streamIdPlaceholder && (
            <div className="flex items-center gap-1 text-xs text-muted-foreground/80">
              <span>{streamIdPlaceholder}</span>
            </div>
          )}
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
            copyPrefix={`ACTIONS.${eventRef}.result`}
          />
        )}
      </div>
    )
  }
  if (type === "input") {
    // Inputs are identical for all events, so we can just render the first one
    return renderEvent(
      actionEventsForRef[0],
      "Input is the same for all events"
    )
  }
  return actionEventsForRef.map((actionEvent) => (
    <div key={actionEvent.stream_id}>{renderEvent(actionEvent)}</div>
  ))
}

function ErrorEvent({ failure }: { failure: EventFailure }) {
  return (
    <div className="flex flex-col space-y-8 text-xs">
      <CodeBlock title="Error Message">{failure.message}</CodeBlock>
    </div>
  )
}
