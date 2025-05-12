"use client"

import React from "react"
import {
  EventFailure,
  InteractionState,
  WorkflowExecutionEventCompact,
  WorkflowExecutionReadCompact,
} from "@/client"
import { useWorkflowBuilder } from "@/providers/builder"
import { CircleDot, LoaderIcon } from "lucide-react"

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
      Object.values(execution.interaction_states ?? {}).map((s) => s.action_ref)
    )
    events = events.filter((e) => interactionEvents.has(e.action_ref))
  }
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
            {events.map((event) => (
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
    const interactionState = Object.values(
      execution.interaction_states ?? {}
    ).find((s) => s.action_ref === selectedRef)
    if (!interactionState) {
      // We reach this if we switch tabs or select an event that has no interaction state
      return noEvent
    }
    return (
      <ActionInteractionEventDetails
        eventRef={selectedRef}
        interactionState={interactionState}
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
  interactionState,
}: {
  eventRef: string
  interactionState: InteractionState
}) {
  if (interactionState.data === undefined) {
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
        src={interactionState.data}
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
  // More than 1, error
  if (actionEventsForRef.length > 1) {
    console.error(
      `More than 1 event for action reference ${eventRef}: ${JSON.stringify(
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
          copyPrefix={`ACTIONS.${eventRef}.result`}
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
